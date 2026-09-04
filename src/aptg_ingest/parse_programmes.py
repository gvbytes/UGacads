"""Programmes published outside the DOAA template set, plus curated policy data.

Two 2026-27 programmes matter here and neither appears in a department template:

* **B.Cyber** (WSAIS) publishes its curriculum by course *title* only, and its
  semesters 5-8 are full-time internship. Its courses are emitted as TITLE_ONLY so they
  can never be resolved as prerequisites, and its later semesters are marked INTERNSHIP
  so they carry no elective capacity.
* **BT-IS** (Department of Intelligent Systems) is published on the department site and
  is explicitly labelled subject to Senate approval, so it is recorded TENTATIVE.
"""
from __future__ import annotations

import json

from .model import (
    COURSEWORK,
    INTERNSHIP,
    TITLE_ONLY,
    Course,
    PolicyRule,
    Programme,
    ProgrammeEligibility,
    SemesterSpec,
    TemplateSlot,
)
from .provenance import DERIVED, OBSERVED, TENTATIVE, Quarantine, Source


def parse_bcyber(path: str, q: Quarantine):
    """Return (programme, semester_specs, slots, title_only_courses) for B.Cyber."""
    with open(path) as fh:
        data = json.load(fh)
    src = Source(path, "$")

    programme = Programme(
        programme_id="BCYBER",
        name=data["name"],
        kind="BCYBER",
        department=None,
        school=data.get("school"),
        batch_from="Y26",
        batch_to=None,
        total_semesters=8,
        confidence=OBSERVED,
        **src.as_row(),
    )

    specs: list[SemesterSpec] = []
    slots: list[TemplateSlot] = []
    courses: list[Course] = []

    for key, entries in data["semesters"].items():
        if key == "5-8":
            for sem_no in range(5, 9):
                item = entries[0]
                ssrc = src.at(f"semesters.5-8[0] -> sem {sem_no}")
                specs.append(
                    SemesterSpec(
                        programme_id="BCYBER",
                        semester_no=sem_no,
                        kind=INTERNSHIP,
                        credits_min=item["credits_per_semester"],
                        credits_max=item["credits_per_semester"],
                        printed_total=str(item["credits_per_semester"]),
                        confidence=OBSERVED,
                    )
                )
                slots.append(
                    TemplateSlot(
                        programme_id="BCYBER",
                        semester_no=sem_no,
                        slot_label=item["title"],
                        slot_type="INTERNSHIP",
                        course_code=None,
                        credits=item["credits_per_semester"],
                        is_choice=False,
                        confidence=OBSERVED,
                        **ssrc.as_row(),
                    )
                )
            continue

        sem_no = int(key)
        lo = hi = 0
        for idx, item in enumerate(entries):
            ssrc = src.at(f"semesters.{key}[{idx}]")
            credits = item["credits"]
            if isinstance(credits, dict):
                c_lo = credits.get("min", credits.get("total"))
                c_hi = credits.get("max", credits.get("total"))
            else:
                c_lo = c_hi = credits
            lo += c_lo or 0
            hi += c_hi or 0

            is_basket = item.get("basket_published") is False
            # A taught B.Cyber course is published by title with a fixed semester and no
            # course code. FIXED marks exactly that: schedulable, but only where the
            # curriculum puts it, and never resolvable as a prerequisite.
            slot_type = "ELECTIVE" if is_basket else "FIXED"
            slots.append(
                TemplateSlot(
                    programme_id="BCYBER",
                    semester_no=sem_no,
                    slot_label=item["title"],
                    slot_type=slot_type,
                    course_code=None,
                    credits=c_hi,
                    is_choice=is_basket,
                    confidence=TENTATIVE if is_basket else OBSERVED,
                    **ssrc.as_row(),
                )
            )
            if not is_basket:
                courses.append(
                    Course(
                        code=None,
                        title=item["title"],
                        department="WSAIS",
                        identity_mode=TITLE_ONLY,
                        level=None,
                        is_ug=True,
                        ltps=None,
                        credits=c_hi,
                        confidence=OBSERVED,
                        **ssrc.as_row(),
                    )
                )
            else:
                q.add(
                    "programme.unpublished_basket",
                    ssrc,
                    "B.Cyber elective basket contents are not published",
                    item["title"],
                )

        specs.append(
            SemesterSpec(
                programme_id="BCYBER",
                semester_no=sem_no,
                kind=COURSEWORK,
                credits_min=lo,
                credits_max=hi,
                printed_total=f"{lo}-{hi}" if lo != hi else str(lo),
                confidence=OBSERVED,
            )
        )

    return programme, specs, slots, courses


def load_curated(policy_path: str, eligibility_path: str):
    """Load hand-transcribed policy and eligibility rules, each carrying its citation."""
    with open(policy_path) as fh:
        policy = json.load(fh)["rules"]
    with open(eligibility_path) as fh:
        elig = json.load(fh)["rules"]

    rules = [
        PolicyRule(
            rule_id=r["rule_id"],
            scope=r["scope"],
            name=r["name"],
            value_min=r["value_min"],
            value_max=r["value_max"],
            unit=r["unit"],
            text=r["text"],
            citation=r["citation"],
            confidence=r.get("confidence", OBSERVED),
        )
        for r in policy
    ]
    eligibility = [
        ProgrammeEligibility(
            rule_id=r["rule_id"],
            programme_id=r["programme_id"],
            option=r["option"],
            department=r["department"],
            batch_from=r["batch_from"],
            batch_to=r["batch_to"],
            eligible=r["eligible"],
            text=r["text"],
            citation=r["citation"],
            confidence=r.get("confidence", DERIVED),
        )
        for r in elig
    ]
    return rules, eligibility
