"""Fail-closed source-access and application-submission policy."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from urllib.parse import urlparse

import yaml
from pydantic import Field, HttpUrl

from src.models import Scholarship, StrictModel


class AccessMode(StrEnum):
    PUBLIC_ALLOWED = "public_allowed"
    MANUAL_ONLY = "manual_only"
    BLOCKED = "blocked"


class PolicyAction(StrEnum):
    FETCH = "fetch"
    MANUAL_IMPORT = "manual_import"
    AUTOFILL = "autofill"
    SUBMIT = "submit"


class SourceDefinition(StrictModel):
    name: str
    url: HttpUrl
    category: str
    access_mode: AccessMode
    notes: str
    enabled: bool = True
    rss_url: HttpUrl | None = None
    allow_submit_automation: bool = False


class SourceCatalog(StrictModel):
    sources: list[SourceDefinition] = Field(default_factory=list)


class PolicyDecision(StrictModel):
    allowed: bool
    reason: str


def load_source_catalog(path: str | Path) -> SourceCatalog:
    """Load and validate source policy configuration."""

    source_path = Path(path)
    raw = yaml.safe_load(source_path.read_text(encoding="utf-8")) or {"sources": []}
    return SourceCatalog.model_validate(raw)


def check_source_policy(source: SourceDefinition, action: PolicyAction) -> PolicyDecision:
    """Decide whether a configured source may be fetched or manually imported."""

    if source.access_mode == AccessMode.BLOCKED:
        return PolicyDecision(
            allowed=False,
            reason=f"{source.name} is blocked; fetching and automated import are disabled. {source.notes}",
        )
    if source.access_mode == AccessMode.MANUAL_ONLY:
        if action == PolicyAction.MANUAL_IMPORT:
            return PolicyDecision(
                allowed=True,
                reason=f"{source.name} permits manual paste/import only; no network request will be made.",
            )
        if action == PolicyAction.AUTOFILL:
            return PolicyDecision(
                allowed=True,
                reason=f"{source.name} permits assisted autofill with manual login, review, and submission.",
            )
        return PolicyDecision(
            allowed=False,
            reason=f"{source.name} is manual-only; fetching and automated submission are disabled.",
        )
    if action == PolicyAction.SUBMIT and not source.allow_submit_automation:
        return PolicyDecision(
            allowed=False,
            reason=f"{source.name} does not explicitly allow submit automation; prepare-only autofill remains available.",
        )
    if action in {PolicyAction.FETCH, PolicyAction.AUTOFILL, PolicyAction.SUBMIT}:
        return PolicyDecision(
            allowed=True,
            reason=f"{source.name} is configured as public_allowed for policy-checked {action.value}.",
        )
    return PolicyDecision(
        allowed=True,
        reason=f"{source.name} also permits local manual import.",
    )


def source_matches_url(source: SourceDefinition, url: str) -> bool:
    """Limit public imports to the configured URL or a configured path subtree."""

    target = urlparse(url)
    configured_urls = [source.url, *([source.rss_url] if source.rss_url else [])]
    for configured_url in configured_urls:
        configured = urlparse(str(configured_url))
        base_path = configured.path.rstrip("/")
        if (
            target.scheme in {"http", "https"}
            and target.scheme == configured.scheme
            and target.netloc.lower() == configured.netloc.lower()
            and (target.path == base_path or target.path.startswith(f"{base_path}/"))
        ):
            return True
    return False


def check_unknown_public_landing(url: str) -> PolicyDecision:
    """Allow one public landing-page read for a search result; never imply form automation approval."""

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return PolicyDecision(allowed=False, reason="Search result is not a valid public HTTP(S) URL.")
    return PolicyDecision(
        allowed=True,
        reason="Unconfigured search result may be read once as a public landing page only; links/forms are not followed.",
    )


def submission_allowed(scholarship: Scholarship, *, site_allowed: bool) -> bool:
    """Require both explicit per-opportunity approval and an allowed site."""

    return site_allowed and scholarship.pre_approved_submit


def check_submission_policy(
    scholarship: Scholarship,
    *,
    source: SourceDefinition,
    submit_mode: bool,
    blockers: list[str] | tuple[str, ...] = (),
) -> PolicyDecision:
    """Require explicit mode, scholarship approval, allowed source, and a blocker-free page."""

    if not submit_mode:
        return PolicyDecision(allowed=False, reason="Submit mode was not explicitly requested; final submit is disabled.")
    if not scholarship.pre_approved_submit:
        return PolicyDecision(
            allowed=False,
            reason="Scholarship is not marked pre_approved_submit; final submit is disabled.",
        )
    source_decision = check_source_policy(source, PolicyAction.SUBMIT)
    if not source_decision.allowed:
        return source_decision
    if blockers:
        return PolicyDecision(
            allowed=False,
            reason="Final submit is blocked because manual protection is present: " + ", ".join(blockers),
        )
    return PolicyDecision(
        allowed=True,
        reason="Explicit submit mode, pre-approval, allowed source, and blocker-free page are all confirmed.",
    )
