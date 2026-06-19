"""Streamlit dashboard skeleton for the local scholarship workflow."""

from __future__ import annotations

import os
import re
import sqlite3
from hashlib import sha1
from pathlib import Path

import streamlit as st

from src.approval import approval_risk_flags
from src.autopilot import run_autopilot
from src.autofill import AutofillReport, autofill_application, latest_autofill_report
from src.db import ScholarshipDatabase
from src.discovery import run_discovery, update_source_enabled
from src.drafter import DraftContextError, generate_and_save_draft, slugify
from src.export import (
    build_weekly_action_list,
    export_application_packet,
    export_approval_queue,
    export_csv_tracker,
    export_draft_packet,
    export_mav_weekly_checklist,
    export_quick_apply_queue,
    export_quick_apply_queue_markdown,
    export_weekly_action_list,
)
from src.finder import SourceFetchError, SourcePolicyError, import_manual_text, import_public_url
from src.mav_import import import_mav_opportunity
from src.models import DraftRecord, DraftStatus, Recommendation, ScholarshipRecord, ScholarshipStatus
from src.policy import AccessMode, SourceDefinition, load_source_catalog, source_matches_url
from src.profile import ProfileLoadError, load_profile
from src.source_adapters.search import build_search_provider


ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "data" / "profile.yaml"
EXAMPLE_PROFILE_PATH = ROOT / "data" / "profile.example.yaml"
DEFAULT_DB_PATH = ROOT / os.getenv("SCHOLARSHIP_DB_PATH", "data/scholarships.db")
SOURCES_PATH = ROOT / "data" / "sources.yaml"
EXPORTS_PATH = ROOT / "exports"

TAB_CONFIG = [
    ("Autopilot", None, None),
    ("Approval Queue", None, None),
    ("Discovery", None, None),
    ("Sources", None, None),
    ("New scholarships", ScholarshipStatus.NEW, None),
    ("Quick apply / no essay", None, Recommendation.QUICK_APPLY),
    ("Apply now", ScholarshipStatus.APPLY_NOW, None),
    ("Maybe", ScholarshipStatus.MAYBE, None),
    ("Needs manual review", ScholarshipStatus.MANUAL_REVIEW, None),
    ("Drafts ready", ScholarshipStatus.DRAFTS_READY, None),
    ("Needs documents", ScholarshipStatus.NEEDS_DOCUMENTS, None),
    ("Skipped", ScholarshipStatus.SKIPPED, None),
    ("Blocked/manual-only source", ScholarshipStatus.BLOCKED_SOURCE, None),
    ("Mav ScholarShop manual check", ScholarshipStatus.MAV_MANUAL_CHECK, None),
]


def _draft_prompt_controls(
    record: ScholarshipRecord,
    prompt: str,
    database: ScholarshipDatabase,
    *,
    key_prefix: str,
) -> None:
    if record.id is None:
        return
    digest = sha1(prompt.encode("utf-8")).hexdigest()[:10]
    key = f"{key_prefix}-{record.id}-{digest}"
    draft = database.get_draft_for_prompt(record.id, prompt)
    st.write(f"**Prompt:** {prompt}")
    if st.button("Generate draft", key=f"generate-{key}"):
        try:
            draft = generate_and_save_draft(record, prompt, database=database)
            st.success(f"Draft saved to {draft.path}")
        except (ValueError, OSError, DraftContextError) as exc:
            st.error(f"Draft generation stopped: {exc}")
    if draft:
        st.caption(f"Draft status: {draft.status.value} · {draft.path}")
        view_column, ready_column, input_column = st.columns(3)
        if view_column.button("View draft", key=f"view-{key}"):
            st.session_state[f"show-{key}"] = not st.session_state.get(f"show-{key}", False)
        if ready_column.button("Mark as ready to review", key=f"ready-{key}"):
            draft = database.update_draft_status(draft.id, DraftStatus.READY_TO_REVIEW)
            st.success("Marked ready to review.")
        if input_column.button("Mark as needs user input", key=f"input-{key}"):
            draft = database.update_draft_status(draft.id, DraftStatus.NEEDS_USER_INPUT)
            st.warning("Marked as needing user input.")
        if st.session_state.get(f"show-{key}"):
            try:
                st.markdown(draft.path.read_text(encoding="utf-8"))
            except OSError as exc:
                st.error(f"Could not read draft: {exc}")


