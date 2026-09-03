"""
B2B pitch-signal extractor.

Design goals, in priority order:
1. Realism    – precise provider signatures (CDN hosts, script paths, DOM attrs)
                instead of loose brand-name matching; boilerplate-aware mission
                extraction; real copyright-year parsing instead of "year appears
                anywhere in the page".
2. Robustness – every step is isolated behind a safe wrapper; malformed/missing
                snapshot fields degrade to defaults; deterministic output.
3. Speed      – no BeautifulSoup in this module at all. One .lower() pass, then
                pre-compiled regexes + C-speed substring scans. ~1-3 ms/page
                vs. ~100-300 ms for a BS4 parse of a typical 300 KB page.
"""

from __future__ import annotations

import html as html_utils
import logging
import re
from datetime import datetime
from typing import Any, Callable, Iterable, TypeVar
from urllib.parse import urlparse

from app.modules.web_scraper.domain.models import PageSnapshot
from app.modules.web_scraper.application.extractor_utils import (
    CTAExtractor,
    DataExtractor,
    FormExtractor,
    LinkExtractor,
    safe_cached_text
)

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# Output caps — keeps LLM token cost bounded and defends against pathological pages.
_MAX_LINKS = 25
_MAX_SOCIAL = 20
_MAX_EMAILS = 15
_MAX_PHONES = 10
_MAX_FORMS = 20
_MAX_MISSION_CHARS = 320


