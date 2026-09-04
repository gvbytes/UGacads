"""Senate-approved Old-UGARC to New-UGARC course mappings.

These matter for students admitted in Y21 and earlier, whose transcripts carry codes the
current schedules no longer print. Some of the changes are renumberings that no
structural rule can derive, and at least one is a case where structural inference would
produce a confident wrong answer.

Source: DOAA `Course_mapping_data.pdf`, approved by the Senate in meeting 2023-24/5th
(564th), 06-07 June 2024, with additions through SUGC 2024-25/4th-8th.
"""
import json
import sqlite3
from pathlib import Path

import pytest

from aptg_engine.data import load

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "out" / "aptg.sqlite"
MAPPING = ROOT / "data" / "curated" / "course_mapping_ugarc.json"


@pytest.fixture(scope="module")
def cat():
    return load(str(DB))


@pytest.fixture(scope="module")
def mapping():
    return json.loads(MAPPING.read_text())


def test_renumbered_physics_courses_resolve(cat):
    """PHY101A/102A/103A became PHY111/112/113 — a renumbering, not a suffix change.

    The codes share no stem, so no structural rule can connect them.
    """
    assert cat.canonical("PHY101A") == "PHY111"
    assert cat.canonical("PHY102A") == "PHY112"
    assert cat.canonical("PHY103A") == "PHY113"


def test_the_mse_project_mapping_is_not_the_one_inference_would_give(cat):
    """MSE497A maps to MSE498, and MSE497 is the new code for a *different* course.

    Dropping the suffix would credit a student for the wrong project course, which is
    precisely why the Senate mapping has to override structural inference.
    """
    assert cat.canonical("MSE497A") == "MSE498"
    assert cat.canonical("MSE398A") == "MSE497"


def test_department_renumbering_applies_as_a_rule(cat):
    """The Senate states IMExyzA is equivalent to DMSxyz in general terms."""
    assert cat.canonical("IME701A") == "DMS661"
    conn = sqlite3.connect(DB)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM code_alias WHERE method = 'SENATE_DEPT'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert n > 0, "the IME -> DMS rule should generate aliases"


def test_a_rule_never_invents_a_course(cat):
    """The department rule only fires where the resulting code actually exists."""
    assert cat.canonical("IME615A") == "IME615A", "no DMS615 exists, so nothing to map to"


def test_senate_mappings_are_recorded_even_when_unused(mapping):
    """The legacy codes appear in no loaded term; the mapping must persist regardless.

    They exist for old-batch transcripts, so waiting for one to turn up in a template
    would defeat their purpose.
    """
    conn = sqlite3.connect(DB)
    try:
        stored = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT from_code, to_code FROM code_alias WHERE method = 'SENATE'"
            )
        }
    finally:
        conn.close()
    for entry in mapping["aliases"]:
        if entry["to"] in stored.values() or entry["from"] in stored:
            assert stored.get(entry["from"]) == entry["to"], entry["from"]
    assert len(stored) >= 10


def test_one_to_many_mappings_are_equivalences_not_aliases(cat):
    """CE451A maps to a *choice* of two courses, which no alias can express."""
    conn = sqlite3.connect(DB)
    try:
        ids = {r[0] for r in conn.execute("SELECT equivalence_id FROM course_equivalence")}
        alias = conn.execute(
            "SELECT to_code FROM code_alias WHERE from_code = 'CE451A'"
        ).fetchone()
    finally:
        conn.close()
    assert "ce451.split" in ids
    assert alias is None, "a one-to-many mapping must not be collapsed into an alias"


def test_an_old_batch_transcript_is_credited(cat):
    """A Y21 student entering old codes must not be told to retake the course."""
    from aptg_engine.engine import Engine
    from aptg_engine.state import Preferences, StudentProfile

    conn = sqlite3.connect(DB)
    try:
        done = {
            r[0] for r in conn.execute(
                "SELECT course_code FROM template_slot WHERE programme_id='ME-BT' "
                "AND semester_no < 6 AND course_code IS NOT NULL"
            )
        }
    finally:
        conn.close()
    legacy = (done - {"PHY111", "PHY112"}) | {"PHY101A", "PHY102A"}
    p = StudentProfile(
        batch="Y21", department="ME", programme_id="ME-BT",
        current_semester=6, cpi=8.5, completed=legacy,
    )
    rm = Engine(cat).solve(p, Preferences(max_credits=55))
    scheduled = {x.course_code for x in rm.placements}
    assert "PHY111" not in scheduled and "PHY112" not in scheduled
