"""SQLite schema and writer.

Rows are written in a deterministic order so that two runs over identical inputs
produce identical database content, which the idempotence test relies on.
"""
from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path

from .model import Dataset

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE term (
    term_id TEXT PRIMARY KEY, parity TEXT NOT NULL, era TEXT,
    label TEXT, is_current INTEGER NOT NULL,
    source_file TEXT, source_sha256 TEXT);

CREATE TABLE course (
    code TEXT, title TEXT NOT NULL, department TEXT,
    identity_mode TEXT NOT NULL, level INTEGER, is_ug INTEGER,
    ltps TEXT, credits INTEGER, confidence TEXT NOT NULL,
    source_file TEXT, source_locator TEXT, source_sha256 TEXT);
CREATE UNIQUE INDEX course_code_ux ON course(code) WHERE code IS NOT NULL;
CREATE INDEX course_dept_ix ON course(department);

CREATE TABLE course_master (
    code TEXT PRIMARY KEY, branch TEXT, title TEXT NOT NULL,
    discontinued INTEGER NOT NULL, confidence TEXT NOT NULL,
    source_file TEXT, source_locator TEXT, source_sha256 TEXT);
CREATE INDEX master_branch_ix ON course_master(branch);

CREATE TABLE code_alias (
    from_code TEXT PRIMARY KEY, to_code TEXT NOT NULL,
    method TEXT NOT NULL, confidence TEXT NOT NULL);

CREATE TABLE course_equivalence (
    equivalence_id TEXT PRIMARY KEY, name TEXT, legacy_code TEXT,
    member_codes TEXT NOT NULL, expression TEXT NOT NULL,
    text TEXT, citation TEXT, confidence TEXT NOT NULL);

CREATE TABLE offering (
    course_code TEXT NOT NULL, term_id TEXT NOT NULL, slot_name TEXT,
    section TEXT, course_types TEXT, mode TEXT, instructors TEXT,
    timing TEXT, venue TEXT, confidence TEXT NOT NULL,
    source_file TEXT, source_locator TEXT, source_sha256 TEXT);
CREATE INDEX offering_course_ix ON offering(course_code);
CREATE INDEX offering_term_ix ON offering(term_id);

CREATE TABLE course_availability (
    course_code TEXT PRIMARY KEY, parity TEXT NOT NULL, summer INTEGER NOT NULL,
    terms TEXT, seen_current INTEGER NOT NULL, confidence TEXT NOT NULL);

CREATE TABLE prereq_edge (
    course_code TEXT NOT NULL, prereq_code TEXT NOT NULL, group_id TEXT NOT NULL,
    alternative_group INTEGER, resolved INTEGER NOT NULL,
    expr_raw TEXT, expr_ast TEXT, expr_normalized TEXT, ambiguous INTEGER NOT NULL,
    equivalence_id TEXT,
    confidence TEXT NOT NULL,
    source_file TEXT, source_locator TEXT, source_sha256 TEXT);
CREATE INDEX prereq_course_ix ON prereq_edge(course_code);
CREATE INDEX prereq_prereq_ix ON prereq_edge(prereq_code);

CREATE TABLE programme (
    programme_id TEXT PRIMARY KEY, name TEXT, kind TEXT, department TEXT,
    school TEXT, batch_from TEXT, batch_to TEXT, total_semesters INTEGER,
    confidence TEXT NOT NULL,
    source_file TEXT, source_locator TEXT, source_sha256 TEXT);

CREATE TABLE semester_spec (
    programme_id TEXT NOT NULL, semester_no INTEGER NOT NULL, kind TEXT NOT NULL,
    credits_min INTEGER, credits_max INTEGER, printed_total TEXT,
    confidence TEXT NOT NULL,
    PRIMARY KEY (programme_id, semester_no));

CREATE TABLE template_slot (
    programme_id TEXT NOT NULL, semester_no INTEGER NOT NULL, slot_label TEXT,
    slot_type TEXT, course_code TEXT, credits INTEGER, is_choice INTEGER,
    confidence TEXT NOT NULL,
    source_file TEXT, source_locator TEXT, source_sha256 TEXT);
