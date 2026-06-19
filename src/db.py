"""Small SQLite repository with explicit schema creation and JSON storage."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.models import DraftRecord, DraftStatus, RankingResult, Scholarship, ScholarshipRecord


SCHEMA = """
CREATE TABLE IF NOT EXISTS scholarships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    provider TEXT,
    amount REAL,
    deadline TEXT,
    eligibility_json TEXT NOT NULL DEFAULT '[]',
    location_restrictions_json TEXT NOT NULL DEFAULT '[]',
    school_restrictions_json TEXT NOT NULL DEFAULT '[]',
    major_restrictions_json TEXT NOT NULL DEFAULT '[]',
    essay_prompts_json TEXT NOT NULL DEFAULT '[]',
    required_documents_json TEXT NOT NULL DEFAULT '[]',
    recommendation_required INTEGER,
    fafsa_required INTEGER,
    first_generation_required INTEGER,
    need_only INTEGER,
    citizenship_residency_requirements_json TEXT NOT NULL DEFAULT '[]',
    no_essay_quick_apply INTEGER NOT NULL DEFAULT 0,
    manual_overrides_json TEXT NOT NULL DEFAULT '[]',
    application_url TEXT,
    source_url TEXT,
    source_type TEXT NOT NULL DEFAULT 'manual',
    source_category TEXT,
    competition_level TEXT,
    effort_hours REAL,
    status TEXT NOT NULL DEFAULT 'new',
    pre_approved_submit INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(name, provider, application_url)
);

