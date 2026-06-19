"""Policy-gated, stop-before-submit Playwright autofill."""

from __future__ import annotations

import json
import argparse
import os
import re
import time
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field

from src.drafter import slugify
from dotenv import load_dotenv

from src.approval import check_safe_submit_policy
from src.db import ScholarshipDatabase
from src.models import DraftRecord, DraftStatus, Profile, Scholarship, StrictModel
from src.policy import (
    PolicyAction,
    SourceDefinition,
    check_source_policy,
    source_matches_url,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAPPINGS_PATH = ROOT / "data" / "form_mappings.yaml"
DEFAULT_SCREENSHOTS_DIR = ROOT / "screenshots"
DEFAULT_LOGS_DIR = ROOT / "autofill_logs"
DEFAULT_BROWSER_PROFILE_DIR = ROOT / "data" / "browser_profile"


class AutofillStatus(StrEnum):
    COMPLETED = "completed"
    MANUAL_REQUIRED = "manual_required"
    BLOCKED = "blocked"
    ERROR = "error"


class FieldMapping(StrictModel):
    profile_key: str
    labels: list[str] = Field(min_length=1)


class FormMappings(StrictModel):
    version: int = 1
    fields: dict[str, FieldMapping]
    sites: dict[str, dict[str, FieldMapping]] = Field(default_factory=dict)


class FieldDescriptor(StrictModel):
    index: int
    label: str = ""
    name: str = ""
    element_id: str = ""
    placeholder: str = ""
    aria_label: str = ""
    autocomplete: str = ""
    tag: str = "input"
    input_type: str = "text"
    required: bool = False
    options: list[str] = Field(default_factory=list)


class PlannedFill(StrictModel):
    field_index: int
    field_label: str
    mapping_name: str
    profile_key: str
    value: str | bool


class ManualField(StrictModel):
    field_index: int
    label: str
    reason: str


class FilledField(StrictModel):
    field_index: int
    label: str
    mapping_name: str
    value_log: str


class AutofillPlan(StrictModel):
    fills: list[PlannedFill] = Field(default_factory=list)
    manual_fields: list[ManualField] = Field(default_factory=list)


class AutofillReport(StrictModel):
    scholarship_id: int | None = None
    scholarship_name: str
    application_url: str
    status: AutofillStatus
    policy_reason: str
    blockers: list[str] = Field(default_factory=list)
    filled_fields: list[FilledField] = Field(default_factory=list)
    manual_fields: list[ManualField] = Field(default_factory=list)
    screenshot_path: Path | None = None
    before_submit_screenshot_path: Path | None = None
    after_submit_screenshot_path: Path | None = None
    log_path: Path | None = None
    submit_requested: bool = False
    submitted: bool = False
    message: str
    created_at: datetime = Field(default_factory=datetime.now)


class AutofillError(RuntimeError):
    """Raised for browser/runtime failures that are safe to report to the user."""


def load_form_mappings(path: str | Path = DEFAULT_MAPPINGS_PATH) -> FormMappings:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return FormMappings.model_validate(raw)


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def profile_field_values(profile: Profile, essay_text: str | None = None) -> dict[str, str | bool | None]:
    """Expose only reviewed profile fields used by mappings."""

    name_parts = profile.full_name.split()
    education = profile.education[0]
    linkedin = profile.linkedin
    github = profile.github
    if linkedin and not linkedin.startswith(("http://", "https://")):
        linkedin = f"https://{linkedin}"
    if github and not github.startswith(("http://", "https://")):
        github = f"https://{github}"
    return {
        "first_name": name_parts[0] if name_parts else None,
        "last_name": name_parts[-1] if len(name_parts) > 1 else None,
        "email": profile.email,
        "phone": profile.phone,
        "city": profile.address.city,
        "state": profile.address.state,
        "school": education.school,
        "major": education.major,
        "gpa": str(education.gpa) if education.gpa is not None else None,
        "graduation_date": education.graduation_date.isoformat() if education.graduation_date else None,
        "citizenship": profile.citizenship,
        "texas_resident": profile.eligibility_preferences.texas_resident,
        "essay": essay_text,
        "linkedin": linkedin,
        "github": github,
    }


def _field_text(field: FieldDescriptor) -> str:
    return " ".join(
        part for part in (
            field.label, field.name, field.element_id, field.placeholder,
            field.aria_label, field.autocomplete,
        ) if part
    )


def _mapping_score(field: FieldDescriptor, mapping: FieldMapping) -> int:
    text = _normalize(_field_text(field))
    if not text:
        return 0
    # Never infer applicant data into fields belonging to another person.
    if any(word in text.split() for word in ("parent", "guardian", "recommender", "reference", "spouse")):
        return 0
    score = 0
    for label in mapping.labels:
        pattern = _normalize(label)
        if text == pattern:
            score = max(score, 100 + len(pattern))
        elif re.search(rf"(?:^| ){re.escape(pattern)}(?: |$)", text):
            score = max(score, 75 + len(pattern))
    return score


def build_autofill_plan(
    fields: list[FieldDescriptor],
    mappings: FormMappings,
    profile: Profile,
    *,
    essay_text: str | None = None,
) -> AutofillPlan:
    """Match visible fields conservatively; ambiguous and unknown fields remain manual."""

    values = profile_field_values(profile, essay_text)
    fills: list[PlannedFill] = []
    manual: list[ManualField] = []
    for field in fields:
        if field.input_type in {"hidden", "password", "file", "submit", "button", "image", "reset"}:
            continue
        scored = sorted(
            (
                (_mapping_score(field, mapping), name, mapping)
                for name, mapping in mappings.fields.items()
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        best_score, mapping_name, mapping = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else 0
        label = field.label or field.placeholder or field.name or field.element_id or f"Field {field.index + 1}"
        if best_score < 80 or (second_score and best_score - second_score < 5):
            manual.append(ManualField(field_index=field.index, label=label, reason="No unique high-confidence mapping."))
            continue
        value = values.get(mapping.profile_key)
        if value is None or value == "":
            manual.append(
                ManualField(
                    field_index=field.index,
                    label=label,
                    reason=f"Verified profile value is missing for {mapping.profile_key}.",
                )
            )
            continue
        fills.append(
            PlannedFill(
                field_index=field.index,
                field_label=label,
                mapping_name=mapping_name,
                profile_key=mapping.profile_key,
                value=value,
            )
        )
    return AutofillPlan(fills=fills, manual_fields=manual)


def detect_blockers_from_snapshot(*, text: str, html: str) -> list[str]:
    """Detect manual protections without attempting to evade them."""

    combined = f"{text}\n{html}".lower()
    blockers: list[str] = []
    if any(marker in combined for marker in ("recaptcha", "hcaptcha", "captcha", "verify you are human")):
        blockers.append("CAPTCHA/manual human verification")
    if any(marker in combined for marker in (
        "two-factor", "two factor", "2fa", "multi-factor", "multifactor", "one-time code",
        "one time code", "verification code", "autocomplete=\"one-time-code\"",
    )):
        blockers.append("2FA/verification code")
    if (
        "type=\"password\"" in combined
        or "type='password'" in combined
        or "autocomplete=\"username\"" in combined
        or "autocomplete='username'" in combined
        or re.search(r"<form[^>]+(?:action|id|class)=[\"'][^\"']*(?:login|sign[-_ ]?in)", combined)
    ):
        blockers.append("login required")
    if any(marker in combined for marker in (
        "access denied", "unusual traffic", "checking your browser", "cloudflare ray id", "subscription required",
    )):
        blockers.append("paywall or anti-bot protection")
    return list(dict.fromkeys(blockers))


def _masked_value(mapping_name: str, value: str | bool) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if mapping_name == "email" and "@" in value:
        local, domain = value.split("@", 1)
        return f"{local[:1]}***@{domain}"
    if mapping_name == "phone":
        digits = re.sub(r"\D", "", value)
        return f"***{digits[-4:]}" if digits else "<redacted>"
    return "<redacted>"


def _save_report(report: AutofillReport, logs_dir: Path) -> AutofillReport:
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = report.created_at.strftime("%Y%m%d-%H%M%S-%f")
    slug = slugify(report.scholarship_name, fallback="scholarship")
    path = (logs_dir / f"{slug}-{report.scholarship_id or 'new'}-{stamp}.json").resolve()
    saved = report.model_copy(update={"log_path": path})
    path.write_text(json.dumps(saved.model_dump(mode="json"), indent=2), encoding="utf-8")
    return saved


def latest_autofill_report(
    scholarship: Scholarship,
    logs_dir: str | Path = DEFAULT_LOGS_DIR,
) -> AutofillReport | None:
    directory = Path(logs_dir)
    if not directory.exists():
        return None
    slug = slugify(scholarship.name, fallback="scholarship")
    candidates = sorted(directory.glob(f"{slug}-{scholarship.id or 'new'}-*.json"), reverse=True)
    if not candidates:
        return None
    return AutofillReport.model_validate_json(candidates[0].read_text(encoding="utf-8"))


def _page_snapshot(page: Any) -> tuple[str, str]:
    try:
        text = page.locator("body").inner_text(timeout=3000)
    except Exception:
        text = ""
    try:
        html = page.content()
    except Exception:
        html = ""
    return text, html


def _page_fields(page: Any) -> tuple[list[FieldDescriptor], Any]:
    locator = page.locator("input, textarea, select")
    fields: list[FieldDescriptor] = []
    for index in range(locator.count()):
        element = locator.nth(index)
        try:
            if not element.is_visible() or not element.is_enabled():
                continue
            metadata = element.evaluate(
                """el => ({
                    label: el.labels ? Array.from(el.labels).map(x => x.innerText).join(' ') : '',
                    name: el.name || '', id: el.id || '', placeholder: el.placeholder || '',
                    aria: el.getAttribute('aria-label') || '', autocomplete: el.autocomplete || '',
                    tag: el.tagName.toLowerCase(), type: (el.type || 'text').toLowerCase(),
                    required: !!el.required,
                    options: el.options ? Array.from(el.options).map(x => x.text.trim()).filter(Boolean) : []
                })"""
            )
            fields.append(
                FieldDescriptor(
                    index=index,
                    label=metadata["label"],
                    name=metadata["name"],
                    element_id=metadata["id"],
                    placeholder=metadata["placeholder"],
                    aria_label=metadata["aria"],
                    autocomplete=metadata["autocomplete"],
                    tag=metadata["tag"],
                    input_type=metadata["type"],
                    required=metadata["required"],
                    options=metadata["options"],
                )
            )
        except Exception:
            continue
    return fields, locator


def _apply_plan(plan: AutofillPlan, locator: Any, fields: list[FieldDescriptor]) -> tuple[list[FilledField], list[ManualField]]:
    descriptors = {field.index: field for field in fields}
    filled: list[FilledField] = []
    manual = list(plan.manual_fields)
    for planned in plan.fills:
        field = descriptors[planned.field_index]
        element = locator.nth(planned.field_index)
        try:
            value = planned.value
            if field.input_type in {"checkbox", "radio"}:
                if bool(value):
                    element.check()
                else:
                    element.uncheck()
            elif field.tag == "select":
                desired = "Yes" if value is True else "No" if value is False else str(value)
                option = next(
                    (item for item in field.options if _normalize(item) == _normalize(desired)),
                    None,
                )
                if option is None:
                    raise ValueError(f"No select option confidently matches {desired!r}.")
                element.select_option(label=option)
            else:
                desired = "Yes" if value is True else "No" if value is False else str(value)
                if field.input_type == "month" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", desired):
                    desired = desired[:7]
                element.fill(desired)
            filled.append(
                FilledField(
                    field_index=planned.field_index,
                    label=planned.field_label,
                    mapping_name=planned.mapping_name,
                    value_log=_masked_value(planned.mapping_name, planned.value),
                )
            )
        except Exception as exc:
            manual.append(
                ManualField(
                    field_index=planned.field_index,
                    label=planned.field_label,
                    reason=f"Playwright could not fill this field safely: {exc}",
                )
            )
    return filled, manual


def _upload_safe_documents(page: Any, profile: Profile) -> tuple[list[FilledField], list[ManualField]]:
    """Upload only explicitly configured resume/transcript paths to unambiguous file inputs."""

    uploaded: list[FilledField] = []
    manual: list[ManualField] = []
    locator = page.locator('input[type="file"]')
    document_map = {
        "resume": profile.documents.resume,
        "cv": profile.documents.resume,
        "transcript": profile.documents.transcript,
    }
    allowed_suffixes = {".pdf", ".doc", ".docx"}
    for index in range(locator.count()):
        element = locator.nth(index)
        try:
            metadata = element.evaluate(
                """el => ({
                    label: el.labels ? Array.from(el.labels).map(x => x.innerText).join(' ') : '',
                    name: el.name || '', id: el.id || '', aria: el.getAttribute('aria-label') || ''
                })"""
            )
            label = " ".join(metadata.values()).strip() or f"File upload {index + 1}"
            normalized = _normalize(label)
            matches = {key: path for key, path in document_map.items() if key in normalized and path is not None}
            unique_paths = {Path(path).expanduser().resolve() for path in matches.values()}
            if len(unique_paths) != 1:
                manual.append(ManualField(field_index=-1, label=label, reason="No unique configured document matches this upload field."))
                continue
            path = unique_paths.pop()
            if not path.is_file() or path.suffix.lower() not in allowed_suffixes:
                manual.append(ManualField(field_index=-1, label=label, reason="Configured document is missing or has an unsupported file type."))
                continue
            element.set_input_files(str(path))
            uploaded.append(FilledField(field_index=-1, label=label, mapping_name="document_upload", value_log=f"<local {path.suffix.lower()} file>"))
        except Exception as exc:
            manual.append(ManualField(field_index=-1, label=f"File upload {index + 1}", reason=f"Document upload stopped safely: {exc}"))
    return uploaded, manual


def autofill_application(
    scholarship: Scholarship,
    profile: Profile,
    *,
    source: SourceDefinition,
    essay_text: str | None = None,
    submit_mode: bool = False,
    drafts: list[DraftRecord] | None = None,
    mappings_path: str | Path = DEFAULT_MAPPINGS_PATH,
    screenshots_dir: str | Path = DEFAULT_SCREENSHOTS_DIR,
    logs_dir: str | Path = DEFAULT_LOGS_DIR,
    browser_profile_dir: str | Path = DEFAULT_BROWSER_PROFILE_DIR,
    headless: bool = False,
    manual_login_wait_seconds: int = 0,
    review_wait_seconds: int = 0,
) -> AutofillReport:
    """Open, inspect, conservatively fill, screenshot, and stop before submit by default."""

    if scholarship.application_url is None:
        raise AutofillError("Scholarship has no application URL.")
    drafts = drafts or []
    url = str(scholarship.application_url)
    policy = check_source_policy(source, PolicyAction.AUTOFILL)
    if not policy.allowed or not source_matches_url(source, url):
        reason = policy.reason if not policy.allowed else "Application URL is outside the configured allowed source path."
        return _save_report(
            AutofillReport(
                scholarship_id=scholarship.id,
                scholarship_name=scholarship.name,
                application_url=url,
                status=AutofillStatus.BLOCKED,
                policy_reason=reason,
                submit_requested=submit_mode,
                message="Autofill did not open the site because source policy failed closed.",
            ),
            Path(logs_dir),
        )
    if not scholarship.approved_autofill:
        return _save_report(
            AutofillReport(
                scholarship_id=scholarship.id,
                scholarship_name=scholarship.name,
                application_url=url,
                status=AutofillStatus.BLOCKED,
                policy_reason="Autofill approval has not been granted in the Approval Queue.",
                submit_requested=submit_mode,
                message="Autofill did not open the site because approval is required first.",
            ),
            Path(logs_dir),
        )
    if submit_mode:
        submit_precheck = check_safe_submit_policy(
            scholarship, source=source, drafts=drafts, submit_mode=True
        )
        if not submit_precheck.allowed:
            return _save_report(
                AutofillReport(
                    scholarship_id=scholarship.id,
                    scholarship_name=scholarship.name,
                    application_url=url,
                    status=AutofillStatus.BLOCKED,
                    policy_reason=submit_precheck.reason,
                    submit_requested=True,
                    message="Submit-approved mode stopped before opening the site.",
                ),
                Path(logs_dir),
            )

    mappings = load_form_mappings(mappings_path)
    screenshot_dir = Path(screenshots_dir)
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    Path(browser_profile_dir).mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    screenshot = (screenshot_dir / f"{slugify(scholarship.name, fallback='scholarship')}-{stamp}-prepared.png").resolve()
    after_screenshot = (screenshot_dir / f"{slugify(scholarship.name, fallback='scholarship')}-{stamp}-submitted.png").resolve()
    report: AutofillReport | None = None

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(Path(browser_profile_dir).resolve()),
                headless=headless,
            )
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                text, html = _page_snapshot(page)
                blockers = detect_blockers_from_snapshot(text=text, html=html)
                if blockers == ["login required"] and manual_login_wait_seconds > 0:
                    print("Login detected. Log in manually in the opened browser; autofill is paused.")
                    deadline = time.monotonic() + manual_login_wait_seconds
                    while time.monotonic() < deadline and "login required" in blockers:
                        page.wait_for_timeout(1000)
                        text, html = _page_snapshot(page)
                        blockers = detect_blockers_from_snapshot(text=text, html=html)
                if blockers:
                    page.screenshot(path=str(screenshot), full_page=True)
                    report = AutofillReport(
                        scholarship_id=scholarship.id,
                        scholarship_name=scholarship.name,
                        application_url=url,
                        status=AutofillStatus.MANUAL_REQUIRED,
                        policy_reason=policy.reason,
                        blockers=blockers,
                        screenshot_path=screenshot,
                        submit_requested=submit_mode,
                        message="Autofill stopped. Complete login/CAPTCHA/2FA or protected access manually.",
                    )
                else:
                    fields, locator = _page_fields(page)
                    plan = build_autofill_plan(fields, mappings, profile, essay_text=essay_text)
                    filled, manual = _apply_plan(plan, locator, fields)
                    uploaded, upload_manual = _upload_safe_documents(page, profile)
                    filled.extend(uploaded)
                    manual.extend(upload_manual)
                    page.screenshot(path=str(screenshot), full_page=True)
                    submitted = False
                    submit_reason = check_safe_submit_policy(
                        scholarship,
                        source=source,
                        drafts=drafts,
                        submit_mode=submit_mode,
                        blockers=["manual fields remain after prepare"] if manual else [],
                    )
                    if submit_reason.allowed:
                        submit_controls = page.locator('button[type="submit"], input[type="submit"]')
                        if submit_controls.count() == 1:
                            label = submit_controls.first.evaluate("el => (el.innerText || el.value || el.getAttribute('aria-label') || '').toLowerCase()")
                            if any(word in label for word in ("login", "log in", "sign in")):
                                manual.append(ManualField(field_index=-1, label=label, reason="Submit control appears to be login."))
                            else:
                                submit_controls.first.click()
                                submitted = True
                                page.wait_for_timeout(1000)
                                page.screenshot(path=str(after_screenshot), full_page=True)
                        else:
                            manual.append(
                                ManualField(
                                    field_index=-1,
                                    label="Final submit",
                                    reason="A unique final submit control was not found; submission remains manual.",
                                )
                            )
                    report = AutofillReport(
                        scholarship_id=scholarship.id,
                        scholarship_name=scholarship.name,
                        application_url=url,
                        status=AutofillStatus.MANUAL_REQUIRED if manual else AutofillStatus.COMPLETED,
                        policy_reason=(submit_reason.reason if submit_mode else policy.reason),
                        filled_fields=filled,
                        manual_fields=manual,
                        screenshot_path=screenshot,
                        before_submit_screenshot_path=screenshot if submit_mode else None,
                        after_submit_screenshot_path=after_screenshot if submitted else None,
                        submit_requested=submit_mode,
                        submitted=submitted,
                        message=(
                            "Autofill completed and stopped for human review."
                            if not submitted
                            else "Explicitly approved submit mode completed."
                        ),
                    )
                if review_wait_seconds > 0 and not page.is_closed():
                    print("Review the opened browser. Close it when finished; final submit remains manual by default.")
                    deadline = time.monotonic() + review_wait_seconds
                    while time.monotonic() < deadline and not page.is_closed():
                        page.wait_for_timeout(500)
            finally:
                context.close()
    except Exception as exc:
        report = AutofillReport(
            scholarship_id=scholarship.id,
            scholarship_name=scholarship.name,
            application_url=url,
            status=AutofillStatus.ERROR,
            policy_reason=policy.reason,
            screenshot_path=screenshot if screenshot.exists() else None,
            submit_requested=submit_mode,
            message=f"Autofill stopped safely: {exc}",
        )
    return _save_report(report, Path(logs_dir))


def _source_for_scholarship(scholarship: Scholarship, sources_path: Path) -> SourceDefinition | None:
    from src.policy import load_source_catalog

    if scholarship.application_url is None:
        return None
    return next(
        (
            source for source in load_source_catalog(sources_path).sources
            if source_matches_url(source, str(scholarship.application_url))
        ),
        None,
    )


def _ready_essay_text(scholarship_id: int, drafts: list[DraftRecord]) -> str | None:
    draft = next(
        (item for item in drafts if item.scholarship_id == scholarship_id and item.status == DraftStatus.READY_TO_REVIEW),
        None,
    )
    if draft is None:
        return None
    try:
        markdown = draft.path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"## Draft answer\s+(.+?)(?=\n## )", markdown, re.DOTALL)
    return match.group(1).strip() if match else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Policy-gated scholarship autofill")
    parser.add_argument("--scholarship-id", type=int, required=True)
    parser.add_argument("--mode", choices=("prepare", "submit-approved"), default="prepare")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args(argv)

    load_dotenv(ROOT / ".env")
    configured = Path(os.getenv("SCHOLARSHIP_DB_PATH", "data/scholarships.db"))
    database = ScholarshipDatabase(configured if configured.is_absolute() else ROOT / configured)
    database.initialize()
    scholarship = database.get_scholarship(args.scholarship_id)
    if scholarship is None:
        parser.error(f"Scholarship id {args.scholarship_id} was not found.")
    source = _source_for_scholarship(scholarship, ROOT / "data" / "sources.yaml")
    if source is None:
        print("Autofill blocked: no configured source policy covers the application URL.")
        return 2
    profile_path = ROOT / "data" / "profile.yaml"
    from src.profile import load_profile

    profile = load_profile(profile_path if profile_path.exists() else ROOT / "data" / "profile.example.yaml")
    drafts = [item for item in database.list_drafts() if item.scholarship_id == scholarship.id]
    report = autofill_application(
        scholarship,
        profile,
        source=source,
        essay_text=_ready_essay_text(scholarship.id, drafts),
        submit_mode=args.mode == "submit-approved",
        drafts=drafts,
        headless=args.headless,
        manual_login_wait_seconds=300,
        review_wait_seconds=300 if args.mode == "prepare" else 0,
    )
    print(report.message)
    print(f"Log: {report.log_path}")
    return 0 if report.status != AutofillStatus.ERROR and (args.mode == "prepare" or report.submitted) else 2


if __name__ == "__main__":
    raise SystemExit(main())
