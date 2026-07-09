import re
from bs4 import BeautifulSoup

class DataExtractor:
    # Ensures a clear boundary and valid domain endings
    _EMAIL_RE = re.compile(r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b')
    # Requires 7-15 digits, allows common formatting characters
    _PHONE_RE = re.compile(r'\b(?:\+?\d{1,3}[ \-.])?\(?\d{2,5}\)?[ \-.]?\d{3,4}[ \-. samples]?\d{3,4}\b')

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
    
from urllib.parse import urlparse

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