CREATE INDEX slot_prog_ix ON template_slot(programme_id, semester_no);

CREATE TABLE credit_rule (
    programme_id TEXT NOT NULL, course_type TEXT NOT NULL,
    recommended_min INTEGER, recommended_max INTEGER,
    required_min INTEGER, required_max INTEGER, confidence TEXT NOT NULL,
    source_file TEXT, source_locator TEXT, source_sha256 TEXT);

CREATE TABLE minor_basket (
    minor_id TEXT PRIMARY KEY, department TEXT, title TEXT, stream TEXT,
    ugarc_variant TEXT NOT NULL, batch_from TEXT, batch_to TEXT,
    compulsory_codes TEXT, choose_n INTEGER, choice_codes TEXT,
    unresolved_codes TEXT, source_course_count INTEGER,
    credits_min INTEGER, credits_max INTEGER,
    cpi_min REAL, seat_cap INTEGER, template_ref TEXT, notes TEXT,
    citation TEXT, confidence TEXT NOT NULL,
    source_file TEXT, source_locator TEXT, source_sha256 TEXT);
CREATE INDEX minor_dept_ix ON minor_basket(department);

CREATE TABLE policy_rule (
    rule_id TEXT PRIMARY KEY, scope TEXT, name TEXT,
    value_min INTEGER, value_max INTEGER, unit TEXT,
    text TEXT, citation TEXT, confidence TEXT NOT NULL);

CREATE TABLE programme_eligibility (
    rule_id TEXT PRIMARY KEY, programme_id TEXT, option TEXT, department TEXT,
    batch_from TEXT, batch_to TEXT, eligible INTEGER NOT NULL,
    text TEXT, citation TEXT, confidence TEXT NOT NULL);

CREATE TABLE ingest_quarantine (
    kind TEXT, reason TEXT, raw TEXT,
    source_file TEXT, source_locator TEXT, source_sha256 TEXT);
"""

TABLES = [
    ("term", "terms"),
    ("course_master", "course_master"),
    ("code_alias", "code_aliases"),
    ("course_equivalence", "course_equivalences"),
    ("course", "courses"),
    ("offering", "offerings"),
    ("course_availability", "availability"),
    ("prereq_edge", "prereq_edges"),
    ("programme", "programmes"),
    ("semester_spec", "semester_specs"),
    ("template_slot", "template_slots"),
    ("credit_rule", "credit_rules"),
    ("minor_basket", "minor_baskets"),
    ("policy_rule", "policy_rules"),
    ("programme_eligibility", "eligibility"),
]


def _sort_key(row: dict) -> tuple:
    return tuple(str(v) for v in row.values())


def write(dataset: Dataset, quarantine: list[dict], path: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()

    conn = sqlite3.connect(out)
    try:
        conn.executescript(SCHEMA)
        for table, attr in TABLES:
            items = getattr(dataset, attr)
            if not items:
                continue
            rows = [dataclasses.asdict(i) for i in items]
            rows.sort(key=_sort_key)
            cols = list(rows[0].keys())
            placeholders = ",".join("?" * len(cols))
            conn.executemany(
                f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})",
                [tuple(r[c] for c in cols) for r in rows],
            )
        if quarantine:
            rows = sorted(quarantine, key=_sort_key)
            cols = ["kind", "reason", "raw", "source_file", "source_locator", "source_sha256"]
            conn.executemany(
                f"INSERT INTO ingest_quarantine ({','.join(cols)}) VALUES (?,?,?,?,?,?)",
                [tuple(r.get(c) for c in cols) for r in rows],
            )
        conn.commit()
    finally:
        conn.close()


def counts(path: str) -> dict[str, int]:
    conn = sqlite3.connect(path)
    try:
        out = {}
        for table, _ in TABLES + [("ingest_quarantine", "")]:
            out[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return out
    finally:
        conn.close()
