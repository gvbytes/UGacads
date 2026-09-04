"""The summer term.

UG Manual 4.3.6: "UG students may register for a maximum of 27 credits in the
summer-term. The summer-term is not a regular semester." Three consequences the engine
has to honour, and these tests pin all three:

* a far lower credit ceiling than the regular 65;
* a much smaller course set — 157 courses against 1,482 across the regular terms — so
  most requirements simply cannot be met in summer;
* it is not a semester, so it neither advances the graduation semester nor counts
  against the maximum programme duration in UG Manual 3.2.

Summer is used only when the student asks for it. `allow_summer` defaults to False.
"""
from pathlib import Path

import pytest

from aptg_engine.data import load
from aptg_engine.engine import FEASIBLE, Engine
from aptg_engine.state import Preferences, StudentProfile

DB = Path(__file__).resolve().parents[1] / "out" / "aptg.sqlite"


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


# --- the data ---------------------------------------------------------------

def test_the_summer_term_is_loaded(engine):
    summer = [c for c in engine.cat.courses.values() if c.summer]
    assert len(summer) == 157


def test_a_summer_only_course_has_no_regular_parity(engine):
    cat = engine.cat
    only = [c for c in cat.courses.values() if c.parity == "NEITHER"]
    assert only, "the summer schedule carries courses no regular term does"
    for c in only:
        assert c.summer
        # It can never be placed in a regular semester, of either parity.
        assert not cat.offered_in(c.code, 3)
        assert not cat.offered_in(c.code, 4)
        assert cat.offered_in_summer(c.code)


def test_summer_availability_is_independent_of_parity(engine):
    """A course can run in odd semesters and in summer; the two are separate facts."""
    cat = engine.cat
    both = [c for c in cat.courses.values() if c.summer and c.parity in ("ODD", "EVEN")]
    assert both, "some summer courses also run in a regular semester"


# --- the preference ---------------------------------------------------------

def test_no_summer_term_appears_unless_the_student_asks(engine):
    rm = engine.solve(fresher(), Preferences(max_credits=55, allow_summer=False))
    assert rm.summers == {}
    assert not any(t["is_summer"] for t in rm.as_dict()["semesters"])


def test_allowing_summer_can_shorten_the_degree(engine):
    p = fresher()
    without = engine.solve(p, Preferences(max_credits=55, allow_summer=False))
    with_summer = engine.solve(p, Preferences(max_credits=55, allow_summer=True))
    assert with_summer.summers, "summer should be used when it helps"
    assert with_summer.graduation_semester <= without.graduation_semester
    assert with_summer.verdict == FEASIBLE


# --- the rules --------------------------------------------------------------

def test_a_summer_term_never_exceeds_the_ug_manual_ceiling(engine):
    """27 credits, against 65 in a regular semester."""
    cap = engine.cat.policy["summer.max"]["value_max"]
    assert cap == 27
    for ceiling in (30, 55, 65):
        rm = engine.solve(fresher(), Preferences(max_credits=ceiling, allow_summer=True))
        for after, credits in rm.credits_per_summer.items():
            assert credits <= cap, f"summer after {after} carries {credits} > {cap}"


def test_a_students_own_ceiling_still_binds_in_summer(engine):
    rm = engine.solve(fresher(), Preferences(max_credits=12, allow_summer=True))
    for credits in rm.credits_per_summer.values():
        assert credits <= 12


def test_only_courses_the_summer_schedule_carries_are_placed_in_summer(engine):
    cat = engine.cat
    rm = engine.solve(fresher(), Preferences(max_credits=55, allow_summer=True))
    for after, places in rm.summers.items():
        for p in places:
            if p.course_code:
                assert cat.offered_in_summer(p.course_code), (
                    f"{p.course_code} placed in a summer term but the summer schedule "
                    "does not carry it"
                )


def test_summer_follows_an_even_semester(engine):
    """The academic year runs odd, even, summer."""
    rm = engine.solve(fresher(), Preferences(max_credits=55, allow_summer=True))
    for after in rm.summers:
        assert after % 2 == 0, f"a summer term was placed after semester {after}"