def _record_card(
    record: ScholarshipRecord,
    database: ScholarshipDatabase,
    *,
    key_prefix: str,
) -> None:
    ranking = record.ranking
    score = f"{ranking.total_score:.1f}/100" if ranking else "Not ranked"
    recommendation = ranking.recommendation if ranking else "Needs ranking"
    deadline = record.deadline.isoformat() if record.deadline else "Unknown"
    amount = f"${record.amount:,.0f}" if record.amount is not None else "Unknown"
    effort = f"{record.effort_hours:.1f} hours" if record.effort_hours is not None else "Estimate pending"
    with st.container(border=True):
        st.subheader(record.name)
        st.caption(f"{record.provider or 'Unknown provider'} · {recommendation}")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Fit score", score)
        col2.metric("Deadline", deadline)
        col3.metric("Amount", amount)
        col4.metric("Effort", effort)
        st.write("**Required documents:**", ", ".join(record.required_documents) or "None listed")
        flags = [
            label for enabled, label in (
                (record.recommendation_required is True, "Recommendation required"),
                (record.fafsa_required is True, "FAFSA required"),
                (record.first_generation_required is True, "First-generation required"),
                (record.need_only is True, "Need-only"),
                (record.no_essay_quick_apply, "No essay / quick apply"),
            ) if enabled
        ]
        if flags:
            st.write("**Eligibility flags:**", " · ".join(flags))
        if record.application_url:
            st.link_button("Open application page", str(record.application_url))
        if ranking:
            for reason in ranking.explanation:
                st.write(f"- {reason}")
            for conflict in ranking.hard_conflicts:
                st.error(conflict)
        next_action = {
            ScholarshipStatus.NEW: "Review eligibility and ranking.",
            ScholarshipStatus.APPLY_NOW: "Prepare required materials and open the application.",
            ScholarshipStatus.DRAFTS_READY: "Review every draft and verify its facts.",
            ScholarshipStatus.NEEDS_DOCUMENTS: "Collect the missing documents.",
            ScholarshipStatus.SKIPPED: "No action unless circumstances change.",
            ScholarshipStatus.MAV_MANUAL_CHECK: "Open Mav ScholarShop manually and copy opportunity text here.",
            ScholarshipStatus.MAYBE: "Verify uncertain eligibility and expected value.",
            ScholarshipStatus.MANUAL_REVIEW: "Verify missing deadline, application URL, or extraction details.",
            ScholarshipStatus.BLOCKED_SOURCE: "Use manual import only if source policy permits it.",
            ScholarshipStatus.NEEDS_EDIT: "Revise the draft or application details before approval.",
        }[record.status]
        st.info(f"Next action: {next_action}")
        with st.expander("Essay drafts"):
            if record.essay_prompts:
                for prompt in record.essay_prompts:
                    _draft_prompt_controls(record, prompt, database, key_prefix=key_prefix)
                    st.divider()
            else:
                st.caption("No essay prompt was extracted. Add one below if the application has a response field.")
            custom_prompt = st.text_area(
                "Custom prompt",
                key=f"custom-prompt-{key_prefix}-{record.id}",
                placeholder="Paste a prompt that was not extracted…",
            )
            if custom_prompt.strip():
                _draft_prompt_controls(
                    record,
                    custom_prompt.strip(),
                    database,
                    key_prefix=f"{key_prefix}-custom",
                )
        _autofill_controls(record, database, key_prefix=key_prefix)
        if st.button("Export application packet", key=f"packet-{key_prefix}-{record.id}"):
            path = export_application_packet(record, database.list_drafts(), EXPORTS_PATH)
            st.success(f"Application packet saved to {path}")


