"""The template's semester is a floor: no course is scheduled before the curriculum
places it.

The scheduler used to order candidates by prerequisite criticality first, so a course
that unlocks a long chain was pulled as early as its prerequisites and parity allowed.
The published prerequisite data is sparse — most Institute Core courses record none at
all — so nothing held those courses back, and a plan for a first-semester student opened
with ESC201, ESO201, ESO202 and ME209: a third- and fourth-semester load in semester 1,
while MTH111 and MTH112 went unscheduled.

Sequencing the prerequisite graph does not capture is carried by the department
template. These tests pin that.
"""
from pathlib import Path

import pytest

from aptg_engine.data import load
from aptg_engine.engine import Engine
from aptg_engine.state import Preferences, StudentProfile

DB = Path(__file__).resolve().parents[1] / "out" / "aptg.sqlite"

# The eight per-semester credit totals printed on the ME B.Tech template.
ME_PRINTED = [55, 52, 55, 50, 53, 57, 51, 45]


@pytest.fixture(scope="module")
def engine():
    return Engine(load(str(DB)))


def fresher(**kw):
    base = dict(
        batch="Y25", department="ME", programme_id="ME-BT",
        current_semester=1, cpi=None, completed=set(),
    )
    base.update(kw)
    return StudentProfile(**base)


def slot_semester(cat, programme_id, code):
    """The semester the programme's own template assigns to a named course."""
    return min(
        s.semester_no
        for s in cat.slots_by_programme[programme_id]
        if s.course_code == code
    )


# --- the reported defect ----------------------------------------------------

def test_no_named_course_is_placed_before_its_template_semester(engine):
    cat = engine.cat
    rm = engine.solve(fresher(), Preferences(max_credits=55))
    for sem, places in rm.semesters.items():
        for p in places:
            if not p.course_code or p.origin != "template":
                continue
            try:
                want = slot_semester(cat, "ME-BT", p.course_code)
            except ValueError:
                continue  # filled from a pool, not named by the template
            assert sem >= want, (
                f"{p.course_code} placed in semester {sem} but the ME template puts it "
                f"in semester {want}"
            )


def test_esc201_lands_in_the_semester_the_template_gives_it(engine):
    """The case that was reported: a third-semester Institute Core in semester 1."""
    assert slot_semester(engine.cat, "ME-BT", "ESC201") == 3
    rm = engine.solve(fresher(), Preferences(max_credits=55))
    where = {p.course_code: s for s, ps in rm.semesters.items() for p in ps}
    assert where["ESC201"] == 3


def test_the_first_semester_is_the_templates_first_semester(engine):
    """Semester 1 must hold the seven Institute Core courses the template names."""
    rm = engine.solve(fresher(), Preferences(max_credits=55))
    got = {p.course_code for p in rm.semesters[1] if p.course_code}
    expected = {
        "MTH111M", "MTH112M", "CHM112M", "CHM113M", "PHY111", "PHY113", "TA111",
    }
    assert expected <= got, f"missing from semester 1: {sorted(expected - got)}"


def test_second_year_courses_do_not_appear_in_the_first_year(engine):
    rm = engine.solve(fresher(), Preferences(max_credits=55))
    early = {p.course_code for s in (1, 2) for p in rm.semesters.get(s, [])}
    for code in ("ESC201", "ESO201", "ESO202", "ME209", "ME231", "ME222"):
        assert code not in early, f"{code} scheduled in the first year"


# --- a backlog still moves forward, not backward ---------------------------

def test_a_course_may_be_scheduled_later_than_the_template_places_it(engine):
    """The floor must not stop a backlog being cleared: it is a floor, not a fixture."""
    profile = StudentProfile(
        batch="Y24", department="ME", programme_id="ME-BT",
        current_semester=5, cpi=7.0, completed=set(),
    )
    rm = engine.solve(profile, Preferences(max_credits=55, max_semesters=12))
    where = {p.course_code: s for s, ps in rm.semesters.items() for p in ps}
    # ESC201 belongs to semester 3 but the student starts planning at 5.
    assert where.get("ESC201", 0) >= 5


