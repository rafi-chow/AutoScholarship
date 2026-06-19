"""Deterministic scholarship extraction from pasted text and allowed HTML pages."""

from __future__ import annotations

import re
from datetime import date, datetime
from html import unescape
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.models import Scholarship


URL_RE = re.compile(r"https?://[^\s<>\]\[\)\(]+", re.IGNORECASE)
MONEY_RE = re.compile(r"\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)")
DATE_PATTERNS = (
    re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),
    re.compile(
        r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
        r"Dec(?:ember)?)\s+\d{1,2}(?:st|nd|rd|th)?(?:,)?\s+\d{4}\b",
        re.IGNORECASE,
    ),
)

SECTION_ALIASES = {
    "name": ("name", "scholarship name", "title"),
    "amount": ("amount", "award", "award amount", "scholarship amount"),
    "deadline": ("deadline", "application deadline", "due date", "closes"),
    "eligibility": ("eligibility", "eligibility requirements", "who can apply", "requirements"),
    "documents": ("required documents", "documents required", "application materials", "materials"),
    "prompts": (
        "essay prompt", "essay prompts", "prompt", "prompts", "essay question", "questions",
        "personal statement", "short answer", "short answer prompt",
    ),
    "url": ("application url", "apply", "apply here", "application link"),
}
ALL_ALIASES = sorted(
    ((alias, section) for section, aliases in SECTION_ALIASES.items() for alias in aliases),
    key=lambda item: len(item[0]),
    reverse=True,
)

DOCUMENT_KEYWORDS = {
    "resume": "Resume",
    "transcript": "Transcript",
    "recommendation": "Recommendation letter",
    "reference letter": "Reference letter",
    "fafsa": "FAFSA",
    "financial aid": "Financial aid information",
    "proof of enrollment": "Proof of enrollment",
    "enrollment verification": "Enrollment verification",
    "tax return": "Tax return",
}


def _clean_lines(text: str) -> list[str]:
    text = unescape(text).replace("\r", "\n")
    lines = []
    for raw in text.split("\n"):
        line = re.sub(r"^[\s•*\-–—]+", "", raw).strip()
        line = re.sub(r"\s+", " ", line)
        if line:
            lines.append(line)
    return lines


def _section_for_line(line: str) -> tuple[str | None, str]:
    normalized = line.strip().lower()
    for alias, section in ALL_ALIASES:
        if normalized == alias:
            return section, ""
        match = re.match(rf"^{re.escape(alias)}\s*[:\-–—]\s*(.*)$", line, re.IGNORECASE)
        if match:
            return section, match.group(1).strip()
    return None, line


def _sections(lines: list[str]) -> dict[str, list[str]]:
    result = {key: [] for key in SECTION_ALIASES}
    active: str | None = None
    for line in lines:
        section, value = _section_for_line(line)
        if section:
            active = section
            if value:
                result[section].append(value)
            continue
        if active:
            result[active].append(value)
    return result


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = value.strip(" ;:-")
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def _parse_amount(lines: list[str], sections: dict[str, list[str]]) -> float | None:
    candidates = sections["amount"] + [
        line for line in lines if re.search(r"\b(amount|award|scholarship value|offer)\b", line, re.IGNORECASE)
    ]
    for candidate in candidates:
        match = MONEY_RE.search(candidate)
        if match:
            return float(match.group(1).replace(",", ""))
    return None