def _source_for_application(record: ScholarshipRecord) -> SourceDefinition | None:
    if record.application_url is None:
        return None
    catalog = load_source_catalog(SOURCES_PATH)
    return next(
        (
            source for source in catalog.sources
            if source_matches_url(source, str(record.application_url))
        ),
        None,
    )


def _draft_answer_for_autofill(record: ScholarshipRecord, database: ScholarshipDatabase) -> str | None:
    if record.id is None:
        return None
    drafts = [draft for draft in database.list_drafts() if draft.scholarship_id == record.id]
    preferred = next((draft for draft in drafts if draft.status == DraftStatus.READY_TO_REVIEW), None)
    if preferred is None:
        return None
    try:
        markdown = preferred.path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"## Draft answer\s+(.+?)(?=\n## )", markdown, re.DOTALL)
    return match.group(1).strip() if match else None


def _autofill_controls(
    record: ScholarshipRecord,
    database: ScholarshipDatabase,
    *,
    key_prefix: str,
) -> None:
    if record.application_url is None:
        return
    key = f"autofill-{key_prefix}-{record.id}"
    source = _source_for_application(record)
    report = latest_autofill_report(record)
    session_report = st.session_state.get(f"report-{key}")
    if session_report:
        report = AutofillReport.model_validate(session_report)
    with st.expander("Safe browser autofill"):
        st.caption(
            "The browser fills only high-confidence fields, logs masked values, screenshots the result, "
            "and never submits in this dashboard workflow. Login/CAPTCHA/2FA remain manual."
        )
        if source is None:
            st.warning("No reviewed source policy covers this application URL. Add it to data/sources.yaml first.")
        if not record.approved_autofill:
            st.warning("Approve this scholarship for autofill in the Approval Queue first.")
        if st.button(
            "Open and autofill safely",
            key=f"open-{key}",
            disabled=source is None or source.access_mode == AccessMode.BLOCKED or not record.approved_autofill,
        ):
            st.info("A headed browser may remain open for up to five minutes for manual login and review.")
            with st.spinner("Waiting for the safe browser workflow…"):
                report = autofill_application(
                    record,
                    load_profile(PROFILE_PATH if PROFILE_PATH.exists() else EXAMPLE_PROFILE_PATH),
                    source=source,
                    essay_text=_draft_answer_for_autofill(record, database),
                    submit_mode=False,
                    drafts=[draft for draft in database.list_drafts() if draft.scholarship_id == record.id],
                    manual_login_wait_seconds=300,
                    review_wait_seconds=300,
                )
            st.session_state[f"report-{key}"] = report.model_dump(mode="json")
            st.success(report.message) if report.status.value == "completed" else st.warning(report.message)
        if report:
            log_column, screenshot_column, manual_column = st.columns(3)
            if log_column.button("View autofill log", key=f"log-{key}"):
                st.session_state[f"show-log-{key}"] = not st.session_state.get(f"show-log-{key}", False)
            if screenshot_column.button("View screenshot", key=f"shot-{key}"):
                st.session_state[f"show-shot-{key}"] = not st.session_state.get(f"show-shot-{key}", False)
            if manual_column.button("Manual fields needed", key=f"manual-{key}"):
                st.session_state[f"show-manual-{key}"] = not st.session_state.get(f"show-manual-{key}", False)
            if st.session_state.get(f"show-log-{key}"):
                st.json(report.model_dump(mode="json"))
            if st.session_state.get(f"show-shot-{key}"):
                if report.screenshot_path and report.screenshot_path.is_file():
                    st.image(str(report.screenshot_path), caption="Autofill review screenshot")
                else:
                    st.info("No screenshot is available for this run.")
            if st.session_state.get(f"show-manual-{key}"):
                if report.blockers:
                    for blocker in report.blockers:
                        st.warning(blocker)
                if report.manual_fields:
                    for field in report.manual_fields:
                        st.write(f"- {field.label}: {field.reason}")
                if not report.blockers and not report.manual_fields:
                    st.write("No manual fields were detected, but final review is still required.")