CREATE TABLE IF NOT EXISTS rankings (
    scholarship_id INTEGER PRIMARY KEY,
    total_score REAL NOT NULL,
    recommendation TEXT NOT NULL,
    explanation_json TEXT NOT NULL,
    hard_conflicts_json TEXT NOT NULL,
    breakdown_json TEXT NOT NULL,
    ranked_at TEXT NOT NULL,
    FOREIGN KEY(scholarship_id) REFERENCES scholarships(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scholarship_id INTEGER NOT NULL,
    prompt TEXT NOT NULL,
    path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    story_angle TEXT NOT NULL,
    facts_used_json TEXT NOT NULL DEFAULT '[]',
    claims_to_verify_json TEXT NOT NULL DEFAULT '[]',
    missing_user_input_json TEXT NOT NULL DEFAULT '[]',
    why_angle_fits TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(scholarship_id) REFERENCES scholarships(id) ON DELETE CASCADE,
    UNIQUE(scholarship_id, prompt)
);

CREATE TABLE IF NOT EXISTS scholarship_sources (
    scholarship_id INTEGER NOT NULL,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    PRIMARY KEY (scholarship_id, source_name, source_url),
    FOREIGN KEY(scholarship_id) REFERENCES scholarships(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS discovery_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    stats_json TEXT NOT NULL,
    errors_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    new_scholarship_ids_json TEXT NOT NULL,
    search_status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_state (
    source_name TEXT PRIMARY KEY,
    last_fetched TEXT,
    last_error TEXT,
    last_status TEXT
);

CREATE TABLE IF NOT EXISTS autopilot_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    stats_json TEXT NOT NULL,
    errors_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    new_scholarship_ids_json TEXT NOT NULL,
    draft_ids_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scholarships_status ON scholarships(status);
CREATE INDEX IF NOT EXISTS idx_scholarships_deadline ON scholarships(deadline);
CREATE INDEX IF NOT EXISTS idx_rankings_score ON rankings(total_score DESC);
CREATE INDEX IF NOT EXISTS idx_drafts_status ON drafts(status);
CREATE INDEX IF NOT EXISTS idx_scholarship_sources_id ON scholarship_sources(scholarship_id);
"""

JSON_FIELDS = (
    "eligibility",
    "location_restrictions",
    "school_restrictions",
    "major_restrictions",
    "essay_prompts",
    "required_documents",
    "citizenship_residency_requirements",
    "manual_overrides",
)

BOOL_FIELDS = (
    "recommendation_required",
    "fafsa_required",
    "first_generation_required",
    "need_only",
    "no_essay_quick_apply",
    "pre_approved_submit",
    "approved_autofill",
)

COLUMN_MIGRATIONS = {
    "recommendation_required": "INTEGER",
    "fafsa_required": "INTEGER",
    "first_generation_required": "INTEGER",
    "need_only": "INTEGER",
    "citizenship_residency_requirements_json": "TEXT NOT NULL DEFAULT '[]'",
    "no_essay_quick_apply": "INTEGER NOT NULL DEFAULT 0",
    "manual_overrides_json": "TEXT NOT NULL DEFAULT '[]'",
    "source_category": "TEXT",
    "approved_autofill": "INTEGER NOT NULL DEFAULT 0",
}


class ScholarshipDatabase:
    def __init__(self, path: str | Path = "data/scholarships.db") -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            existing = {
                row["name"] for row in connection.execute("PRAGMA table_info(scholarships)")
            }
            for column, definition in COLUMN_MIGRATIONS.items():
                if column not in existing:
                    connection.execute(f"ALTER TABLE scholarships ADD COLUMN {column} {definition}")

    def add_scholarship(self, scholarship: Scholarship) -> int:
        data = scholarship.model_dump(mode="json", exclude={"id"})
        columns: list[str] = []
        values: list[object] = []
        for key, value in data.items():
            column = f"{key}_json" if key in JSON_FIELDS else key
            columns.append(column)
            if key in JSON_FIELDS:
                value = json.dumps(value)
            elif key in BOOL_FIELDS:
                value = None if value is None else int(value)
            values.append(value)
        placeholders = ", ".join("?" for _ in values)
        sql = f"INSERT INTO scholarships ({', '.join(columns)}) VALUES ({placeholders})"
        with self.connect() as connection:
            cursor = connection.execute(sql, values)
            return int(cursor.lastrowid)

    @staticmethod
    def normalize_name(name: str) -> str:
        value = re.sub(r"\b(?:19|20)\d{2}\b", "", name.lower())
        value = re.sub(r"\b(the|scholarship|award|fund)\b", " ", value)
        return re.sub(r"[^a-z0-9]+", " ", value).strip()

    @staticmethod
    def normalize_url(url: str | None) -> str:
        if not url:
            return ""
        parts = urlsplit(url)
        host = parts.netloc.lower().removeprefix("www.")
        query = urlencode([
            (key, value) for key, value in parse_qsl(parts.query)
            if not key.lower().startswith("utm_") and key.lower() not in {"ref", "source"}
        ])
        return urlunsplit((parts.scheme.lower(), host, parts.path.rstrip("/"), query, ""))

    def add_or_merge_scholarship(
        self,
        scholarship: Scholarship,
        *,
        source_name: str,
        source_url: str,
    ) -> tuple[int, bool]:
        """Deduplicate by normalized name/URL, merge missing data, and retain provenance."""

        normalized_name = self.normalize_name(scholarship.name)
        urls = {
            self.normalize_url(str(scholarship.application_url or "")),
            self.normalize_url(str(scholarship.source_url or "")),
        } - {""}
        duplicate = None
        for existing in self.list_scholarships():
            existing_urls = {
                self.normalize_url(str(existing.application_url or "")),
                self.normalize_url(str(existing.source_url or "")),
            } - {""}
            if normalized_name == self.normalize_name(existing.name) or urls.intersection(existing_urls):
                duplicate = existing
                break
        if duplicate is None:
            scholarship_id = self.add_scholarship(scholarship)
            self.add_source_reference(scholarship_id, source_name, source_url)
            return scholarship_id, True

        list_fields = set(JSON_FIELDS)
        existing_data = duplicate.model_dump(exclude={"ranking"})
        incoming_data = scholarship.model_dump()
        merged: dict[str, object] = {}
        for key, current in existing_data.items():
            if key == "id":
                continue
            incoming = incoming_data.get(key)
            if key in list_fields:
                merged[key] = list(dict.fromkeys([*(current or []), *(incoming or [])]))
            elif current is None or current == "":
                merged[key] = incoming
            else:
                merged[key] = current
        from datetime import datetime

        merged["updated_at"] = datetime.now()
        self.update_scholarship(Scholarship.model_validate({"id": duplicate.id, **merged}))
        self.add_source_reference(duplicate.id, source_name, source_url)
        return duplicate.id, False

    def update_scholarship(self, scholarship: Scholarship) -> None:
        if scholarship.id is None:
            raise ValueError("Scholarship id is required for update.")
        data = scholarship.model_dump(mode="json", exclude={"id"})
        assignments: list[str] = []
        values: list[object] = []
        for key, value in data.items():
            column = f"{key}_json" if key in JSON_FIELDS else key
            assignments.append(f"{column} = ?")
            if key in JSON_FIELDS:
                value = json.dumps(value)
            elif key in BOOL_FIELDS:
                value = None if value is None else int(value)
            values.append(value)
        values.append(scholarship.id)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE scholarships SET {', '.join(assignments)} WHERE id = ?", values
            )

    def update_scholarship_status(self, scholarship_id: int, status: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE scholarships SET status = ?, updated_at = datetime('now') WHERE id = ?",
                (status, scholarship_id),
            )

    def update_approval(
        self,
        scholarship_id: int,
        *,
        approved_autofill: bool | None = None,
        pre_approved_submit: bool | None = None,
    ) -> None:
        assignments: list[str] = []
        values: list[object] = []
        if approved_autofill is not None:
            assignments.append("approved_autofill = ?")
            values.append(int(approved_autofill))
        if pre_approved_submit is not None:
            assignments.append("pre_approved_submit = ?")
            values.append(int(pre_approved_submit))
        if not assignments:
            return
        assignments.append("updated_at = datetime('now')")
        values.append(scholarship_id)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE scholarships SET {', '.join(assignments)} WHERE id = ?",
                values,
            )

    def add_source_reference(self, scholarship_id: int, source_name: str, source_url: str) -> None:
        from datetime import datetime

        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO scholarship_sources
                    (scholarship_id, source_name, source_url, discovered_at)
                VALUES (?, ?, ?, ?)
                """,
                (scholarship_id, source_name, source_url, datetime.now().isoformat()),
            )

    def get_source_references(self, scholarship_id: int) -> list[dict[str, str]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT source_name, source_url, discovered_at FROM scholarship_sources WHERE scholarship_id = ?",
                (scholarship_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_ranking(self, ranking: RankingResult) -> None:
        if ranking.scholarship_id is None:
            raise ValueError("A ranking must have scholarship_id before persistence.")
        data = ranking.model_dump(mode="json")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO rankings (
                    scholarship_id, total_score, recommendation,
                    explanation_json, hard_conflicts_json, breakdown_json, ranked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scholarship_id) DO UPDATE SET
                    total_score=excluded.total_score,
                    recommendation=excluded.recommendation,
                    explanation_json=excluded.explanation_json,
                    hard_conflicts_json=excluded.hard_conflicts_json,
                    breakdown_json=excluded.breakdown_json,
                    ranked_at=excluded.ranked_at
                """,
                (
                    data["scholarship_id"],
                    data["total_score"],
                    data["recommendation"],
                    json.dumps(data["explanation"]),
                    json.dumps(data["hard_conflicts"]),
                    json.dumps(data["breakdown"]),
                    data["ranked_at"],
                ),
            )

    def save_discovery_run(self, result: dict[str, object]) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO discovery_runs (
                    started_at, finished_at, stats_json, errors_json, warnings_json,
                    new_scholarship_ids_json, search_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result["started_at"], result["finished_at"], json.dumps(result["stats"]),
                    json.dumps(result["errors"]), json.dumps(result["warnings"]),
                    json.dumps(result["new_scholarship_ids"]), result["search_status"],
                ),
            )
            return int(cursor.lastrowid)

    def latest_discovery_run(self) -> dict[str, object] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM discovery_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "stats": json.loads(row["stats_json"]),
            "errors": json.loads(row["errors_json"]),
            "warnings": json.loads(row["warnings_json"]),
            "new_scholarship_ids": json.loads(row["new_scholarship_ids_json"]),
            "search_status": row["search_status"],
        }

    def save_autopilot_run(self, result: dict[str, object]) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO autopilot_runs (
                    started_at, finished_at, stats_json, errors_json, warnings_json,
                    new_scholarship_ids_json, draft_ids_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result["started_at"], result["finished_at"], json.dumps(result["stats"]),
                    json.dumps(result["errors"]), json.dumps(result["warnings"]),
                    json.dumps(result["new_scholarship_ids"]), json.dumps(result["draft_ids"]),
                ),
            )
            return int(cursor.lastrowid)

    def latest_autopilot_run(self) -> dict[str, object] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM autopilot_runs ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "stats": json.loads(row["stats_json"]),
            "errors": json.loads(row["errors_json"]),
            "warnings": json.loads(row["warnings_json"]),
            "new_scholarship_ids": json.loads(row["new_scholarship_ids_json"]),
            "draft_ids": json.loads(row["draft_ids_json"]),
        }

    def update_source_state(
        self,
        source_name: str,
        *,
        last_fetched: str | None,
        last_error: str | None,
        last_status: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO source_state (source_name, last_fetched, last_error, last_status)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source_name) DO UPDATE SET
                    last_fetched=excluded.last_fetched,
                    last_error=excluded.last_error,
                    last_status=excluded.last_status
                """,
                (source_name, last_fetched, last_error, last_status),
            )

    def source_states(self) -> dict[str, dict[str, str | None]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM source_state").fetchall()
        return {
            row["source_name"]: {
                "last_fetched": row["last_fetched"],
                "last_error": row["last_error"],
                "last_status": row["last_status"],
            }
            for row in rows
        }

    def get_scholarship(self, scholarship_id: int) -> ScholarshipRecord | None:
        records = self._query("WHERE s.id = ?", (scholarship_id,))
        return records[0] if records else None

    def save_draft(self, draft: DraftRecord) -> DraftRecord:
        """Insert or update draft metadata for a scholarship prompt."""

        data = draft.model_dump(mode="json", exclude={"id", "scholarship_name"})
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO drafts (
                    scholarship_id, prompt, path, status, story_angle,
                    facts_used_json, claims_to_verify_json, missing_user_input_json,
                    why_angle_fits, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scholarship_id, prompt) DO UPDATE SET
                    path=excluded.path,
                    status=excluded.status,
                    story_angle=excluded.story_angle,
                    facts_used_json=excluded.facts_used_json,
                    claims_to_verify_json=excluded.claims_to_verify_json,
                    missing_user_input_json=excluded.missing_user_input_json,
                    why_angle_fits=excluded.why_angle_fits,
                    updated_at=excluded.updated_at
                """,
                (
                    data["scholarship_id"], data["prompt"], data["path"], data["status"],
                    data["story_angle"], json.dumps(data["facts_used"]),
                    json.dumps(data["claims_to_verify"]), json.dumps(data["missing_user_input"]),
                    data["why_angle_fits"], data["created_at"], data["updated_at"],
                ),
            )
            row = connection.execute(
                "SELECT id FROM drafts WHERE scholarship_id = ? AND prompt = ?",
                (draft.scholarship_id, draft.prompt),
            ).fetchone()
        saved = self.get_draft(int(row["id"]))
        if saved is None:  # pragma: no cover
            raise RuntimeError("Draft metadata could not be read back from SQLite.")
        return saved

    def get_draft(self, draft_id: int) -> DraftRecord | None:
        drafts = self._query_drafts("WHERE d.id = ?", (draft_id,))
        return drafts[0] if drafts else None

    def get_draft_for_prompt(self, scholarship_id: int, prompt: str) -> DraftRecord | None:
        drafts = self._query_drafts(
            "WHERE d.scholarship_id = ? AND d.prompt = ?", (scholarship_id, prompt)
        )
        return drafts[0] if drafts else None

    def list_drafts(self, status: DraftStatus | str | None = None) -> list[DraftRecord]:
        if status is None:
            return self._query_drafts("", ())
        value = status.value if isinstance(status, DraftStatus) else status
        return self._query_drafts("WHERE d.status = ?", (value,))

    def update_draft_status(self, draft_id: int, status: DraftStatus) -> DraftRecord:
        from datetime import datetime

        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE drafts SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, datetime.now().isoformat(), draft_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Draft not found: {draft_id}")
        updated = self.get_draft(draft_id)
        if updated is None:  # pragma: no cover
            raise RuntimeError("Updated draft could not be read back from SQLite.")
        return updated

    def _query_drafts(self, where: str, params: tuple[object, ...]) -> list[DraftRecord]:
        sql = f"""
            SELECT d.*, s.name AS scholarship_name
            FROM drafts d
            JOIN scholarships s ON s.id = d.scholarship_id
            {where}
            ORDER BY d.updated_at DESC
        """
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [
            DraftRecord.model_validate({
                "id": row["id"],
                "scholarship_id": row["scholarship_id"],
                "scholarship_name": row["scholarship_name"],
                "prompt": row["prompt"],
                "path": row["path"],
                "status": row["status"],
                "story_angle": row["story_angle"],
                "facts_used": json.loads(row["facts_used_json"]),
                "claims_to_verify": json.loads(row["claims_to_verify_json"]),
                "missing_user_input": json.loads(row["missing_user_input_json"]),
                "why_angle_fits": row["why_angle_fits"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })
            for row in rows
        ]

    def list_scholarships(self, status: str | None = None) -> list[ScholarshipRecord]:
        if status is None:
            return self._query("", ())
        return self._query("WHERE s.status = ?", (status,))

    def _query(self, where: str, params: tuple[object, ...]) -> list[ScholarshipRecord]:
        sql = f"""
            SELECT s.*, r.total_score, r.recommendation, r.explanation_json,
                   r.hard_conflicts_json, r.breakdown_json, r.ranked_at
            FROM scholarships s
            LEFT JOIN rankings r ON r.scholarship_id = s.id
            {where}
            ORDER BY COALESCE(r.total_score, -1) DESC, s.deadline ASC
        """
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._record_from_row(row) for row in rows]

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> ScholarshipRecord:
        scholarship_data = {
            key: json.loads(row[f"{key}_json"]) for key in JSON_FIELDS
        }
        for key in (
            "id", "name", "provider", "amount", "deadline", "application_url",
            "source_url", "source_type", "source_category", "competition_level", "effort_hours",
            "status", "notes", "created_at", "updated_at",
        ):
            scholarship_data[key] = row[key]
        for key in BOOL_FIELDS:
            value = row[key]
            scholarship_data[key] = None if value is None else bool(value)
        ranking = None
        if row["total_score"] is not None:
            ranking = RankingResult.model_validate({
                "scholarship_id": row["id"],
                "total_score": row["total_score"],
                "recommendation": row["recommendation"],
                "explanation": json.loads(row["explanation_json"]),
                "hard_conflicts": json.loads(row["hard_conflicts_json"]),
                "breakdown": json.loads(row["breakdown_json"]),
                "ranked_at": row["ranked_at"],
            })
        return ScholarshipRecord.model_validate({**scholarship_data, "ranking": ranking})