def test_a_summer_term_does_not_advance_the_graduation_semester(engine):
    """UG Manual 4.3.6: the summer-term is not a regular semester."""
    rm = engine.solve(fresher(), Preferences(max_credits=55, allow_summer=True))
    assert rm.graduation_semester == max(rm.semesters)
    assert rm.graduation_semester not in (None,)


def test_prerequisites_hold_across_a_summer_term(engine):
    """A course taken in summer counts towards the semester that follows it."""
    cat = engine.cat
    profile = fresher()
    rm = engine.solve(profile, Preferences(max_credits=55, allow_summer=True))
    have = set(profile.completed)
    for s in sorted(set(rm.semesters) | set(rm.summers)):
        for p in rm.semesters.get(s, []):
            if p.course_code and (node := cat.prereqs.get(p.course_code)):
                from aptg_engine.data import satisfied
                assert satisfied(node, have), f"{p.course_code} scheduled unmet in {s}"
        for p in rm.semesters.get(s, []):
            if p.course_code:
                have.add(p.course_code)
        for p in rm.summers.get(s, []):
            if p.course_code and (node := cat.prereqs.get(p.course_code)):
                from aptg_engine.data import satisfied
                assert satisfied(node, have), (
                    f"{p.course_code} scheduled in the summer after {s} with unmet "
                    "prerequisites"
                )
        for p in rm.summers.get(s, []):
            if p.course_code:
                have.add(p.course_code)


def test_a_course_is_never_scheduled_twice_across_summer_and_semester(engine):
    rm = engine.solve(fresher(), Preferences(max_credits=55, allow_summer=True))
    codes = [p.course_code for p in rm.placements if p.course_code]
    assert len(codes) == len(set(codes))


# --- explainability ---------------------------------------------------------

def test_the_summer_dependency_is_flagged(engine):
    rm = engine.solve(fresher(), Preferences(max_credits=55, allow_summer=True))
    assert any(f.kind == "summer_dependency" and f.level == "HIGH" for f in rm.risks)


def test_the_log_cites_the_summer_credit_rule(engine):
    rm = engine.solve(fresher(), Preferences(max_credits=55, allow_summer=True))
    assert any("summer-term maximum is" in line for line in rm.log)
    assert any("4.3.6" in line for line in rm.log)


def test_an_unused_summer_permission_is_reported(engine):
    """Permitting summer and getting none is a fact the student should be told."""
    profile = StudentProfile(
        batch="Y26", department="WSAIS", programme_id="BCYBER",
        current_semester=1, completed=set(),
    )
    rm = engine.solve(profile, Preferences(max_credits=55, allow_summer=True))
    assert rm.summers == {}
    assert any("none was used" in line for line in rm.log)


def test_every_summer_placement_carries_a_reason(engine):
    rm = engine.solve(fresher(), Preferences(max_credits=55, allow_summer=True))
    for places in rm.summers.values():
        for p in places:
            assert p.reason and "summer" in p.reason.lower()


def test_the_output_labels_the_summer_term_as_such(engine):
    d = engine.solve(fresher(), Preferences(max_credits=55, allow_summer=True)).as_dict()
    summers = [t for t in d["semesters"] if t["is_summer"]]
    assert summers
    for t in summers:
        assert t["parity"] == "summer"
        assert t["label"].startswith("Summer after semester")


def test_terms_are_emitted_in_the_order_they_are_sat(engine):
    d = engine.solve(fresher(), Preferences(max_credits=55, allow_summer=True)).as_dict()
    seen = [(t["semester"], t["is_summer"]) for t in d["semesters"]]
    assert seen == sorted(seen), "a summer term must follow the semester it comes after"


def test_summer_planning_is_deterministic(engine):
    p, prefs = fresher(), Preferences(max_credits=55, allow_summer=True)
    assert engine.solve(p, prefs).as_dict() == engine.solve(p, prefs).as_dict()