def _draft_review_card(draft: DraftRecord, database: ScholarshipDatabase, *, key_prefix: str) -> None:
    with st.container(border=True):
        st.subheader(draft.scholarship_name)
        st.write(f"**Prompt:** {draft.prompt}")
        st.code(str(draft.path), language=None)
        st.write("**Facts used:**")
        for fact in draft.facts_used:
            st.write(f"- {fact}")
        st.write("**Missing input:**")
        if draft.missing_user_input:
            for item in draft.missing_user_input:
                st.warning(item)
        else:
            st.write("None currently identified.")
        next_action = (
            "Supply and verify the missing details before editing the answer."
            if draft.status == DraftStatus.NEEDS_USER_INPUT
            else "Review the wording, verify every fact, and paste manually only when satisfied."
        )
        st.info(f"Next action: {next_action}")
        key = f"draft-review-{key_prefix}-{draft.id}"
        view_column, ready_column, input_column = st.columns(3)
        if view_column.button("View draft", key=f"view-{key}"):
            st.session_state[f"show-{key}"] = not st.session_state.get(f"show-{key}", False)
        if ready_column.button("Mark as ready to review", key=f"ready-{key}"):
            database.update_draft_status(draft.id, DraftStatus.READY_TO_REVIEW)
            st.success("Marked ready to review.")
        if input_column.button("Mark as needs user input", key=f"input-{key}"):
            database.update_draft_status(draft.id, DraftStatus.NEEDS_USER_INPUT)
            st.warning("Marked as needing user input.")
        if st.session_state.get(f"show-{key}"):
            try:
                st.markdown(draft.path.read_text(encoding="utf-8"))
            except OSError as exc:
                st.error(f"Could not read draft: {exc}")


def _empty_state(label: str) -> None:
    st.info(f"No items in {label.lower()} yet.")


def _import_forms(database: ScholarshipDatabase, profile) -> None:
    catalog = load_source_catalog(SOURCES_PATH)
    manual_sources = [source for source in catalog.sources if source.access_mode != AccessMode.BLOCKED]
    public_sources = [source for source in catalog.sources if source.access_mode == AccessMode.PUBLIC_ALLOWED]

    with st.expander("Import scholarships", expanded=True):
        manual_column, public_column = st.columns(2)
        with manual_column:
            st.subheader("Paste opportunity text")
            source_labels = ["Unlisted/manual copy"] + [source.name for source in manual_sources]
            with st.form("manual_import_form", clear_on_submit=True):
                selected_label = st.selectbox("Source", source_labels)
                raw_text = st.text_area(
                    "Scholarship text",
                    height=230,
                    placeholder="Paste one complete scholarship opportunity here…",
                )
                manual_submit = st.form_submit_button("Extract and import")
            if manual_submit:
                selected_source = next(
                    (source for source in manual_sources if source.name == selected_label), None
                )
                try:
                    record = import_manual_text(
                        raw_text, database=database, profile=profile, source=selected_source
                    )
                    st.success(
                        f"Imported {record.name}: "
                        f"{record.ranking.recommendation if record.ranking else 'Needs ranking'}."
                    )
                except (ValueError, SourcePolicyError, sqlite3.IntegrityError) as exc:
                    st.error(f"Import stopped: {exc}")

        with public_column:
            st.subheader("Import approved public URL")
            if not public_sources:
                st.info(
                    "No source is currently marked public_allowed. Review Terms/robots and enable a "
                    "specific source in data/sources.yaml before fetching."
                )
                with st.form("public_import_form_disabled"):
                    st.selectbox("Approved source", ["No approved source configured"], disabled=True)
                    st.text_input("Public scholarship URL", disabled=True)
                    st.form_submit_button("Fetch, extract, and import", disabled=True)
            else:
                with st.form("public_import_form"):
                    public_label = st.selectbox("Approved source", [source.name for source in public_sources])
                    public_url = st.text_input("Public scholarship URL")
                    public_submit = st.form_submit_button("Fetch, extract, and import")
                if public_submit:
                    selected_source = next(source for source in public_sources if source.name == public_label)
                    try:
                        record = import_public_url(
                            public_url,
                            source=selected_source,
                            database=database,
                            profile=profile,
                        )
                        st.success(
                            f"Imported {record.name}: "
                            f"{record.ranking.recommendation if record.ranking else 'Needs ranking'}."
                        )
                    except (
                        ValueError,
                        SourcePolicyError,
                        SourceFetchError,
                        sqlite3.IntegrityError,
                    ) as exc:
                        st.error(f"Import stopped: {exc}")


