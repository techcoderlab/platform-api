import re
from datetime import datetime
from bs4 import BeautifulSoup
from typing import Any
from app.modules.web_scraper.domain.models import PageSnapshot
from app.modules.web_scraper.application.extractor_utils import DataExtractor, LinkExtractor, FormExtractor, CTAExtractor

class B2BPitchExtractor:
    """
    Decoupled module to extract tailored B2B growth signals for AI pitches.
    Optimized for token efficiency and high-conversion psychological hooks.
    """

    @classmethod
    def extract(cls, snapshot: PageSnapshot) -> dict[str, Any]:
        """Orchestrates the extraction of all AI pitch metrics."""
        html = snapshot.html
        text = snapshot.text
        soup = BeautifulSoup(html, "html.parser")

        # 1. Base Leads Data (Existing logic)
        forms = FormExtractor.extract_forms(html)
        ctas = CTAExtractor.extract_ctas(html)
        emails = DataExtractor.find_emails(html)
        phones = DataExtractor.find_contacts(html)
        social_links = LinkExtractor.find_social_links(snapshot.links)

        # 2. Token-Optimized Content (Mission/Goal)
        mission_statement = cls._extract_mission_statement(text)

        # 3. Platform Detection
        platform = cls._detect_platform(soup, html)

        # 4. Automation & Reputation Signals
        has_chat = cls._detect_chat_widget(html)
        booking_links = cls._detect_booking_links(snapshot.links)
        review_links = cls._detect_review_links(snapshot.links)

        # 5. Psychological Hooks / Missing Elements
        hooks = cls._extract_hooks(
            snapshot=snapshot,
            emails=emails,
            phones=phones,
            forms=forms,
            social_links=social_links,
            soup=soup,
            has_chat=has_chat,
            booking_links=booking_links
        )

        return {
            "seo": {
                "title": snapshot.title,
                "description": snapshot.meta.get("description") or snapshot.meta.get("og:description") or "",
                "platform": platform
            },
            "content": {
                "mission_statement": mission_statement
            },
            "leads": {
                "emails": emails,
                "phones": phones,
                "social_links": social_links,
                "forms": forms,
                "ctas": ctas,
                "automation": {
                    "has_chat_widget": has_chat,
                    "booking_links": booking_links
                },
                "reputation": {
                    "review_links": review_links
                }
            },
            "pitch_hooks": hooks
        }

    @classmethod
    def _extract_mission_statement(cls, text: str) -> str:
        """Extracts the first ~3 meaningful sentences to represent the business goal/niche."""
        # Clean up whitespace and empty lines
        cleaned = re.sub(r'\n\s*\n', '\n', text.strip())
        
        # Split into lines and filter short navigational noise
        lines = [line.strip() for line in cleaned.split('\n') if len(line.strip()) > 30]
        
        # Take the first 3 substantive lines (which usually contain H1 + Hero text)
        top_lines = lines[:3]
        return "\n".join(top_lines)

    @classmethod
    def _detect_platform(cls, soup: BeautifulSoup, html: str) -> str:
        """Determines the CMS/Framework efficiently by known signatures."""
        html_lower = html.lower()
        
        if 'wp-content' in html_lower or 'wp-includes' in html_lower:
            return "WordPress"
        if 'cdn.shopify.com' in html_lower or 'shopify.com' in html_lower:
            return "Shopify"
        if 'w-webflow' in html_lower:
            return "Webflow"
        if 'static.wixstatic.com' in html_lower or 'wix.com' in html_lower:
            return "Wix"
        if 'squarespace.com' in html_lower:
            return "Squarespace"
        if 'data-reactroot' in html_lower or '_next/static' in html_lower:
            return "React/Next.js"
        if 'nuxt' in html_lower:
            return "Vue/Nuxt"
        
        return "Custom/Unknown"

    @classmethod
    def _detect_chat_widget(cls, html: str) -> bool:
        """Looks for common chat widget scripts."""
        signatures = [
            'intercom', 'tidio', 'drift', 'crisp', 'tawk.to', 'zendesk', 
            'hubspot', 'gorgias', 'livechat'
        ]
        html_lower = html.lower()
        return any(sig in html_lower for sig in signatures)

    @classmethod
    def _detect_booking_links(cls, links: list[str]) -> list[str]:
        """Looks for scheduling links."""
        booking_domains = ['calendly.com', 'acuityscheduling.com', 'simplybook.me', 'square.site/book', 'setmore.com']
        found = []
        for link in links:
            lower_link = link.lower()
            if any(domain in lower_link for domain in booking_domains):
                found.append(link)
        return list(set(found))

    @classmethod
    def _detect_review_links(cls, links: list[str]) -> list[str]:
        """Looks for reputation platform links."""
        reputation_domains = ['yelp.com', 'trustpilot.com', 'tripadvisor.com', 'g.page', 'google.com/maps', 'bbb.org']
        found = []
        for link in links:
            lower_link = link.lower()
            if any(domain in lower_link for domain in reputation_domains):
                found.append(link)
        return list(set(found))

    @classmethod
    def _extract_hooks(cls, snapshot: PageSnapshot, emails: list, phones: list, forms: list, social_links: list, soup: BeautifulSoup, has_chat: bool, booking_links: list) -> dict[str, Any]:
        """Generates psychological hooks and highlights missing essential elements."""
        missing_contact = not emails and not phones
        missing_forms = len(forms) == 0
        missing_social = len(social_links) == 0
        
        # Security hook
        is_secure = snapshot.final_url.startswith('https')

        # Outdated copyright check
        current_year = str(datetime.now().year)
        html_lower = snapshot.html.lower()
        has_copyright = 'copyright' in html_lower or '©' in html_lower
        outdated_copyright = has_copyright and current_year not in html_lower

        # Mobile readiness (viewport meta tag presence)
        viewport = soup.find('meta', attrs={'name': 'viewport'})
        is_mobile_ready = viewport is not None
        
        return {
            "missing_contact_info": missing_contact,
            "missing_lead_capture_forms": missing_forms,
            "missing_social_presence": missing_social,
            "missing_ssl": not is_secure,
            "outdated_copyright": outdated_copyright,
            "missing_mobile_optimization": not is_mobile_ready,
            "missing_chat_automation": not has_chat,
            "missing_online_booking": len(booking_links) == 0
        }
