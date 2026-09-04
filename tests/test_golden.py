"""Golden-value tests.

These pin figures that were verified by hand against the source documents. They exist
so that a parser regression shows up as a failing test rather than as quietly wrong
data in the database.
"""
from pathlib import Path

import pytest

from aptg_ingest.cli import build
from aptg_ingest.parse_templates import parse_template
from aptg_ingest.provenance import Quarantine
from aptg_ingest.validate import validate

DATA = Path(__file__).resolve().parents[1] / "data"

# The eight per-semester credit totals printed on the ME B.Tech template.
ME_SEMESTER_CREDITS = [55, 52, 55, 50, 53, 57, 51, 45]


@pytest.fixture(scope="module")
def dataset():
    return build(DATA, Quarantine())


def test_me_template_semester_credits_match_the_printed_totals():
    progs, specs, _slots = parse_template(str(DATA / "templates" / "ME-template.pdf"), Quarantine())
    bt = [s for s in specs if s.programme_id == "ME-BT"]
    assert [s.credits_min for s in sorted(bt, key=lambda s: s.semester_no)] == ME_SEMESTER_CREDITS
    assert all(s.confidence == "OBSERVED" for s in bt)


def test_course_universe_size(dataset):
    """1,449 across the three regular terms, plus the 17 the summer term alone runs."""
    coded = [c for c in dataset.courses if c.code]
    assert len(coded) == 1466
    summer_only = {a.course_code for a in dataset.availability if a.parity == "NEITHER"}
    assert len(summer_only) == 17


def test_semester_parity_split(dataset):
    """NEITHER is a course the summer term alone offers: it has no regular parity."""
    parity = {}
    for a in dataset.availability:
        parity[a.parity] = parity.get(a.parity, 0) + 1
    assert parity == {"ODD": 705, "EVEN": 518, "BOTH": 226, "NEITHER": 17}
    assert sum(1 for a in dataset.availability if a.summer) == 157
    assert all(a.summer for a in dataset.availability if a.parity == "NEITHER")


def test_first_year_courses_are_present_in_both_parities(dataset):
    """Regression guard for the lecture-section suffix bug.

    Courses that run in parallel sections are titled "... (TA111) /A". An earlier
    version of the code regex required the code to end the title and silently dropped
    all 87 such rows, which made first-year courses look as though they were never
    offered.
    """
    parity = {a.course_code: a.parity for a in dataset.availability}
    for code in ("TA111", "CHM111", "ESC111M", "ESC112M", "MTH101A"):
        assert parity.get(code) == "BOTH", f"{code} missing or wrong parity"


def test_bcyber_has_no_elective_capacity_after_semester_four(dataset):
    internship = {
        s.semester_no for s in dataset.semester_specs
        if s.programme_id == "BCYBER" and s.kind == "INTERNSHIP"
    }
    assert internship == {5, 6, 7, 8}
    electives = [
        s for s in dataset.template_slots
        if s.programme_id == "BCYBER" and s.semester_no in internship
        and s.slot_type in ("OE", "DE")
    ]
    assert electives == []


def test_bcyber_courses_are_title_only_and_never_prerequisites(dataset):
    bcyber = [c for c in dataset.courses if c.department == "WSAIS"]
    assert bcyber and all(c.identity_mode == "TITLE_ONLY" and c.code is None for c in bcyber)


def test_minor_baskets_carry_the_ugarc_batch_split(dataset):
    variants = {b.minor_id: b for b in dataset.minor_baskets}
    assert variants["AE-OLD_Y21"].batch_to == "Y21"
    assert variants["AE-NEW_Y22"].batch_from == "Y22"
    assert variants["AE-NEW_Y22"].compulsory_codes == "AE201M"
    # AE201A is the Old UGARC anchor and no loaded term offers that spelling. The
    # approved course master gives both AE201A and AE201M the title "INTRODUCTION TO
    # AEROSPACE ENGINEERING", which is direct evidence that they are one course
    # renumbered — evidence the structural stem rule cannot supply, since it must refuse
    # to merge two differently-suffixed codes. The batch split survives in the window and
    # the basket, not in the anchor's spelling.
    assert variants["AE-OLD_Y21"].compulsory_codes == "AE201M"
    assert len(variants["AE-OLD_Y21"].choice_codes.split(",")) == 3
    assert len(variants["AE-NEW_Y22"].choice_codes.split(",")) == 4


def test_every_minor_basket_requires_at_least_one_course(dataset):
    """The scrape this replaced let thirteen empty baskets through.

    An empty basket is worse than a missing one: requesting it retargets nothing, so the
    engine reports a minor earned having scheduled not a single course for it.
    """
    for b in dataset.minor_baskets:
        assert b.compulsory_codes or b.choose_n, f"{b.minor_id} requires no courses"


def test_minor_streams_are_separate_baskets(dataset):
    """A minor is a stream, not a department: a student reads one of CSE's four."""
    cse = {b.stream for b in dataset.minor_baskets if b.department == "CSE"}
    assert cse == {
        "Algorithms", "Computer Systems", "Theory of Computing",
        "Artificial Intelligence (AI/ML)",
    }
    assert len({b.department for b in dataset.minor_baskets}) == 15