def _mav_manual_helper(database: ScholarshipDatabase, profile) -> None:
    st.subheader("Mav ScholarShop manual helper")
    st.warning("Login and final submission stay manual. This app never requests or stores portal credentials.")
    with st.form("mav_manual_import_form", clear_on_submit=True):
        opportunity_text = st.text_area(
            "Paste one Mav ScholarShop opportunity",
            height=260,
            placeholder="Copy the full opportunity text after logging in yourself…",
        )
        submitted = st.form_submit_button("Parse, rank, and import")
    if submitted:
        try:
            imported = import_mav_opportunity(
                opportunity_text,
                database=database,
                profile=profile,
            )
            st.success(
                f"Imported {imported.name}: "
                f"{imported.ranking.recommendation if imported.ranking else 'Needs ranking'}."
            )
            _record_card(imported, database, key_prefix=f"mav-imported-{imported.id}")
        except (ValueError, SourcePolicyError, sqlite3.IntegrityError) as exc:
            st.error(f"Mav import stopped: {exc}")

    st.markdown(
        """
        **Weekly checklist**

        1. Log into Mav ScholarShop manually.
        2. Open Task List / Recommended Opportunities.
        3. Copy/paste opportunity text.
        4. Import into app.
        5. Generate drafts.
        6. Submit manually after review.
        """
    )


def _export_controls(records: list[ScholarshipRecord], drafts: list[DraftRecord]) -> None:
    with st.sidebar.expander("Exports"):
        if st.button("Export CSV tracker", key="export-tracker"):
            st.success(f"Saved {export_csv_tracker(records, EXPORTS_PATH)}")
        if st.button("Export weekly action list", key="export-weekly"):
            st.success(f"Saved {export_weekly_action_list(records, drafts, EXPORTS_PATH)}")
        if st.button("Export draft packet", key="export-drafts"):
            st.success(f"Saved {export_draft_packet(drafts, EXPORTS_PATH)}")
        if st.button("Export quick-apply queue", key="export-quick"):
            st.success(f"Saved {export_quick_apply_queue(records, EXPORTS_PATH)}")
        if st.button("Export approval queue", key="export-approval"):
            st.success(f"Saved {export_approval_queue(records, drafts, EXPORTS_PATH)}")
        if st.button("Export quick-apply Markdown", key="export-quick-md"):
            st.success(f"Saved {export_quick_apply_queue_markdown(records, EXPORTS_PATH)}")
        if st.button("Export Mav checklist", key="export-mav"):
            st.success(f"Saved {export_mav_weekly_checklist(EXPORTS_PATH)}")


