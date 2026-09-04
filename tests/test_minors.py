"""Minor handling: code resolution, basket integrity and the engine's minor rules.

These pin the four defects the curated catalogue replaced:

1. every minor course code failed to join to the course table, because the catalogue
   prints ``ESO207A`` where the schedule prints ``ESO207``;
2. thirteen baskets required no courses at all, so requesting one produced a FEASIBLE
   plan containing no minor course;
3. a department's streams were merged into a single basket;
4. departmental CPI cutoffs, seat caps and the "not your own department" rule were
   absent entirely.
"""
from pathlib import Path

import pytest

from aptg_ingest.codes import resolve, resolve_all, stem
from aptg_engine.data import load
from aptg_engine.engine import FEASIBLE, INFEASIBLE, Engine
from aptg_engine.state import Preferences, StudentProfile

DB = Path(__file__).resolve().parents[1] / "out" / "aptg.sqlite"

ME_THROUGH_SEM5 = {
    "MTH111", "MTH112", "PHY111", "PHY113", "TA111", "CHM112", "CHM113", "CHM111",
    "ESC111", "ESC112", "LIF111", "MTH113", "MTH114", "PHY112", "ESC201", "MSO202M",
    "MSO203M", "ESO201", "ESO202", "ME209", "ME222", "ME231", "ME252", "ME261",
    "ME301", "ME321", "ME331", "ME333", "ME361", "ME381",
}


@pytest.fixture(scope="module")
def engine():
    return Engine(load(str(DB)))


def me_student(**kw):
    base = dict(
        batch="Y24", department="ME", programme_id="ME-BT",
        current_semester=6, cpi=8.5, completed=set(ME_THROUGH_SEM5),
    )
    base.update(kw)
    return StudentProfile(**base)


# --- code resolution --------------------------------------------------------

def test_stem_strips_only_a_trailing_letter():
    assert stem("ESO207A") == "ESO207"
    assert stem("ESO207") == "ESO207"
    assert stem("MSO203M") == "MSO203"


def test_a_suffixed_code_resolves_to_its_bare_form():
    """The whole reason no minor course used to resolve."""
    assert resolve("ESO207A", {"ESO207"}) == "ESO207"
    assert resolve("CS345A", {"CS345", "CS340"}) == "CS345"


def test_a_suffixed_code_never_resolves_to_a_different_suffix():
    """AE201A and AE201M are different courses — that is the Old/New UGARC split."""
    assert resolve("AE201A", {"AE201M"}) is None
    assert resolve("CE667A", {"CE667M"}) is None


def test_a_bare_code_may_resolve_to_its_only_marked_variant():
    assert resolve("CGS702", {"CGS702A"}) == "CGS702A"
    assert resolve("CGS702", {"CGS702A", "CGS702B"}) is None


def test_resolve_all_reports_what_it_could_not_place():
    got, missing = resolve_all(["ESO207A", "CS345A", "NOPE999A"], {"ESO207", "CS345"})
    assert got == ["ESO207", "CS345"]
    assert missing == ["NOPE999A"]


def test_resolution_is_order_preserving_and_deduplicating():
    got, _ = resolve_all(["CS345A", "CS345", "ESO207A"], {"CS345", "ESO207"})
    assert got == ["CS345", "ESO207"]


# --- catalogue integrity ----------------------------------------------------

def test_minor_courses_now_join_to_the_course_table(engine):
    """Before the catalogue was curated, this count was zero."""
    cat = engine.cat
    codes = set()
    for m in cat.minors.values():
        codes.update(c for g in m.compulsory for c in g)
        codes.update(m.choices)
    assert len(codes) > 100
    assert all(c in cat.courses for c in codes)


def test_no_minor_can_be_earned_without_taking_a_course(engine):
    for m in engine.cat.minors.values():
        assert m.course_count > 0, f"{m.minor_id} requires nothing"


def test_alternative_groups_are_loaded_as_tuples(engine):
    ee = engine.cat.minors["EE-MICROELECTRONICS"]
    assert ("EE311", "EE370") in ee.compulsory or ("EE370", "EE311") in ee.compulsory


# --- the engine's minor rules ----------------------------------------------

