"""RSS/Atom discovery for explicitly configured public feeds."""

from __future__ import annotations

from xml.etree import ElementTree

from src.extract import extract_scholarship_html
from src.models import Scholarship
from src.policy import SourceDefinition
from src.source_adapters.base import SafeFetcher


class RSSSourceAdapter:
    def __init__(self, fetcher: SafeFetcher | None = None) -> None:
        self.fetcher = fetcher or SafeFetcher()

    @staticmethod
    def links(xml: str) -> list[str]:
        root = ElementTree.fromstring(xml)
        links: list[str] = []
        for element in root.iter():
            tag = element.tag.rsplit("}", 1)[-1].lower()
            value = element.attrib.get("href") if tag == "link" else element.text
            if tag == "link" and value and value.startswith(("http://", "https://")) and value not in links:
                links.append(value.strip())
        return links

    def discover(self, source: SourceDefinition) -> tuple[list[Scholarship], list[str]]:
        if source.rss_url is None:
            return [], []
        feed = self.fetcher.fetch_configured(str(source.rss_url), source, landing_page=True)
        if not feed.allowed:
            return [], [feed.reason]
        try:
            links = self.links(feed.text)
        except ElementTree.ParseError as exc:
            return [], [f"Invalid RSS/Atom feed: {exc}"]
        scholarships: list[Scholarship] = []
        warnings: list[str] = []
        for url in links:
            page = self.fetcher.fetch_configured(url, source, landing_page=False)
            if not page.allowed or "html" not in page.content_type:
                warnings.append(f"{url}: {page.reason or 'non-HTML page'}")
                continue
            scholarships.append(
                extract_scholarship_html(page.text, page_url=page.url, source_category=source.category)
            )
        return scholarships, warnings