class B2BPitchExtractor:
    """Decoupled module to extract tailored B2B growth signals for AI pitches."""

    # ------------------------------------------------------------------ #
    # Pre-compiled patterns — compiled once at import, never per call.   #
    # ------------------------------------------------------------------ #
    _META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.I)
    _META_CONTENT_RE = re.compile(r"""content\s*=\s*["']([^"']+)["']""", re.I)
    # name= is matched in any attribute order, any quote style, unquoted allowed
    _VIEWPORT_RE = re.compile(r"""<meta\b[^>]*name\s*=\s*["']?viewport["']?""", re.I)
    _H1_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1\s*>", re.I | re.S)
    _TAG_RE = re.compile(r"<[^>]+>")
    _WS_RE = re.compile(r"\s{2,}")
    _WORD_RE = re.compile(r"[A-Za-z]{3,}")

    _CANONICAL_RE = re.compile(
        r'<link\b(?=[^>]*\brel=["\']canonical["\'])(?=[^>]*\bhref=["\']([^"\']+)["\'])[^>]*>', re.I
    )
    _NOINDEX_RE = re.compile(
        r'<meta\b(?=[^>]*\bname=["\'](?:robots|googlebot)["\'])(?=[^>]*\bcontent=["\'][^"\']*noindex)[^>]*>', re.I
    )

    # Copyright: only years *adjacent to* a copyright marker count.
    _COPYRIGHT_MARK_RE = re.compile(r"(?:©|&copy;?|&#169;|\(c\)|\bcopyright\b)", re.I)
    _YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
    _DYNAMIC_YEAR_RE = re.compile(r"getfullyear")  # JS-injected footer year

    # On-page booking CTAs (site may have a booking funnel without a scheduler link)
    _BOOKING_CTA_RE = re.compile(
        r"\b(?:book\s+(?:now|online|an?\s+appointment|a\s+(?:call|demo|consult|table|room))"
        r"|schedule\s+(?:now|online|an?\s+appointment|a\s+(?:call|consult|demo))"
        r"|make\s+a\s+reservation"
        r"|reserve\s+(?:now|a\s+table))\b",
        re.I,
    )
    _CAL_COM_RE = re.compile(r"(?<![\w.-])cal\.com/", re.I)

    # Boilerplate filter for mission candidates — deliberately avoids generic
    # words like "cookie"/"privacy" so real businesses ("we bake cookies…",
    # "privacy-first CRM") aren't rejected.
    _JUNK_LINE_RE = re.compile(
        r"cookies?\s+(?:policy|consent|settings|notice)"
        r"|accept(?:ing)?\s+(?:all\s+)?cookies"
        r"|(?:we\s+use|this\s+(?:website|site)\s+uses?|by\s+(?:clicking|continuing|using))\s+(?:\w+\s+){0,2}cookies"
        r"|privacy\s+(?:policy|notice|center|centre)"
        r"|terms\s+(?:of\s+(?:use|service)|and\s+conditions)"
        r"|all\s+rights\s+reserved"
        r"|skip\s+to\s+(?:main\s+)?content"
        r"|subscribe|newsletter"
        r"|enable\s+javascript|javascript\s+is\s+(?:disabled|required)"
        r"|ad[\s-]?blocker"
        r"|just\s+another\s+wordpress"
        r"|proudly\s+powered\s+by"
        r"|copyright|©"
        r"|\bpage\s+not\s+found\b|\b40[34]\b",
        re.I,
    )
    _LEADING_NOISE_RE = re.compile(r"^(?:home|homepage|welcome(?:\s+to)?)\s*[|·–—-]\s*", re.I)

    # ------------------------------------------------------------------ #
    # Signature tables — ORDER MATTERS (most specific/reliable first).   #
    # Only infrastructure fingerprints, never bare brand names, so a     #
    # blog *mentioning* Shopify can't be misread as a Shopify store.     #
    # ------------------------------------------------------------------ #
    _GENERATOR_MAP: tuple[tuple[str, str], ...] = (
        ("wordpress", "WordPress"),
        ("woocommerce", "WordPress (WooCommerce)"),
        ("shopify", "Shopify"),
        ("wix", "Wix"),
        ("squarespace", "Squarespace"),
        ("webflow", "Webflow"),
        ("joomla", "Joomla"),
        ("drupal", "Drupal"),
        ("ghost", "Ghost"),
        ("duda", "Duda"),
        ("next.js", "Next.js"),
        ("nuxt", "Vue/Nuxt"),
        ("gatsby", "Gatsby"),
        ("hugo", "Hugo"),
        ("jekyll", "Jekyll"),
    )

    _PLATFORM_SIGNATURES: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("WordPress (WooCommerce)", ("woocommerce", "wc-ajax=")),
        ("WordPress", ("wp-content/", "wp-includes/", "/wp-json/", "wp-emoji")),
        ("Shopify", ("cdn.shopify.", "myshopify.com", "shopify.theme", "/cdn/shop/")),
        ("Wix", ("wixstatic.com", "parastorage.com", "wix-code", "wixsite.com")),
        ("Squarespace", ("squarespace-cdn", "static1.squarespace.com", "static.squarespace.com", "sqs-block")),
        ("Webflow", ("website-files.com", "data-wf-site", "data-wf-page", "w-webflow")),
        ("Next.js", ("__next_data__", "_next/static", "data-nextjs")),
        ("Vue/Nuxt", ("__nuxt__", "/_nuxt/", "data-nuxt")),
        ("React", ("data-reactroot", "react-dom.production", "react.production", "/static/js/main.")),
        ("Vue", ("data-v-app", "__vue__", "vue.runtime")),
        ("Drupal", ("drupal-settings-json", "/sites/default/files", "drupal.js")),
        ("Joomla", ("/media/jui/", "joomla!", "/media/system/js/")),
        ("Ghost", ("ghost-sdk", "ghost.min.js", "content_api_key")),
        ("Duda", ("duda.co", "dudaone")),
        ("GoDaddy Builder", ("img1.wsimg.com", "wsimg.com")),
        ("BigCommerce", ("cdn11.bigcommerce.com", "mybigcommerce.com", "bigcommerce")),
    )

    # Domains/host paths only — 'hs-scripts.com' (chat) is distinct from
    # 'hsforms.net' (forms), fixing the HubSpot false positive.
    _CHAT_SIGNATURES: tuple[str, ...] = (
        "intercom.io", "intercomcdn", "widget.intercom",
        "crisp.chat", "drift.com", "driftt.com",
        "tawk.to", "zdassets.com", "zopim", "zendesk",
        "livechatinc.com", "lc_chat", "tidio.co", "gorgias",
        "hs-scripts.com",
        "olark.com", "smartsupp", "chaport", "purechat",
        "freshchat",
        "fb-customerchat", "fb-customer-chat", "fb-chat-plugin",
        # WhatsApp click-to-chat — deliberate: it's the dominant SMB "chat" affordance.
        "wa.me", "api.whatsapp.com/send", "whatsapp://",
    )

    _BOOKING_SIGNATURES: tuple[str, ...] = (
        "calendly.com", "acuityscheduling.com", "simplybook", "setmore",
        "savvycal.com", "youcanbook.me", "appointlet", "oncehub",
        "scheduleonce", "10to8.com", "booksy.com", "vagaro.com",
        "squareup.com/appointments", "square.site/book",
        "appointment-scheduling.google.com",
        "outlook.office365.com/owa/calendar",  # MS Bookings
    )

    _REVIEW_SIGNATURES: tuple[str, ...] = (
        "yelp.com", "trustpilot.com", "tripadvisor.", "g.page",
        "google.com/maps", "search.google.com/local",
        "bbb.org", "birdeye.com", "podium.com",
        "g2.com", "capterra.com", "clutch.co", "producthunt.com",
        "angi.com", "opentable.",
    )

    # Email/phone junk filters — regex scrapers love these false positives.
    _ASSET_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css",
                   ".js", ".json", ".woff", ".woff2", ".ttf", ".ico", ".mp4")
    _JUNK_EMAIL_LOCALS = {
        "test", "example", "user", "username", "name", "email", "youremail",
        "your-email", "yourname", "johndoe", "janedoe", "john.doe", "jane.doe",
        "johnsmith", "abc", "abcd", "sample", "someone", "first.last",
    }
    _JUNK_EMAIL_DOMAINS = (".example", "example.com", "example.org", "example.net",
                           "domain.com", "email.com", "yoursite.com", "yourdomain.com",
                           "website.com", "sample.com", "test.com", "sentry.io",
                           "wixpress.com", "acme.com", "company.com", "address.com")
    _PHONE_PLACEHOLDERS = {"1234567890", "0123456789", "123456789", "0000000000",
                           "1111111111", "9999999999", "5555555555", "1231231234"}

    # ================================================================== #
    # Public API                                                         #
    # ================================================================== #
    @classmethod
    def extract(cls, snapshot: PageSnapshot) -> dict[str, Any]:
        """Orchestrates the extraction of all AI pitch metrics. Never raises."""
        # 0. Defensive field access — the pipeline must survive None/partial snapshots.
        html = getattr(snapshot, "html", None) or ""
        text = getattr(snapshot, "text", None) or ""
        meta = getattr(snapshot, "meta", None) or {}
        links = list(getattr(snapshot, "links", None) or [])
        title = getattr(snapshot, "title", None) or ""
        final_url = getattr(snapshot, "final_url", None) or ""

        # Single lowercasing pass, reused by every signal below.
        html_lower = html.lower()

        # 1. Base lead data (external extractors, each isolated + sanitized)
        forms = cls._safe("forms", lambda: cls._cap(FormExtractor.extract_forms(html), _MAX_FORMS), [])
        ctas = cls._safe("ctas", lambda: CTAExtractor.extract_ctas(html) or [], [])
        emails = cls._safe("emails", lambda: cls._clean_emails(DataExtractor.find_emails(html)), [])
        phones = cls._safe("phones", lambda: cls._clean_phones(DataExtractor.find_contacts(html)), [])
        social_links = cls._safe("social", lambda: cls._cap(LinkExtractor.find_social_links(links), _MAX_SOCIAL), [])

        # 2-5. Signals
        mission = cls._safe("mission", lambda: cls._extract_mission_statement(text, meta, html), "")
        platform = cls._safe("platform", lambda: cls._detect_platform(html_lower, meta), "Custom/Unknown")
        has_chat = cls._safe("chat", lambda: cls._detect_chat_widget(html_lower), False)
        booking_links = cls._safe("booking links", lambda: cls._detect_booking_links(links), [])
        booking_cta = bool(cls._safe("booking cta", lambda: cls._BOOKING_CTA_RE.search(html_lower), None))
        review_links = cls._safe("review links", lambda: cls._detect_review_links(links), [])
        hooks = cls._safe(
            "hooks",
            lambda: cls._extract_hooks(
                html_lower=html_lower,
                final_url=final_url,
                emails=emails,
                phones=phones,
                forms=forms,
                social_links=social_links,
                booking_links=booking_links,
                booking_cta=booking_cta,
                has_chat=has_chat,
            ),
            cls._default_hooks(),
        )


        visible_text = cls._safe("visible text", lambda: safe_cached_text(html), "")
        word_count = len(visible_text.split()) if visible_text else 0

        description = meta.get("description") or meta.get("og:description") or "" if isinstance(meta, dict) else ""

        h1_matches = cls._safe("h1", lambda: cls._H1_RE.findall(html), [])
        h1_count = len(h1_matches)

        canonical = cls._safe("canonical", lambda: (cls._CANONICAL_RE.search(html).group(1) if cls._CANONICAL_RE.search(html) else ""), "")
        noindex = bool(cls._safe("noindex", lambda: cls._NOINDEX_RE.search(html), None))
        

        return {
            "seo": {
                "title": title,
                "description": description,
                "canonical": canonical,
                "noindex": noindex,
                "platform": platform
            },
            "content": {
                "mission_statement": mission,
                "word_count": word_count,
                "h1_count": h1_count
            },
            "leads": {
                "emails": emails,
                "phones": phones,
                "social_links": social_links,
                "forms": forms,
                "ctas": ctas,
                "automation": {
                    "has_chat_widget": has_chat,
                    "booking_links": booking_links,
                    "booking_cta_present": booking_cta,  # additive
                },
                "reputation": {"review_links": review_links},
            },
            "pitch_hooks": hooks,
        }

    # ================================================================== #
    # Extraction steps                                                   #
    # ================================================================== #
    @classmethod
    def _extract_mission_statement(cls, text: str, meta: Any, html: str) -> str:
        """Best 'what this business does' statement, in priority order:
        meta description → og:description → first <h1> → first substantive text lines."""
        # 1) SEO descriptions are literally hand-written business summaries.
        for key in ("description", "og:description", "twitter:description"):
            candidate = cls._sanitize_statement(meta.get(key) if isinstance(meta, dict) else None)
            if candidate:
                return candidate

        # 2) First meaningful <h1> (brand positioning line).
        match = cls._H1_RE.search(html)
        if match:
            candidate = cls._sanitize_statement(match.group(1))
            if candidate:
                return candidate

        # 3) Fallback: first substantive lines of visible text (junk-filtered).
        picked: list[str] = []
        for raw_line in text.splitlines():
            line = cls._WS_RE.sub(" ", raw_line).strip()
            if not (30 <= len(line) <= 500):
                continue
            if cls._JUNK_LINE_RE.search(line):
                continue  # skips cookie banners, nav, login prompts…
            if len(cls._WORD_RE.findall(line)) < 3:
                continue
            picked.append(line)
            if len(picked) == 3:
                break
        return cls._truncate("\n".join(picked))

    @classmethod
    def _detect_platform(cls, html_lower: str, meta: Any) -> str:
        # 1) <meta name="generator"> — the purpose-built, most reliable signal.
        generator = ""
        if isinstance(meta, dict):
            generator = str(meta.get("generator") or meta.get("x-generator") or "").lower()
        if not generator:
            for tag in cls._META_TAG_RE.findall(html_lower):
                if "generator" in tag:
                    match = cls._META_CONTENT_RE.search(tag)
                    if match:
                        generator = match.group(1).lower()
                        break
        if generator:
            for needle, name in cls._GENERATOR_MAP:
                if needle in generator:
                    return name

        # 2) Infrastructure fingerprints (CDN hosts / DOM attrs / script paths).
        for name, signatures in cls._PLATFORM_SIGNATURES:
            if any(sig in html_lower for sig in signatures):
                return name
        return "Custom/Unknown"

    @classmethod
    def _detect_chat_widget(cls, html_lower: str) -> bool:
        return any(sig in html_lower for sig in cls._CHAT_SIGNATURES)

    @classmethod
    def _detect_booking_links(cls, links: list[str]) -> list[str]:
        found = [
            link for link in links
            if isinstance(link, str)
            and (any(sig in link.lower() for sig in cls._BOOKING_SIGNATURES) or cls._CAL_COM_RE.search(link))
        ]
        return cls._dedupe(found)[:_MAX_LINKS]

    @classmethod
    def _detect_review_links(cls, links: list[str]) -> list[str]:
        found = [
            link for link in links
            if isinstance(link, str) and any(sig in link.lower() for sig in cls._REVIEW_SIGNATURES)
        ]
        return cls._dedupe(found)[:_MAX_LINKS]

    @classmethod
    def _extract_hooks(cls, *, html_lower: str, final_url: str, emails: list, phones: list,
                       forms: list, social_links: list, booking_links: list,
                       booking_cta: bool, has_chat: bool) -> dict[str, Any]:
        # SSL: parsed properly — a None/empty URL can't crash this.
        try:
            scheme = urlparse(final_url).scheme.lower()
        except (ValueError, AttributeError):
            scheme = ""

        is_mobile_ready = bool(cls._VIEWPORT_RE.search(html_lower))

        return {
            "missing_contact_info": not emails and not phones,
            "missing_lead_capture_forms": not forms,
            "missing_social_presence": not social_links,
            "missing_ssl": scheme != "https",
            "outdated_copyright": cls._is_copyright_outdated(html_lower),
            "missing_mobile_optimization": not is_mobile_ready,
            "missing_chat_automation": not has_chat,
            # A visible "Book now" CTA counts as a booking funnel even
            # without a scheduler link — far fewer false "missing booking" pitches.
            "missing_online_booking": not booking_links and not booking_cta,
        }

    @classmethod
    def _is_copyright_outdated(cls, html_lower: str) -> bool:
        """Only years appearing *next to* a copyright marker count. A random
        '2025' elsewhere in the page (blog date, JSON-LD) no longer hides a
        stale footer. JS-injected years (getFullYear) count as current."""
        current_year = datetime.now().year
        latest_found = 0
        for match in cls._COPYRIGHT_MARK_RE.finditer(html_lower):
            window = html_lower[match.end():match.end() + 40]
            years = [int(y) for y in cls._YEAR_RE.findall(window)]
            if years:
                latest_found = max(latest_found, max(years))
        if latest_found:
            return latest_found < current_year
        return bool(cls._DYNAMIC_YEAR_RE.search(html_lower)) and False or False  # no year found → not judgeable

    # ================================================================== #
    # Sanitizers / helpers                                               #
    # ================================================================== #
    @classmethod
    def _clean_emails(cls, raw_emails: Any) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in raw_emails or []:
            if not isinstance(raw, str):
                continue
            email = raw.strip().strip("<>").lower()
            if email.startswith("mailto:"):
                email = email[len("mailto:"):]
            if "@" not in email:
                continue
            local, _, domain = email.partition("@")
            if not local or "." not in domain or len(domain) < 4:
                continue
            if email.endswith(cls._ASSET_EXTS):           # image@2x.png etc.
                continue
            if domain.endswith(cls._JUNK_EMAIL_DOMAINS):  # example.com etc.
                continue
            if "sentry" in domain or "wixpress" in domain:
                continue
            if local in cls._JUNK_EMAIL_LOCALS:
                continue
            if email in seen:
                continue
            seen.add(email)
            cleaned.append(email)
            if len(cleaned) >= _MAX_EMAILS:
                break
        return cleaned

    @classmethod
    def _clean_phones(cls, raw_phones: Any) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in raw_phones or []:
            if not isinstance(raw, str):
                continue
            phone = raw.strip()
            digits = re.sub(r"\D", "", phone)
            if not (7 <= len(digits) <= 15):
                continue
            key = digits[-10:] if len(digits) > 10 else digits
            if len(set(digits)) == 1 or digits in cls._PHONE_PLACEHOLDERS:
                continue
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(phone)
            if len(cleaned) >= _MAX_PHONES:
                break
        return cleaned

    @classmethod
    def _sanitize_statement(cls, value: Any) -> str:
        if not value:
            return ""
        value = html_utils.unescape(cls._TAG_RE.sub(" ", str(value)))
        value = cls._WS_RE.sub(" ", value).strip(" \t-–—|·•")
        value = cls._LEADING_NOISE_RE.sub("", value).strip(" \t-–—|·•")
        if not (30 <= len(value) <= 500):
            return ""
        if cls._JUNK_LINE_RE.search(value):
            return ""
        return cls._truncate(value)

    @staticmethod
    def _truncate(value: str, limit: int = _MAX_MISSION_CHARS) -> str:
        if len(value) <= limit:
            return value
        return value[:limit].rsplit(" ", 1)[0].rstrip(" ,;:–-") + "…"

    @staticmethod
    def _dedupe(items: Iterable[Any]) -> list[str]:
        """Order-preserving dedupe (set() scrambled output order)."""
        return list(dict.fromkeys(i.strip() for i in items if isinstance(i, str) and i.strip()))

    @staticmethod
    def _cap(value: Any, n: int) -> Any:
        return value[:n] if isinstance(value, list) else value

    @staticmethod
    def _safe(what: str, func: Callable[[], _T], default: _T) -> _T:
        """Isolates every extraction step: one bad page/step logs a warning
        and degrades to a default instead of killing the whole result."""
        try:
            return func()
        except Exception as exc:  # deliberately broad — scrape inputs are hostile
            logger.warning("B2BPitchExtractor: step '%s' failed (%s)", what, exc)
            return default

    @staticmethod
    def _default_hooks() -> dict[str, Any]:
        return {
            "missing_contact_info": False,
            "missing_lead_capture_forms": True,
            "missing_social_presence": True,
            "missing_ssl": False,
            "outdated_copyright": False,
            "missing_mobile_optimization": False,
            "missing_chat_automation": True,
            "missing_online_booking": True,
        }