def _discovery_tab(database: ScholarshipDatabase, profile) -> None:
    st.subheader("Automated scholarship discovery")
    st.caption("Runs policy-approved curated pages, configured RSS feeds, and an optional search API.")
    if st.button("Run discovery now", key="run-discovery-now"):
        with st.spinner("Discovering, deduplicating, extracting, and ranking…"):
            try:
                result = run_discovery(database, profile)
                st.session_state["latest-discovery-result"] = result.model_dump(mode="json")
                st.success(f"Discovery complete: {result.stats.new} new, {result.stats.duplicates} duplicates.")
            except Exception as exc:
                st.error(f"Discovery stopped safely: {exc}")
    latest = database.latest_discovery_run()
    if latest is None:
        st.info("Discovery has not run yet.")
    else:
        stats = latest["stats"]
        columns = st.columns(5)
        columns[0].metric("Found", stats["found"])
        columns[1].metric("New", stats["new"])
        columns[2].metric("Duplicates", stats["duplicates"])
        columns[3].metric("Skipped/blocked", stats["skipped_blocked"])
        columns[4].metric("Errors", stats["errors"])
        st.write(f"**Last run:** {latest['finished_at']}")
        st.write(f"**Search API:** {latest['search_status']}")
        if latest["warnings"]:
            with st.expander("Warnings"):
                for warning in latest["warnings"]:
                    st.warning(warning)
        if latest["errors"]:
            with st.expander("Errors"):
                for error in latest["errors"]:
                    st.error(error)
        new_records = [
            record for item_id in latest["new_scholarship_ids"]
            if (record := database.get_scholarship(item_id)) is not None
        ]
        new_records.sort(key=lambda item: item.ranking.total_score if item.ranking else -1, reverse=True)
        st.write("**Top newly found scholarships**")
        if new_records:
            for record in new_records[:5]:
                st.write(
                    f"- {record.name} — {record.ranking.recommendation.value if record.ranking else 'Unranked'} "
                    f"({record.ranking.total_score:.1f}/100)" if record.ranking else f"- {record.name} — Unranked"
                )
        else:
            st.write("- None in the latest run.")
    provider = build_search_provider()
    if not provider.enabled:
        st.info(provider.status)