def test_minor_course_codes_all_resolve_to_real_courses(dataset):
    """Every code stored must join to the course table, or the engine cannot use it."""
    known = {c.code for c in dataset.courses if c.code}
    for b in dataset.minor_baskets:
        for group in b.compulsory_codes.split(","):
            for code in group.split("|"):
                if code:
                    assert code in known, f"{b.minor_id}: {code} joins to no course"
        for code in b.choice_codes.split(","):
            if code:
                assert code in known, f"{b.minor_id}: {code} joins to no course"


def test_a_basket_that_cannot_reach_its_credit_floor_is_tentative(dataset):
    """CE's minor is a five-course sequence; most of it is in no loaded term."""
    ce = next(b for b in dataset.minor_baskets if b.minor_id == "CE-INFRASTRUCTURE")
    assert ce.credits_min == 30
    assert ce.confidence == "TENTATIVE"
    assert set(ce.unresolved_codes.split(",")) == {"CE241A", "CE641A", "CE643A", "CE644A"}


def test_first_year_institute_core_is_schedulable(dataset):
    """Regression guard for the code-aliasing gap that broke every first-year semester.

    The templates print MTH111 and ESC111; the schedule exports print MTH111M and
    ESC111M. Until the two were reconciled, eight Institute Core courses matched no
    course row, so the engine reported them unschedulable and filled semesters 1 and 2
    with whatever else fitted.
    """
    known = {c.code for c in dataset.courses if c.code}
    me = [s for s in dataset.template_slots if s.programme_id == "ME-BT"]
    first_year = [s for s in me if s.semester_no <= 2 and s.course_code]
    assert len(first_year) == 14
    for s in first_year:
        assert s.course_code in known, f"{s.course_code} still resolves to no course"


def test_aliases_are_recorded_with_the_evidence_for_them(dataset):
    by_from = {a.from_code: a for a in dataset.code_aliases}
    assert by_from["MTH111"].to_code == "MTH111M"
    assert by_from["MTH111"].method == "STEM"
    assert by_from["ESO207A"].to_code == "ESO207"
    assert by_from["ESO207A"].method == "TITLE_MATCH"
    assert all(a.to_code in {c.code for c in dataset.courses if c.code}
               for a in dataset.code_aliases)


def test_no_course_is_its_own_prerequisite(dataset):
    """Aliasing can create a self-edge where a course lists its own former code."""
    for e in dataset.prereq_edges:
        assert e.course_code != e.prereq_code, f"{e.course_code} depends on itself"


def test_the_course_master_covers_the_codes_the_schedules_omit(dataset):
    master = {r.code for r in dataset.course_master}
    assert len(master) > 2500
    for code in ("AE201A", "CE241A", "MTH428A", "PHI141A", "CS365A"):
        assert code in master, f"{code} missing from the approved course master"


def test_departmental_cpi_cutoffs_and_seat_caps_are_recorded(dataset):
    by_id = {b.minor_id: b for b in dataset.minor_baskets}
    assert by_id["CGS-COGNITIVE_SCIENCE"].cpi_min == 7.5
    assert by_id["MSE-FUNCTIONAL_MATERIALS"].cpi_min == 6.0
    assert by_id["BSBE-TISSUE_ENGINEERING"].cpi_min == 7.0
    assert by_id["BSBE-TISSUE_ENGINEERING"].seat_cap == 8


def test_stated_course_alternatives_survive_as_or_groups(dataset):
    """EE311A or EE370A satisfies the third Microelectronics requirement."""
    ee = next(b for b in dataset.minor_baskets if b.minor_id == "EE-MICROELECTRONICS")
    groups = ee.compulsory_codes.split(",")
    assert any("|" in g for g in groups), ee.compulsory_codes
    assert {"EE311", "EE370"} == set(next(g for g in groups if "|" in g).split("|"))


def test_dis_minor_is_gated_to_y26(dataset):
    rule = next(r for r in dataset.eligibility if r.rule_id == "dis.minor.y26")
    assert rule.batch_from == "Y26" and rule.department == "IS"


def test_departments_without_column_geometry_still_extract(dataset):
    """EE and AE publish templates the geometric reader cannot rule.

    EE emits each table row as a single text run with no usable column positions, so the
    linear fallback reads the rows in reading order instead. Both departments must yield
    a usable B.Tech template; before the fallback existed, an EE or AE student could not
    be planned at all.
    """
    by_prog = {}
    for s in dataset.template_slots:
        by_prog.setdefault(s.programme_id, []).append(s)
    for pid in ("EE-BT", "AE-BT"):
        slots = by_prog.get(pid, [])
        assert len(slots) >= 20, f"{pid} extracted only {len(slots)} slots"
        named = [s for s in slots if s.course_code]
        assert len(named) >= 10, f"{pid} named only {len(named)} courses"


def test_batch_specific_templates_get_distinct_programme_ids(dataset):
    """PHY publishes one template for Y22-Y24 and another for Y25 onward.

    Both were previously reduced to the same identifier, so the second file silently
    overwrote the first and the batch distinction was lost.
    """
    phy = {p.programme_id: p for p in dataset.programmes if p.department == "PHY"}
    early = [p for p in phy.values() if p.batch_from == "Y22" and p.batch_to == "Y24"]
    later = [p for p in phy.values() if p.batch_from == "Y25"]
    assert early and later, "both PHY batch windows should be present"
    assert not ({p.programme_id for p in early} & {p.programme_id for p in later})


def test_no_invariant_violations(dataset):
    report = validate(dataset)
    assert report.violations == [], report.violations