def _parse_date_value(value: str) -> date | None:
    cleaned = re.sub(r"(\d)(st|nd|rd|th)", r"\1", value, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    formats = (
        "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%B %d %Y",
        "%b %d, %Y", "%b %d %Y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            pass
    return None


def _parse_deadline(lines: list[str], sections: dict[str, list[str]]) -> date | None:
    candidates = sections["deadline"] + [
        line for line in lines if re.search(r"\b(deadline|due date|closes?)\b", line, re.IGNORECASE)
    ]
    for candidate in candidates:
        for pattern in DATE_PATTERNS:
            match = pattern.search(candidate)
            if match and (parsed := _parse_date_value(match.group(0))):
                return parsed
    return None


def _tri_state(text: str, positive: tuple[str, ...], negative: tuple[str, ...]) -> bool | None:
    lowered = text.lower()
    if any(phrase in lowered for phrase in negative):
        return False
    if any(phrase in lowered for phrase in positive):
        return True
    return None


def _likely_name(lines: list[str], sections: dict[str, list[str]], fallback: str | None) -> str:
    if sections["name"]:
        return sections["name"][0]
    if fallback:
        return fallback
    for line in lines:
        section, _ = _section_for_line(line)
        if not section and not URL_RE.fullmatch(line) and len(line) <= 180:
            return line
    return "Imported Scholarship"


def _extract_prompts(lines: list[str], sections: dict[str, list[str]]) -> list[str]:
    prompts = list(sections["prompts"])
    prompts.extend(
        line for line in lines
        if line.endswith("?") and len(line.split()) >= 4
    )
    return _unique(prompts)


def _extract_documents(lines: list[str], sections: dict[str, list[str]]) -> list[str]:
    documents = list(sections["documents"])
    for line in lines:
        lowered = line.lower()
        for keyword, label in DOCUMENT_KEYWORDS.items():
            if keyword in lowered:
                documents.append(label)
    return _unique(documents)


def _requirement_lines(lines: list[str], sections: dict[str, list[str]]) -> list[str]:
    requirements = list(sections["eligibility"])
    markers = (
        "must ", "eligible", "applicant", "required", "only", "minimum gpa",
        "citizen", "resident", "enrolled", "major", "undergraduate",
    )
    requirements.extend(line for line in lines if any(marker in line.lower() for marker in markers))
    return _unique(requirements)


def _restriction_lines(requirements: list[str], kind: str) -> list[str]:
    keywords = {
        "citizenship": ("citizen", "permanent resident", "residency", "resident status"),
        "location": ("texas", "dfw", "dallas", "fort worth", "county", "local resident", "state resident"),
        "school": ("university of texas at arlington", "uta", "college", "university", "school", "enrolled at"),
        "major": ("major", "computer science", "stem", "engineering", "software", "aerospace", "data science"),
    }[kind]
    return _unique([line for line in requirements if any(word in line.lower() for word in keywords)])


def extract_scholarship_text(
    text: str,
    *,
    fallback_name: str | None = None,
    source_url: str | None = None,
    source_type: str = "manual",
    source_category: str | None = None,
    application_url_hint: str | None = None,
) -> Scholarship:
    """Parse one scholarship opportunity from raw text without an LLM."""

    lines = _clean_lines(text)
    if not lines:
        raise ValueError("Scholarship text is empty.")
    sections = _sections(lines)
    full_text = "\n".join(lines)
    lowered = full_text.lower()
    requirements = _requirement_lines(lines, sections)
    documents = _extract_documents(lines, sections)
    prompts = _extract_prompts(lines, sections)

    urls = [match.group(0).rstrip(".,;:") for match in URL_RE.finditer(full_text)]
    section_urls = [
        match.group(0).rstrip(".,;:")
        for value in sections["url"] for match in URL_RE.finditer(value)
    ]
    application_url = application_url_hint or (section_urls[0] if section_urls else (urls[0] if urls else None))

    recommendation_required = _tri_state(
        lowered,
        ("recommendation required", "recommendation letter required", "must submit a recommendation", "letter of recommendation"),
        ("no recommendation required", "recommendation not required", "no recommendation letter"),
    )
    if recommendation_required is None and any("recommendation" in doc.lower() for doc in documents):
        recommendation_required = True
    fafsa_required = _tri_state(
        lowered,
        ("fafsa required", "must complete the fafsa", "must submit the fafsa", "completed fafsa"),
        ("fafsa not required", "no fafsa required"),
    )
    if fafsa_required is None and any("fafsa" in document.lower() for document in documents):
        fafsa_required = True
    first_generation_required = _tri_state(
        lowered,
        ("first-generation students only", "first generation students only", "must be first-generation", "must be a first-generation"),
        ("first-generation status not required", "first generation status not required"),
    )
    need_only = _tri_state(
        lowered,
        ("low-income students only", "low income students only", "must demonstrate financial need", "financial need is required", "need-based applicants only"),
        ("financial need not required", "no financial need requirement", "not based on financial need"),
    )
    no_essay = any(
        phrase in lowered for phrase in (
            "no essay", "no-essay", "essay not required", "quick apply", "sweepstakes", "random drawing",
        )
    )
    if no_essay:
        prompts = []

    return Scholarship(
        name=_likely_name(lines, sections, fallback_name),
        amount=_parse_amount(lines, sections),
        deadline=_parse_deadline(lines, sections),
        eligibility=requirements,
        location_restrictions=_restriction_lines(requirements, "location"),
        school_restrictions=_restriction_lines(requirements, "school"),
        major_restrictions=_restriction_lines(requirements, "major"),
        essay_prompts=prompts,
        required_documents=documents,
        recommendation_required=recommendation_required,
        fafsa_required=fafsa_required,
        first_generation_required=first_generation_required,
        need_only=need_only,
        citizenship_residency_requirements=_restriction_lines(requirements, "citizenship"),
        no_essay_quick_apply=no_essay,
        application_url=application_url,
        source_url=source_url,
        source_type=source_type,
        source_category=source_category,
    )


def extract_scholarship_html(
    html: str,
    *,
    page_url: str,
    source_category: str | None = None,
) -> Scholarship:
    """Extract visible page content and likely application links from allowed HTML."""

    soup = BeautifulSoup(html, "html.parser")
    for element in soup(["script", "style", "noscript", "svg"]):
        element.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else None
    headings = [item.get_text(" ", strip=True) for item in soup.find_all(["h1", "h2", "h3"])]
    generic_names = {
        "scholarship", "scholarships", "financial aid", "student financial aid",
        "financial aid and scholarship", "financial aid and scholarships",
    }
    cleaned_title = None
    if title:
        cleaned_title = re.split(
            r"\s+(?:\||[-–—])\s+(?:Financial Aid|The University|UTA|Home)\b",
            title,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip()
    fallback_name = (
        cleaned_title
        if cleaned_title
        and cleaned_title.casefold() not in generic_names
        and any(word in cleaned_title.casefold() for word in ("scholarship", "award", "fellowship"))
        else None
    )
    if not fallback_name:
        fallback_name = next(
            (
                heading for heading in headings
                if heading.casefold() not in generic_names
                and "financial aid" not in heading.casefold()
                and any(word in heading.casefold() for word in ("scholarship", "award", "fellowship"))
            ),
            None,
        )
    if not fallback_name:
        fallback_name = next((heading for heading in headings if heading.casefold() not in generic_names), title)
    text = soup.get_text("\n", strip=True)

    apply_links: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = urljoin(page_url, anchor["href"])
        if not href.startswith(("http://", "https://")):
            continue
        label = anchor.get_text(" ", strip=True).lower()
        strong_apply_label = any(
            phrase in label
            for phrase in (
                "apply now", "apply here", "start application", "scholarship application",
                "apply for this scholarship", "submit application",
            )
        )
        generic_or_portal = label in {"apply", "application"} or any(
            phrase in label for phrase in ("apply for admission", "apply for aid", "login", "sign in")
        )
        if strong_apply_label and not generic_or_portal:
            apply_links.append(href)
    application_hint = apply_links[0] if apply_links else None
    link_context = "\n".join(f"Application URL: {url}" for url in apply_links)
    return extract_scholarship_text(
        f"{text}\n{link_context}",
        fallback_name=fallback_name,
        source_url=page_url,
        source_type="public_url",
        source_category=source_category,
        application_url_hint=application_hint,
    )
