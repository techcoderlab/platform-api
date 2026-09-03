"""
Shared extraction utilities for the web-scraper module.

Design goals, in priority order:
1. Realism    – mailto:/tel: harvesting, share-button filtering, search/login/
                comment-form exclusion, junk email/phone filtering, paired-paren
                phone grammar, deterministic ordered output.
2. Robustness – public methods never raise; None/bytes/malformed input degrade
                to empty results; every step is wrapped.
3. Speed      – ONE BeautifulSoup parse per page, shared across DataExtractor,
                FormExtractor and CTAExtractor via a thread-local identity
                cache; lxml when available (10-30x faster than html.parser).
"""

from __future__ import annotations

import html as html_utils
import logging
import re
import threading
from typing import Any
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Parser selection & shared parse cache                                       #
# --------------------------------------------------------------------------- #
try:
    import lxml  # noqa: F401
    _BEST_PARSER = "lxml"
except ImportError:
    _BEST_PARSER = "html.parser"

_thread_local = threading.local()


def _as_html(value: Any) -> str:
    """Coerce arbitrary scraper output into a str. Never raises."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:
            return ""
    if isinstance(value, str):
        return value
    return ""


def _cached_soup(html_content: str) -> BeautifulSoup:
    """
    Parse once per page per thread; shared by all extractor classes.
    Requires the SAME str object to be passed around (the orchestrator does).
    The returned soup has script/style/template/noscript removed once, up
    front — treat it as read-only afterwards.
    """
    cache = getattr(_thread_local, "soup_cache", None)
    if cache is not None and cache[0] is html_content:
        return cache[1]
    soup = BeautifulSoup(html_content, _BEST_PARSER)
    for element in soup(["script", "style", "template", "noscript"]):
        element.decompose()
    _thread_local.soup_cache = (html_content, soup)
    return soup


def _cached_text(html_content: str) -> str:
    """Whitespace-collapsed visible text, computed once per page per thread."""
    cache = getattr(_thread_local, "text_cache", None)
    if cache is not None and cache[0] is html_content:
        return cache[1]
    text = re.sub(r"\s+", " ", _cached_soup(html_content).get_text(separator=" ")).strip()
    _thread_local.text_cache = (html_content, text)
    return text

def safe_cached_text(html_content: str) -> str:
    try:
        return _cached_text(html_content)
    except Exception as exc:
        logger.debug("extractor_utils._cached_text failed: %s", exc)
        return ""


class DataExtractor:
    """Email + phone extraction from page text and mailto:/tel: hrefs, with junk filtering."""

    _EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}\b")
    # Highest-precision sources: href attributes, no text-scrape noise at all.
    _MAILTO_RE = re.compile(r"mailto:\s*([^\s<>\"'?]+)", re.I)
    _TEL_RE = re.compile(r"tel:([+()\d.\s\-]{7,25})", re.I)

    # Paired parens (no more '555) 123'), currency-aware lookbehind (no more
    # '$ 1 250 000' prices), and no digit-run slices via (?!\d).
    _PHONE_RE = re.compile(
        r"""
        (?<![\w@#\$€£₹])              # not inside an email, price, ID or longer token
        (?:\+\d{1,3}[\s.\-]?)?        # optional +country code
        (?:\(\d{2,5}\)|\d{2,5})       # main block — parens must be paired
        (?:[\s.\-]?\d{2,4}){1,3}      # remaining groups, separators optional
        (?!\d)                        # not a slice of a longer digit run
        """,
        re.VERBOSE,
    )

    # Junk filters — regex scrapers' classic false positives.
    _ASSET_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css",
                   ".js", ".json", ".woff", ".woff2", ".ttf", ".ico", ".mp4", ".pdf")
    _JUNK_LOCALS = {"test", "example", "user", "username", "name", "email",
                    "youremail", "your-email", "yourname", "johndoe", "janedoe",
                    "john.doe", "jane.doe", "johnsmith", "abc", "abcd", "sample",
                    "someone", "first.last", "your_email", "mail"}
    _JUNK_DOMAINS = (".example", "example.com", "example.org", "example.net",
                     "domain.com", "email.com", "yoursite.com", "yourdomain.com",
                     "website.com", "sample.com", "test.com", "sentry.io",
                     "wixpress.com", "acme.com", "company.com", "address.com")

    # "2023-2024" year ranges and "12.03.2024" / "2024-03-12" dates.
    _YEAR_RANGE_RE = re.compile(r"^(?:19|20)\d{2}\s*[-–—/.]?\s*(?:19|20)\d{2}$")
    _DATE_LIKE_RE = re.compile(
        r"^(?:\d{1,2}[./\-]\d{1,2}[./\-](?:19|20)\d{2}|(?:19|20)\d{2}[./\-]\d{1,2}[./\-]\d{1,2})$"
    )
    _DEGENERATE_PHONES = {"1234567", "12345678", "123456789", "1234567890",
                          "0123456789", "9876543210", "01234567890", "12345678901"}
    _CURRENCY_CHARS = ("$", "€", "£", "¥", "₹")

    @classmethod
    def find_emails(cls, html_content: Any) -> list[str]:
        try:
            html = _as_html(html_content)
            if not html:
                return []
            raw = html_utils.unescape(html)  # also de-obfuscates &#64; entities
            candidates = [m.group(1) for m in cls._MAILTO_RE.finditer(raw)]
            candidates.extend(m.group(0) for m in cls._EMAIL_RE.finditer(_cached_text(html)))

            found: list[str] = []
            seen: set[str] = set()
            for candidate in candidates:
                email = unquote(candidate).strip().strip(".<>,").lower()
                if not email or email in seen:
                    continue
                local, _, domain = email.partition("@")
                if not local or "." not in domain or len(domain) < 4:
                    continue
                if email.endswith(cls._ASSET_EXTS):        # logo@2x.png artifacts
                    continue
                if domain.endswith(cls._JUNK_DOMAINS):
                    continue
                if local in cls._JUNK_LOCALS:
                    continue
                seen.add(email)
                found.append(email)
                if len(found) >= 15:
                    break
            return found
        except Exception as exc:
            logger.warning("DataExtractor.find_emails failed: %s", exc)
            return []

    @classmethod
    def find_contacts(cls, html_content: Any) -> list[str]:
        try:
            html = _as_html(html_content)
            if not html:
                return []
            raw = html_utils.unescape(html)
            candidates = [m.group(1) for m in cls._TEL_RE.finditer(raw)]

            text = _cached_text(html)
            for m in cls._PHONE_RE.finditer(text):
                prefix = text[max(0, m.start() - 2):m.start()]
                if any(sym in prefix for sym in cls._CURRENCY_CHARS):
                    continue  # "$ 1 250 000" style price, not a phone
                candidates.append(m.group(0))

            found: list[str] = []
            seen: set[str] = set()
            for candidate in candidates:
                phone = cls._normalize_phone(candidate)
                if phone is None:
                    continue
                digits = re.sub(r"\D", "", phone)
                key = digits[-10:] if len(digits) > 10 else digits
                if key in seen:
                    continue
                seen.add(key)
                found.append(phone)
                if len(found) >= 10:
                    break
            return found
        except Exception as exc:
            logger.warning("DataExtractor.find_contacts failed: %s", exc)
            return []

    @classmethod
    def _normalize_phone(cls, candidate: str) -> str | None:
        phone = candidate.strip().strip(" .,-()[]")
        digits = re.sub(r"\D", "", phone)
        if not (7 <= len(digits) <= 15):
            return None
        if cls._YEAR_RANGE_RE.fullmatch(phone):    # "2023-2024"
            return None
        if cls._DATE_LIKE_RE.fullmatch(phone):     # "12.03.2024" / "2024-03-12"
            return None
        if len(set(digits)) == 1 or digits in cls._DEGENERATE_PHONES:
            return None
        return phone


class LinkExtractor:
    """
    Social profile links.

    Platform/builder domains (shopify.com, wix.com, wordpress.com,
    squarespace.com) were deliberately removed: linking out to a website
    builder is NOT social presence, and counting it corrupted the
    'missing_social_presence' pitch hook. Share/intent endpoints (share
    buttons) are excluded too — a 'tweet this' link is not the company's
    profile. Ports (":443") and userinfo ("user@host") are now stripped.
    """

    _SOCIAL_DOMAINS = frozenset({
        "facebook.com", "fb.com", "m.me", "twitter.com", "x.com", "linkedin.com",
        "instagram.com", "youtube.com", "youtu.be", "tiktok.com", "snapchat.com",
        "telegram.me", "t.me", "whatsapp.com", "wa.me", "pinterest.com",
        "github.com", "discord.gg", "discord.com", "twitch.tv", "reddit.com",
        "medium.com", "vimeo.com", "soundcloud.com", "behance.net",
        "dribbble.com", "tumblr.com", "vk.com", "ok.ru", "threads.net",
        "bsky.app", "nextdoor.com", "flickr.com", "line.me",
    })
    _SUFFIXES = tuple("." + d for d in _SOCIAL_DOMAINS)  # single C-level endswith()
    _SHARE_PATH_RE = re.compile(
        r"(?:^|/)(?:sharer?|intent|share|sharing|pin/create|dialog/(?:share|feed))(?:/|$|\?)",
        re.I,
    )

    @classmethod
    def find_social_links(cls, links: Any) -> list[str]:
        if not links:
            return []
        found: list[str] = []
        seen: set[str] = set()
        for link in links:
            if not isinstance(link, str):
                continue
            link = link.strip()
            if not link:
                continue
            try:
                parsed = urlparse(link)
            except ValueError:
                continue
            netloc = parsed.netloc.lower()
            if not netloc:
                continue
            if "@" in netloc:                        # user:pass@host
                netloc = netloc.rsplit("@", 1)[-1]
            netloc = netloc.split(":", 1)[0]         # strip port
            if netloc.startswith("www."):
                netloc = netloc[4:]
            if netloc not in cls._SOCIAL_DOMAINS and not netloc.endswith(cls._SUFFIXES):
                continue
            if cls._SHARE_PATH_RE.search(parsed.path):
                continue                             # share button, not a profile
            cleaned = link.rstrip("/")
            key = cleaned.casefold()
            if key in seen:
                continue
            seen.add(key)
            found.append(cleaned)
            if len(found) >= 25:
                break
        return found                                 # input order = deterministic


class FormExtractor:
    """
    Structural form extraction. Never claims a form is broken/working — only
    reports fields and whether it looks like lead capture.

    Noise exclusion (all previously polluted the 'forms' list, making the
    'missing_lead_capture_forms' hook wrong):
      - search forms (role=search, /search action, single q/s field)
      - login forms (password field, unless a real contact field is present)
      - blog comment forms (name+email+comment — look exactly like contact forms)
      - honeypot / display:none anti-spam fields

    Forms with zero identified labels are now KEPT (previously dropped, which
    hid real lead forms from the hook logic). Labels are resolved per-form via
    wrapping <label>, label[for=id] and aria-labelledby — the old
    find_previous("label") could steal a label from an unrelated earlier field.
    """

    _LEAD_FIELD_HINTS = {
        "name", "email", "phone", "address", "message", "company", "organisation",
        "organization", "enquiry", "inquiry", "subject", "question", "comment",
        "budget", "service",
    }
    _SEARCH_FIELD_NAMES = {"q", "s", "search", "query", "keyword", "keywords",
                           "searchterm", "search_term", "search_query", "txtsearch"}
    _HONEYPOT_RE = re.compile(r"honeypot|bot-?field|trap|(^|[_-])hp([_-]|$)", re.I)
    _SEARCHY_ATTR_RE = re.compile(r"\bsearch\b", re.I)
    _SKIP_TYPES = ("hidden", "submit", "button", "image", "reset")

    @classmethod
    def extract_forms(cls, html_content: Any) -> list[dict]:
        try:
            html = _as_html(html_content)
            if not html:
                return []
            soup = _cached_soup(html)
            forms_data: list[dict] = []
            for form in soup.find_all("form"):
                if cls._is_noise_form(form):
                    continue
                fields: list[str] = []
                lead_by_type = False
                for field in form.find_all(["input", "textarea", "select"]):
                    ftype = (field.get("type") or "text").strip().lower()
                    if ftype in cls._SKIP_TYPES:
                        continue
                    if ftype in ("email", "tel"):
                        lead_by_type = True
                    name = (field.get("name") or "").strip()
                    if cls._HONEYPOT_RE.search(name):
                        continue
                    style = (field.get("style") or "").replace(" ", "").lower()
                    if "display:none" in style or "visibility:hidden" in style:
                        continue
                    label = cls._field_label(field, form)
                    if label:
                        fields.append(label[:60].lower())

                forms_data.append({
                    "fields": fields,
                    "field_count": len(fields),
                    "likely_lead_capture": lead_by_type or any(
                        any(hint in f for hint in cls._LEAD_FIELD_HINTS) for f in fields
                    ),
                })
                if len(forms_data) >= 20:
                    break
            return forms_data
        except Exception as exc:
            logger.warning("FormExtractor.extract_forms failed: %s", exc)
            return []

    @classmethod
    def _is_noise_form(cls, form) -> bool:
        if form.get("role") == "search":
            return True
        class_attr = form.get("class")
        class_str = " ".join(class_attr) if isinstance(class_attr, list) else (class_attr or "")
        attrs = " ".join(filter(None, [
            form.get("id") or "", class_str,
            form.get("action") or "", form.get("name") or "",
        ]))
        if cls._SEARCHY_ATTR_RE.search(attrs) or "comment" in attrs.lower():
            return True

        inputs = form.find_all("input")
        if any((i.get("type") or "").lower() == "password" for i in inputs):
            # Login/signup — exclude unless it also collects contact details
            # (SaaS signup forms with a company field stay).
            names = [(i.get("name") or "").lower() for i in inputs]
            if not any(any(h in n for h in cls._LEAD_FIELD_HINTS) for n in names):
                return True

        visible = [i for i in inputs
                   if (i.get("type") or "text").lower() not in cls._SKIP_TYPES]
        if len(visible) == 1 and (visible[0].get("name") or "").strip().lower() in cls._SEARCH_FIELD_NAMES:
            return True  # bare header search box
        return False

    @classmethod
    def _field_label(cls, field, form) -> str:
        label = (field.get("aria-label") or "").strip()
        if not label:
            parent = field.find_parent("label")          # <label>Name <input></label>
            if parent:
                label = parent.get_text(" ", strip=True)
        if not label:
            field_id = field.get("id")
            if field_id:
                label_tag = form.find("label", attrs={"for": field_id})  # form-scoped, not document-wide
                if label_tag:
                    label = label_tag.get_text(" ", strip=True)
        if not label:
            ref_ids = (field.get("aria-labelledby") or "").split()
            if ref_ids:
                ref = form.find(id=ref_ids[0])
                if ref:
                    label = ref.get_text(" ", strip=True)
        if not label:
            label = (field.get("placeholder") or "").strip()
        if not label:
            label = (field.get("name") or "").strip()
        if not label:
            label = (field.get("id") or "").strip()
        return label.strip("*:·•-— \t")


class CTAExtractor:
    """
    Call-to-action text from <a>, <button>, [role=button] AND
    <input type=submit|button> (previously missed entirely — form submit
    buttons like 'Get Free Quote' are prime CTA signals). Icon-only buttons
    now fall back to aria-label/value/title. Output is DOM-ordered and
    deterministic (set() scrambled it before).
    """

    _ACTION_WORDS = frozenset({
        "get", "request", "book", "download", "contact", "call", "quote",
        "start", "try", "buy", "shop", "sign", "join", "learn", "schedule",
        "talk", "demo", "explore", "view", "subscribe", "apply", "connect",
        "claim", "unlock", "save", "order", "reserve", "register", "enroll",
        "enquire", "inquire", "hire", "find", "check", "calculate", "compare",
        "refer", "donate", "support", "help", "email", "message", "chat",
        "text", "meet", "grow", "build", "create", "launch", "discover",
        "see", "free", "lets", "let's", "ask", "estimate", "access", "grab",
    })
    _MAX_CTA_WORDS = 7
    _MAX_CTAS = 20
    # Token-boundary match: hits 'btn-primary', 'link-button', 'cta-hero';
    # skips false stems like 'buttondown'.
    _CTA_CLASS_RE = re.compile(r"(?:^|[\s_\-])(?:btn|button|cta)(?:$|[\s_\-])", re.I)
    _WS_RE = re.compile(r"\s+")
    _PUNCT = ".,!?:;—–()[]\"'“”’«»"

    @classmethod
    def extract_ctas(cls, html_content: Any) -> list[str]:
        try:
            html = _as_html(html_content)
            if not html:
                return []
            soup = _cached_soup(html)
            tags = soup.find_all(["a", "button"])
            tags.extend(soup.find_all(attrs={"role": "button"}))
            tags.extend(soup.find_all("input", attrs={"type": ["submit", "button"]}))

            ordered: list[str] = []
            seen: set[str] = set()
            for tag in tags:
                text = cls._visible_text(tag)
                if not text:
                    continue
                normalized = cls._WS_RE.sub(" ", text).strip()
                words = normalized.split()
                if not words or len(words) > cls._MAX_CTA_WORDS:
                    continue
                first = words[0].lower().replace("\u2019", "'").strip(cls._PUNCT)
                class_attr = tag.get("class")
                class_str = " ".join(class_attr) if isinstance(class_attr, list) else (class_attr or "")
                if first not in cls._ACTION_WORDS and not cls._CTA_CLASS_RE.search(class_str):
                    continue
                key = normalized.casefold()
                if key in seen:
                    continue
                seen.add(key)
                ordered.append(normalized)
                if len(ordered) >= cls._MAX_CTAS:
                    break
            return ordered
        except Exception as exc:
            logger.warning("CTAExtractor.extract_ctas failed: %s", exc)
            return []

    @staticmethod
    def _visible_text(tag) -> str:
        text = tag.get_text(" ", strip=True)
        if not text:  # icon-only button
            text = (tag.get("aria-label") or tag.get("value") or tag.get("title") or "").strip()
        return text