def test_a_minor_in_the_students_own_department_is_refused(engine):
    """DOAA: a Minor may be taken in any department except one's own."""
    rm = engine.solve(me_student(), Preferences(target_minor="ME-MANUFACTURING"))
    assert rm.verdict == INFEASIBLE
    assert "own department" in rm.summary
    assert any(f.kind == "structural" for f in rm.risks)


def test_a_minor_in_another_department_is_not_refused_on_that_ground(engine):
    rm = engine.solve(me_student(), Preferences(target_minor="MSE-FUNCTIONAL_MATERIALS",
                                                max_credits=55))
    assert "own department" not in rm.summary


def test_a_cpi_below_the_departmental_cutoff_blocks_the_minor(engine):
    """Cognitive Science applies a CPI cutoff of 7.5."""
    rm = engine.solve(
        me_student(cpi=7.2), Preferences(target_minor="CGS-COGNITIVE_SCIENCE")
    )
    assert rm.verdict == INFEASIBLE
    assert "7.5" in rm.summary and "7.20" in rm.summary


def test_the_same_minor_is_allowed_above_the_cutoff(engine):
    rm = engine.solve(
        me_student(cpi=7.9),
        Preferences(target_minor="CGS-COGNITIVE_SCIENCE", max_credits=55),
    )
    assert "CPI cutoff" not in rm.summary


def test_a_missing_cpi_blocks_a_minor_that_states_a_cutoff(engine):
    """Eligibility that cannot be established must not be assumed."""
    rm = engine.solve(
        me_student(cpi=None), Preferences(target_minor="CGS-COGNITIVE_SCIENCE")
    )
    assert rm.verdict == INFEASIBLE
    assert "no CPI was supplied" in rm.summary


def test_a_requested_minor_actually_schedules_its_courses(engine):
    rm = engine.solve(
        me_student(),
        Preferences(target_minor="CGS-COGNITIVE_SCIENCE", max_credits=55),
    )
    minor_courses = [p for p in rm.placements if p.origin == "minor"]
    assert len(minor_courses) >= 3, "the CGS minor is two cores plus one elective"
    codes = {p.course_code for p in minor_courses}
    assert {"CGS401", "CGS402"} <= codes


def test_a_minor_that_schedules_nothing_can_never_report_feasible(engine):
    """The defect this guards: an empty basket used to come back FEASIBLE.

    Squeezing the ceiling leaves the template's own requirements unmet before the
    minor's, so the verdict must not be FEASIBLE while minor requirements are
    outstanding.
    """
    rm = engine.solve(
        me_student(),
        Preferences(target_minor="CGS-COGNITIVE_SCIENCE", max_credits=36,
                    target_semesters=8, max_semesters=8),
    )
    placed = sum(1 for p in rm.placements if p.origin == "minor")
    if placed < engine._minor_slots_needed:
        assert rm.verdict != FEASIBLE
        assert any(f.kind == "minor_incomplete" for f in rm.risks)


def test_an_outstanding_minor_requirement_is_named_in_the_log(engine):
    rm = engine.solve(
        me_student(),
        Preferences(target_minor="CGS-COGNITIVE_SCIENCE", max_credits=55),
    )
    assert any("Requested minor" in line for line in rm.log)
    assert any("CGS-COGNITIVE_SCIENCE" in line for line in rm.log)


def test_a_seat_capped_minor_is_flagged(engine):
    """BSBE caps Tissue Engineering at eight students; a schedule is not an admission."""
    rm = engine.solve(
        me_student(cpi=8.5),
        Preferences(target_minor="BSBE-TISSUE_ENGINEERING", max_credits=55),
    )
    assert any(f.kind == "minor_seat_cap" for f in rm.risks)


def test_a_thinned_basket_is_flagged_rather_than_presented_as_complete(engine):
    rm = engine.solve(
        me_student(),
        Preferences(target_minor="CSE-AI", max_credits=55),
    )
    kinds = {f.kind for f in rm.risks}
    assert "minor_basket_thinned" in kinds
    assert "minor_basket_tentative" in kinds


def test_the_minor_log_cites_its_source(engine):
    rm = engine.solve(
        me_student(), Preferences(target_minor="MSE-FUNCTIONAL_MATERIALS", max_credits=55)
    )
    assert any("IITK_Minor_Courses_Catalogue" in line for line in rm.log)