def test_planning_never_starts_before_the_students_current_semester(engine):
    profile = StudentProfile(
        batch="Y24", department="ME", programme_id="ME-BT",
        current_semester=6, cpi=8.0, completed=set(),
    )
    rm = engine.solve(profile, Preferences(max_credits=55, max_semesters=12))
    assert min(rm.semesters) >= 6


# --- credit reconciliation --------------------------------------------------

def test_semester_loads_track_the_printed_template_totals(engine):
    """Slots whose credit figure did not survive extraction used to default to 9.

    That inflated semester 1 from 55 to 73 and pushed the degree to ten semesters.
    """
    rm = engine.solve(fresher(), Preferences(max_credits=55))
    for sem, printed in enumerate(ME_PRINTED[:6], start=1):
        got = rm.credits_per_semester.get(sem, 0)
        assert abs(got - printed) <= 9, (
            f"semester {sem} carries {got} credits against a printed {printed}"
        )


def test_no_semester_exceeds_the_ug_manual_ceiling(engine):
    for ceiling in (50, 55, 65):
        rm = engine.solve(fresher(), Preferences(max_credits=ceiling))
        for sem, credits in rm.credits_per_semester.items():
            assert credits <= ceiling, f"semester {sem} carries {credits} > {ceiling}"


# --- elective quality -------------------------------------------------------

def test_project_registrations_are_never_offered_as_electives(engine):
    """A UGP needs a supervisor; the template carries its own UGP slots for that."""
    cat = engine.cat
    rm = engine.solve(fresher(), Preferences(max_credits=55))
    for p in rm.placements:
        if not p.course_code or p.slot_type not in ("OE", "DE", "SCHEME"):
            continue
        title = (cat.courses[p.course_code].title or "").upper()
        assert "UNDER GRADUATE PROJECT" not in title, f"{p.course_code} is a UGP"
        assert "UNDER GRADUATE RESEARCH" not in title, f"{p.course_code} is a UGP"


def test_a_postgraduate_course_is_not_chosen_while_an_undergraduate_one_fits(engine):
    cat = engine.cat
    rm = engine.solve(fresher(), Preferences(max_credits=55))
    pg = [
        p.course_code for p in rm.placements
        if p.course_code and p.slot_type in ("OE", "SCHEME")
        and not cat.courses[p.course_code].is_ug
    ]
    assert pg == [], f"postgraduate courses chosen for undergraduate slots: {pg}"


# --- student input ----------------------------------------------------------

def test_a_completed_course_is_recognised_however_it_is_spelled(engine):
    """A student reads MTH111 off their template; the schedule calls it MTH111M."""
    unsuffixed = StudentProfile(
        batch="Y25", department="ME", programme_id="ME-BT", current_semester=2,
        completed={"MTH111", "MTH112", "CHM112", "CHM113", "PHY111", "PHY113", "TA111"},
    )
    rm = engine.solve(unsuffixed, Preferences(max_credits=55))
    rescheduled = {p.course_code for p in rm.placements} & {
        "MTH111M", "MTH112M", "CHM112M", "CHM113M", "PHY111", "PHY113", "TA111"
    }
    assert rescheduled == set(), f"already-completed courses rescheduled: {rescheduled}"
    assert any("matched to the spelling" in line for line in rm.log)


def test_an_unrecognised_code_is_left_alone(engine):
    assert engine.cat.canonical("NOTACODE") == "NOTACODE"


def test_the_master_separates_a_wrong_code_from_an_unscheduled_one(engine):
    cat = engine.cat
    assert "not in the institute's approved course master" in cat.describe_unknown("ZZ999")
    assert "approved course" in cat.describe_unknown("CE241A")
