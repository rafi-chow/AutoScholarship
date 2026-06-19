from datetime import date

from src.extract import extract_scholarship_html, extract_scholarship_text


MANUAL_TEXT = """
Scholarship Name: DFW Future Software Engineers Scholarship
Award Amount: $2,500
Application Deadline: August 15, 2026

Eligibility:
- Must be a U.S. citizen or permanent resident.
- Must be a Texas resident in the Dallas-Fort Worth area.
- Must be enrolled at the University of Texas at Arlington.
- Applicants must major in Computer Science, Software Engineering, or another STEM field.
- Minimum GPA of 3.0.

Required Documents:
- Official transcript
- One recommendation letter
- Completed FAFSA

Essay Prompt:
How will your software education benefit the DFW community?

Application URL: https://foundation.example/apply/dfw-software
"""


def test_manual_text_extracts_structured_fields() -> None:
    scholarship = extract_scholarship_text(MANUAL_TEXT)

    assert scholarship.name == "DFW Future Software Engineers Scholarship"
    assert scholarship.amount == 2500
    assert scholarship.deadline == date(2026, 8, 15)
    assert scholarship.recommendation_required is True
    assert scholarship.fafsa_required is True
    assert scholarship.first_generation_required is None
    assert scholarship.need_only is None
    assert scholarship.no_essay_quick_apply is False
    assert scholarship.essay_prompts == ["How will your software education benefit the DFW community?"]
    assert "Transcript" in scholarship.required_documents
    assert scholarship.citizenship_residency_requirements
    assert scholarship.location_restrictions
    assert scholarship.school_restrictions
    assert scholarship.major_restrictions
    assert str(scholarship.application_url) == "https://foundation.example/apply/dfw-software"


def test_manual_text_extracts_strict_flags_and_no_essay() -> None:
    scholarship = extract_scholarship_text(
        """
Name: First Steps Quick Apply Award
Amount: $500
Deadline: 2026-12-01
Eligibility:
First-generation students only.
Applicants must demonstrate financial need.
No essay required. Selection is a random drawing.
"""
    )

    assert scholarship.first_generation_required is True
    assert scholarship.need_only is True
    assert scholarship.no_essay_quick_apply is True
    assert scholarship.essay_prompts == []


def test_html_extraction_uses_title_visible_text_and_apply_link() -> None:
    scholarship = extract_scholarship_html(
        """
        <html><head><title>Ignored Site Suffix</title></head><body>
        <h1>Texas Aerospace Scholarship</h1>
        <p>Award: $3,000</p><p>Deadline: September 1, 2026</p>
        <p>Eligible Texas engineering undergraduates.</p>
        <a href="/scholarships/apply">Apply now</a>
        </body></html>
        """,
        page_url="https://example.org/scholarships/aerospace",
        source_category="engineering_aerospace",
    )

    assert scholarship.name == "Texas Aerospace Scholarship"
    assert scholarship.amount == 3000
    assert scholarship.source_type == "public_url"
    assert scholarship.source_category == "engineering_aerospace"
    assert str(scholarship.application_url) == "https://example.org/scholarships/apply"