def test_minor_placements_are_deterministic(engine):
    p = me_student()
    prefs = Preferences(target_minor="CGS-COGNITIVE_SCIENCE", max_credits=55)
    assert engine.solve(p, prefs).as_dict() == engine.solve(p, prefs).as_dict()


def test_a_minor_still_consumes_elective_slots_rather_than_adding_credits(engine):
    """UG Manual 7.4, re-checked against the corrected baskets."""
    p = me_student()
    plain = engine.solve(p, Preferences(max_credits=55))
    with_minor = engine.solve(
        p, Preferences(max_credits=55, target_minor="CGS-COGNITIVE_SCIENCE")
    )
    assert sum(with_minor.credits_per_semester.values()) <= sum(
        plain.credits_per_semester.values()
    ) + 11
    assert any(x.origin == "minor" for x in with_minor.placements)


def test_minor_courses_obey_the_same_hard_constraints_as_any_other(engine):
    cat = engine.cat
    profile = me_student()
    rm = engine.solve(
        profile, Preferences(target_minor="CGS-COGNITIVE_SCIENCE", max_credits=55)
    )
    have = set(profile.completed)
    for sem in sorted(rm.semesters):
        for p in rm.semesters[sem]:
            if p.course_code:
                assert cat.offered_in(p.course_code, sem), (
                    f"{p.course_code} placed in semester {sem} against its parity"
                )
        for p in rm.semesters[sem]:
            if p.course_code:
                have.add(p.course_code)


def test_an_ineligible_minor_still_yields_a_degree_pathway(engine):
    rm = engine.solve(me_student(), Preferences(target_minor="ME-MANUFACTURING"))
    assert rm.verdict == INFEASIBLE
    assert rm.semesters, "the degree itself should still be scheduled as an alternative"


# --- resolution must not substitute a different course ----------------------

def test_a_stem_match_is_vetoed_when_the_titles_contradict(engine):
    """EE210A is Microelectronics-I; EE210 is Analog Electronics.

    They share a stem and nothing else. The structural rule alone would merge them, and
    did: the EE Microelectronics minor silently required Analog Electronics in place of
    the course the catalogue names.
    """
    ee = engine.cat.minors["EE-MICROELECTRONICS"]
    codes = {c for g in ee.compulsory for c in g}
    assert "EE210" not in codes, "the minor picked up a different course by stem"
    assert "EE210A" in ee.unresolved
    assert ee.confidence == "TENTATIVE"


def test_a_reworded_title_still_resolves(engine):
    """The veto must stay narrow: departments reword courses all the time."""
    from aptg_ingest.parse_master import titles_conflict

    assert not titles_conflict("PROCESS CONTROL", "PROCESS DYNAMICS AND CONTROL")
    assert not titles_conflict("COMPUTER ORGANIZATION", "INTRODUCTION TO COMPUTER ORGANISATION")
    assert titles_conflict("SOIL MECHANICS", "FOUNDATION DESIGN")
    assert titles_conflict("MICROELECTRONICS-I", "ANALOG ELECTRONICS")
    # No evidence either way must never veto.
    assert not titles_conflict("", "ANYTHING")


def test_every_basket_course_carries_the_title_the_source_intended(engine):
    """The DoMS example codes were mispaired with their titles in the source.

    It names MBA606A for Marketing Management, but the approved course master reads
    MBA606A as Economic Analysis for Management; Marketing Management is MBA631A.
    """
    cat = engine.cat
    doms = cat.minors["DOMS-MANAGEMENT"]
    titles = {cat.courses[c].title.upper() for c in doms.choices}
    assert "MARKETING MANAGEMENT" in titles
    assert "PRODUCTION AND OPERATIONS MANAGEMENT" in titles
    assert "ECONOMIC ANALYSIS FOR MANAGEMENT" not in titles


def test_all_call_sites_resolve_a_code_the_same_way(engine):
    """A spelling must mean the same course in a template, a prereq and a basket."""
    cat = engine.cat
    for m in cat.minors.values():
        for code in {c for g in m.compulsory for c in g} | set(m.choices):
            assert cat.canonical(code) == code, (
                f"{m.minor_id} stored {code}, which the resolver would rewrite again"
            )