def _autopilot_tab(database: ScholarshipDatabase, profile) -> None:
    st.subheader("Scholarship Autopilot")
    st.caption(
        "Runs policy-approved discovery, extraction, deduplication, ranking, drafting, and queue exports. "
        "It does not open forms or submit applications."
    )
    if st.button("Run Autopilot Now", key="run-autopilot-now", type="primary"):
        with st.spinner("Running the full scholarship pipeline…"):
            try:
                result = run_autopilot(database, profile)
                st.session_state["autopilot-flash"] = (
                    f"Autopilot finished: {result.stats.new} new and {result.stats.drafts_generated} drafts generated."
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Autopilot stopped safely: {exc}")
    if message := st.session_state.pop("autopilot-flash", None):
        st.success(message)
    latest = database.latest_autopilot_run()
    if latest is None:
        st.info("Autopilot has not run yet.")
        return
    stats = latest["stats"]
    first = st.columns(4)
    first[0].metric("Scholarships found", stats["found"])
    first[1].metric("New scholarships", stats["new"])
    first[2].metric("Duplicates", stats["duplicates"])
    first[3].metric("Drafts generated", stats["drafts_generated"])
    second = st.columns(4)
    second[0].metric("Quick Apply ready", stats["quick_apply_ready"])
    second[1].metric("Apply Now ready", stats["apply_now_ready"])
    second[2].metric("Blocked/manual", stats["blocked_manual"])
    second[3].metric("Errors", stats["errors"])
    st.write(f"**Last run:** {latest['finished_at']}")
    if latest["warnings"]:
        with st.expander("Warnings"):
            for warning in latest["warnings"]:
                st.warning(warning)
    if latest["errors"]:
        with st.expander("Errors"):
            for error in latest["errors"]:
                st.error(error)


def _draft_preview(draft: DraftRecord) -> str:
    try:
        markdown = draft.path.read_text(encoding="utf-8")
    except OSError:
        return "[Draft file unavailable]"
    match = re.search(r"## Draft answer\s+(.+?)(?=\n## )", markdown, re.DOTALL)
    text = match.group(1).strip() if match else markdown
    return text[:700] + ("…" if len(text) > 700 else "")


def _approval_queue_card(record: ScholarshipRecord, database: ScholarshipDatabase) -> None:
    if record.id is None:
        return
    drafts = [draft for draft in database.list_drafts() if draft.scholarship_id == record.id]
    source = _source_for_application(record)
    risks = approval_risk_flags(record, source=source, drafts=drafts)
    recommendation = record.ranking.recommendation.value if record.ranking else "Unranked"
    with st.container(border=True):
        st.subheader(record.name)
        st.caption(f"{recommendation} · {record.source_url or 'Unknown source'}")
        columns = st.columns(4)
        columns[0].metric("Amount", f"${record.amount:,.0f}" if record.amount is not None else "Unknown")
        columns[1].metric("Deadline", record.deadline.isoformat() if record.deadline else "Unknown")
        columns[2].metric("Fit score", f"{record.ranking.total_score:.1f}/100" if record.ranking else "Unranked")
        columns[3].metric("Drafts", len(drafts))
        if drafts:
            st.write("**Draft preview**")
            st.info(_draft_preview(drafts[0]))
        facts = list(dict.fromkeys(value for draft in drafts for value in draft.facts_used))
        claims = list(dict.fromkeys(value for draft in drafts for value in draft.claims_to_verify))
        missing = list(dict.fromkeys(value for draft in drafts for value in draft.missing_user_input))
        st.write("**Facts used:**", "; ".join(facts) or "None recorded")
        st.write("**Claims to verify:**", "; ".join(claims) or "None recorded")
        st.write("**Missing user input:**", "; ".join(missing) or "None recorded")
        st.write("**Documents needed:**", ", ".join(record.required_documents) or "None extracted")
        st.write("**Risk flags:**")
        if risks:
            for risk in risks:
                st.warning(risk)
        else:
            st.success("Stored-data checks pass. The browser must still recheck the live page.")
        row = st.columns(4)
        if row[0].button("Approve for autofill", key=f"approve-fill-{record.id}"):
            database.update_approval(record.id, approved_autofill=True)
            st.rerun()
        preapproval_risks = [risk for risk in risks if risk != "Safe-submit approval has not been granted."]
        if row[1].button(
            "Approve for safe submit",
            key=f"approve-submit-{record.id}",
            disabled=bool(preapproval_risks),
            help="Enabled only after every stored-data and source-policy safety check passes.",
        ):
            database.update_approval(record.id, approved_autofill=True, pre_approved_submit=True)
            st.rerun()
        if row[2].button("Needs edit", key=f"needs-edit-{record.id}"):
            database.update_scholarship_status(record.id, ScholarshipStatus.NEEDS_EDIT.value)
            st.rerun()
        if row[3].button("Skip", key=f"approval-skip-{record.id}"):
            database.update_scholarship_status(record.id, ScholarshipStatus.SKIPPED.value)
            st.rerun()
        links = st.columns(2)
        if record.source_url:
            links[0].link_button("Open source page", str(record.source_url))
        if links[1].button("Export packet", key=f"approval-packet-{record.id}"):
            st.success(f"Saved {export_application_packet(record, database.list_drafts(), EXPORTS_PATH)}")


def _approval_queue_tab(records: list[ScholarshipRecord], database: ScholarshipDatabase) -> None:
    st.subheader("Approval Queue")
    st.caption("Approvals are explicit and reversible in the database; submit-approved mode still rechecks every risk.")
    matching = [
        record for record in records
        if record.ranking
        and record.ranking.recommendation in {Recommendation.APPLY, Recommendation.QUICK_APPLY}
        and record.status != ScholarshipStatus.SKIPPED
    ]
    matching.sort(
        key=lambda record: (
            record.ranking.recommendation != Recommendation.QUICK_APPLY,
            -record.ranking.total_score,
        )
    )
    if not matching:
        _empty_state("Approval Queue")
        return
    for record in matching:
        _approval_queue_card(record, database)


def _sources_tab(database: ScholarshipDatabase, *, blocked_only: bool = False) -> None:
    catalog = load_source_catalog(SOURCES_PATH)
    states = database.source_states()
    selected = [
        source for source in catalog.sources
        if not blocked_only or source.access_mode in {AccessMode.MANUAL_ONLY, AccessMode.BLOCKED}
    ]
    if not selected:
        st.info("No sources in this category.")
        return
    for source in selected:
        view_key = "blocked" if blocked_only else "all"
        state = states.get(source.name, {})
        with st.container(border=True):
            st.subheader(source.name)
            st.write(f"**Category:** {source.category}")
            st.write(f"**Access mode:** {source.access_mode.value}")
            st.write(f"**Submit automation explicitly allowed:** {'Yes' if source.allow_submit_automation else 'No'}")
            st.write(f"**URL:** {source.url}")
            if source.rss_url:
                st.write(f"**RSS:** {source.rss_url}")
            st.write(f"**Last fetched:** {state.get('last_fetched') or 'Never'}")
            st.write(f"**Last status:** {state.get('last_status') or 'Not run'}")
            if state.get("last_error"):
                st.error(state["last_error"])
            enabled = st.checkbox(
                "Enabled",
                value=source.enabled,
                key=f"source-enabled-{view_key}-{slugify(source.name, fallback='source')}",
            )
            if enabled != source.enabled and st.button(
                "Save source setting",
                key=f"save-source-{view_key}-{slugify(source.name, fallback='source')}",
            ):
                update_source_enabled(SOURCES_PATH, source.name, enabled)
                st.success("Source setting saved. Access policy remains unchanged.")
                st.rerun()
            st.caption(source.notes)


def main() -> None:
    st.set_page_config(page_title="Scholarship Copilot", page_icon="🎓", layout="wide")
    st.title("Scholarship Copilot")
    st.caption("Local-first tracking, transparent ranking, and human-reviewed applications.")

    profile_path = PROFILE_PATH if PROFILE_PATH.exists() else EXAMPLE_PROFILE_PATH
    try:
        profile = load_profile(profile_path)
    except ProfileLoadError as exc:
        st.error(str(exc))
        st.stop()

    database = ScholarshipDatabase(DEFAULT_DB_PATH)
    database.initialize()
    _import_forms(database, profile)
    records = database.list_scholarships()
    drafts = database.list_drafts()

    with st.sidebar:
        st.header(profile.preferred_name or profile.full_name)
        primary_education = profile.education[0]
        st.write(primary_education.school)
        st.write(f"{primary_education.major} · GPA {primary_education.gpa or 'not set'}")
        if profile_path == EXAMPLE_PROFILE_PATH:
            st.warning("Using profile.example.yaml. Copy it to data/profile.yaml and replace placeholders/private fields.")
        st.caption(f"Database: {DEFAULT_DB_PATH}")
        st.metric("Tracked scholarships", len(records))
    _export_controls(records, drafts)

    with st.expander("Weekly action list", expanded=True):
        st.markdown(build_weekly_action_list(records, drafts))

    tabs = st.tabs([label for label, _, _ in TAB_CONFIG])
    for tab, (label, status, recommendation) in zip(tabs, TAB_CONFIG, strict=True):
        with tab:
            if label == "Autopilot":
                _autopilot_tab(database, profile)
                continue
            if label == "Approval Queue":
                _approval_queue_tab(records, database)
                continue
            if label == "Discovery":
                _discovery_tab(database, profile)
                continue
            if label == "Sources":
                _sources_tab(database)
                continue
            if status == ScholarshipStatus.BLOCKED_SOURCE:
                _sources_tab(database, blocked_only=True)
                continue
            if status == ScholarshipStatus.DRAFTS_READY:
                if drafts:
                    for draft in drafts:
                        _draft_review_card(draft, database, key_prefix="drafts-tab")
                else:
                    _empty_state(label)
                continue
            if status == ScholarshipStatus.MAV_MANUAL_CHECK:
                matching = [record for record in records if record.source_category == "uta_manual"]
            elif recommendation is not None:
                matching = [
                    record for record in records
                    if record.ranking and record.ranking.recommendation == recommendation
                ]
            else:
                matching = [record for record in records if record.status == status]
            if matching:
                for record in matching:
                    _record_card(record, database, key_prefix=slugify(label, fallback="tab"))
            else:
                _empty_state(label)
            if status == ScholarshipStatus.MAV_MANUAL_CHECK:
                _mav_manual_helper(database, profile)

    st.divider()
    st.caption("No application is submitted from this dashboard. All generated writing must be human-reviewed.")


if __name__ == "__main__":
    main()
