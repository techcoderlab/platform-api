import re
import json
from bs4 import BeautifulSoup
from urllib.parse import urlparse

class DataExtractor:
    # Ensures a clear boundary and valid domain endings
    _EMAIL_RE = re.compile(r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b')
    # Requires 7-15 digits, allows common formatting characters
    # _PHONE_RE = re.compile(r'\b(?:\+?\d{1,3}[ \-.])?\(?\d{2,5}\)?[ \-.]?\d{3,4}[ \-. samples]?\d{3,4}\b')
    _PHONE_RE = re.compile(r'\b(?:\+?\d{1,3}[ \-.])?\(?\d{2,5}\)?[ \-.]?\d{3,4}[ \-.]?\d{3,4}\b')


    @classmethod
    def _get_clean_text(cls, html_content: str) -> str:
        """Removes HTML tags, scripts, and CSS styling."""
        soup = BeautifulSoup(html_content, "html.parser")
        
        # Completely destroy script and style blocks
        for element in soup(["script", "style"]):
            element.decompose()
            
        # Extract text content separated by spaces
        return soup.get_text(separator=" ")

    @classmethod
    def find_emails(cls, html_content: str) -> list[str]:
        text = cls._get_clean_text(html_content)
        return list(set(cls._EMAIL_RE.findall(text)))

    @classmethod
    def find_contacts(cls, html_content: str) -> list[str]:
        text = cls._get_clean_text(html_content)
        # Filter out purely structural numbers or tiny fragments
        candidates = cls._PHONE_RE.findall(text)
        return list(set([c.strip() for c in candidates if len(re.sub(r'\D', '', c)) >= 7]))   

class LinkExtractor:
    # Converting to a set with pre-stripped 'www.' speeds up matching
    _SOCIAL_DOMAINS = {
        'facebook.com', 'twitter.com', 'x.com', 'linkedin.com', 'instagram.com', 
        'youtube.com','youtu.be', 'tiktok.com', 'whatsapp.com', 'snapchat.com', 'telegram.org', 
        'telegram.me', 'pinterest.com', 'github.com', 'discord.gg', 'twitch.tv', 
        'reddit.com', 'medium.com', 'shopify.com', 'wix.com', 'wordpress.com', 
        'squarespace.com', 'vimeo.com', 'soundcloud.com', 'behance.net', 
        'dribbble.com', 'tumblr.com', 'vk.com', 'ok.ru'
    }

    @classmethod
    def find_social_links(cls, links: list[str] | None) -> list[str]:
        if not links:
            return []
        
        social_links = set()
        
        for link in links:
            try:
                # 1. Parse URL to target only the actual domain network location
                parsed_url = urlparse(link.lower().strip())
                netloc = parsed_url.netloc
                
                if not netloc:
                    continue
                    
                # 2. Strip out 'www.' subdomains if present
                if netloc.startswith('www.'):
                    netloc = netloc[4:]
                    
                # 3. Precise lookup: O(1) hash check instead of nested looping
                if netloc in cls._SOCIAL_DOMAINS or any(netloc.endswith('.' + d) for d in cls._SOCIAL_DOMAINS):
                    link = link.rstrip('/')
                    social_links.add(link)
                    
            except Exception:
                # Silently skip malformed strings that fail URL parsing
                continue
                
        return list(social_links)

class FormExtractor:
    # Fields that signal "this is a lead-gen form" vs a search box, newsletter, etc.
    _LEAD_FIELD_HINTS = {'name', 'email', 'phone', 'address', 'message', 'company', 'organisation', 'organization'}

    @classmethod
    def extract_forms(cls, html_content: str) -> list[dict]:
        """Extracts field labels per form — never claims a form is broken/working,
        only reports structure. Submission success/error markup is explicitly excluded
        because it's boilerplate on most form builders (Webflow, WordPress, etc.)
        and present regardless of whether a failure ever occurred."""
        soup = BeautifulSoup(html_content, "html.parser")
        forms_data = []

        for form in soup.find_all("form"):
            fields = []
            for field in form.find_all(["input", "textarea", "select"]):
                field_type = field.get("type", "text").lower()
                if field_type in ("hidden", "submit", "button"):
                    continue

                label = (
                    field.get("aria-label")
                    or field.get("placeholder")
                    or field.get("name")
                    or field.get("id")
                    or ""
                ).strip()

                if not label:
                    label_tag = field.find_previous("label")
                    if label_tag:
                        label = label_tag.get_text(strip=True)

                if label:
                    fields.append(label.lower())

            if fields:
                is_lead_form = any(
                    any(hint in f for hint in cls._LEAD_FIELD_HINTS) for f in fields
                )
                forms_data.append({
                    "fields": fields,
                    "field_count": len(fields),
                    "likely_lead_capture": is_lead_form,
                })

        return forms_data

class CTAExtractor:
    # Short, action-oriented text — filters out nav links and body copy
    _ACTION_WORDS = {
        'get', 'request', 'book', 'download', 'contact', 'call', 'quote',
        'start', 'try', 'buy', 'shop', 'sign', 'join', 'learn', 'schedule',
        'talk', 'demo', 'explore', 'view', 'subscribe', 'apply', 'connect'
    }
    _MAX_CTA_WORDS = 6

    @classmethod
    def extract_ctas(cls, html_content: str) -> list[str]:
        soup = BeautifulSoup(html_content, "html.parser")
        candidates = set()

        for tag in soup.find_all(["a", "button"]):
            text = tag.get_text(strip=True)
            if not text:
                continue

            word_count = len(text.split())
            if word_count == 0 or word_count > cls._MAX_CTA_WORDS:
                continue

            first_word = text.split()[0].lower().strip(".,!:")
            has_cta_class = bool(
                tag.get("class") and
                any(k in " ".join(tag.get("class")).lower() for k in ("btn", "button", "cta"))
            )

            if first_word in cls._ACTION_WORDS or has_cta_class:
                candidates.add(text)

        return list(candidates)[:15]  # cap — we only need representative CTAs, not all
