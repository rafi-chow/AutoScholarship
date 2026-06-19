"""Curated public-page discovery."""

from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.extract import extract_scholarship_html
from src.models import Scholarship
from src.policy import SourceDefinition, source_matches_url
from src.source_adapters.base import SafeFetcher


class PublicSourceAdapter:
    def __init__(self, fetcher: SafeFetcher | None = None) -> None:
        self.fetcher = fetcher or SafeFetcher()

    @staticmethod
    def scholarship_links(html: str, base_url: str, source: SourceDefinition) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        links: list[str] = []
        generic_labels = {"scholarship", "scholarships", "details", "learn more", "more"}
        excluded_path_terms = (
            "appeal", "departmental-scholarship", "eligibility", "employee-scholarship",
            "graduate-fellowship", "outside-scholarship", "policy", "policies", "renewal",
            "state-program", "terms-condition", "terms-and-condition", "non-resident-tuition-waiver",
        )
        for anchor in soup.find_all("a", href=True):
            label = anchor.get_text(" ", strip=True).lower()
            url = urljoin(base_url, anchor["href"])
            if url.rstrip("/") == base_url.rstrip("/"):
                continue
            combined = f"{label} {url.lower()}"
            if any(blocked in combined for blocked in ("login", "sign in", "submit application", "apply now")):
                continue
            path = url.lower().split("?", 1)[0]
            if label in generic_labels or any(term in path for term in excluded_path_terms):
                continue
            if not any(term in combined for term in ("scholarship", "award", "opportunity", "details", "learn more")):
                continue
            if source_matches_url(source, url) and url not in links:
                links.append(url)
        return links[:25]

    def discover(self, source: SourceDefinition) -> tuple[list[Scholarship], list[str]]:
        landing = self.fetcher.fetch_configured(str(source.url), source, landing_page=True)
        if not landing.allowed:
            return [], [landing.reason]
        if "html" not in landing.content_type:
            return [], [f"{source.name} landing page is not HTML."]
        links = self.scholarship_links(landing.text, landing.url, source)
        scholarships: list[Scholarship] = []
        warnings: list[str] = []
        if not links:
            try:
                scholarships.append(
                    extract_scholarship_html(
                        landing.text,
                        page_url=landing.url,
                        source_category=source.category,
                    )
                )
            except ValueError as exc:
                warnings.append(f"{source.name}: {exc}")
        for url in links:
            result = self.fetcher.fetch_configured(url, source, landing_page=False)
            if not result.allowed:
                warnings.append(f"{url}: {result.reason}")
                continue
            if "html" not in result.content_type:
                warnings.append(f"{url}: non-HTML scholarship page skipped.")
                continue
            try:
                scholarships.append(
                    extract_scholarship_html(
                        result.text,
                        page_url=result.url,
                        source_category=source.category,
                    )
                )
            except ValueError as exc:
                warnings.append(f"{url}: {exc}")
        return scholarships, warnings
