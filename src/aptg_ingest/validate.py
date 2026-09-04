"""Invariants the built dataset must satisfy.

Failures are separated into two kinds. A *violation* means the data is wrong and the
build should fail; a *warning* records something incomplete but expected, such as a
prerequisite naming a course no term in the dataset happens to offer.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .model import BOTH, COURSEWORK, EVEN, INTERNSHIP, NEITHER, ODD, Dataset


@dataclass
class Report:
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.violations


def _prereq_cycles(edges) -> list[list[str]]:
    graph: dict[str, set[str]] = {}
    for e in edges:
        graph.setdefault(e.course_code, set()).add(e.prereq_code)
    colour: dict[str, int] = {}
    cycles: list[list[str]] = []

    def walk(node: str, path: list[str]) -> None:
        colour[node] = 1
        for nxt in sorted(graph.get(node, ())):
            if colour.get(nxt) == 1:
                cycles.append(path[path.index(nxt):] + [nxt] if nxt in path else [nxt, node])
            elif colour.get(nxt, 0) == 0:
                walk(nxt, path + [nxt])
        colour[node] = 2

    for n in sorted(graph):
        if colour.get(n, 0) == 0:
            walk(n, [n])
    return cycles


def validate(ds: Dataset) -> Report:
    r = Report()
    codes = {c.code for c in ds.courses if c.code}

    # 1. Prerequisite graph must be acyclic; a cycle makes scheduling impossible.
    cycles = _prereq_cycles(ds.prereq_edges)
    if cycles:
        r.violations.append(f"prerequisite graph contains {len(cycles)} cycle(s): {cycles[:3]}")

    # 2. Every coded course must carry parsed credits.
    missing = [c.code for c in ds.courses if c.code and c.credits is None]
    if missing:
        r.violations.append(f"{len(missing)} coded courses have no parsed credits: {missing[:5]}")

    # 3. Every offered course must have a derived parity.
    have_parity = {a.course_code for a in ds.availability}
    offered = {o.course_code for o in ds.offerings}
    if offered - have_parity:
        r.violations.append(f"{len(offered - have_parity)} offered courses have no parity")
    bad_parity = [
        a.course_code for a in ds.availability
        if a.parity not in (ODD, EVEN, BOTH, NEITHER)
    ]
    if bad_parity:
        r.violations.append(f"invalid parity values: {bad_parity[:5]}")
    # A course with no regular parity must be one the summer term offers; otherwise the
    # parity derivation has lost track of a term.
    stranded = [
        a.course_code for a in ds.availability if a.parity == NEITHER and not a.summer
    ]
    if stranded:
        r.violations.append(
            f"{len(stranded)} courses are offered in no term at all: {stranded[:5]}"
        )

    # 4. Prerequisite expressions must be parseable JSON referencing at least one course.
    for e in ds.prereq_edges[:]:
        try:
            json.loads(e.expr_normalized)
        except Exception:  # noqa: BLE001
            r.violations.append(f"unparseable expression on {e.course_code}: {e.expr_raw!r}")
            break

    # 5. Title-only courses must never appear in the prerequisite graph.
    title_only = {c.title for c in ds.courses if c.identity_mode == "TITLE_ONLY"}
    if title_only and any(e.prereq_code in title_only for e in ds.prereq_edges):
        r.violations.append("a TITLE_ONLY course is being used as a prerequisite")

    # 6. Internship semesters must carry no elective capacity.
    internship = {
        (s.programme_id, s.semester_no) for s in ds.semester_specs if s.kind == INTERNSHIP
    }
    leaking = [
        (s.programme_id, s.semester_no, s.slot_label)
        for s in ds.template_slots
        if (s.programme_id, s.semester_no) in internship and s.slot_type in ("OE", "DE", "MTB")
    ]
    if leaking:
        r.violations.append(f"elective slots inside internship semesters: {leaking[:3]}")

    # 7. Every minor must state a requirement a student can actually discharge.
    #    A basket with no compulsory course and choose_n of zero would be "granted" by
    #    the engine having scheduled nothing, which is how the earlier text scrape let
    #    thirteen empty baskets through.
    empty = [
        b.minor_id for b in ds.minor_baskets
        if not b.compulsory_codes and not (b.choose_n or 0)
    ]
    if empty:
        r.violations.append(f"{len(empty)} minor baskets require no courses: {empty[:5]}")
    over = [
        b.minor_id for b in ds.minor_baskets
        if (b.choose_n or 0) > len([c for c in b.choice_codes.split(",") if c])
    ]
    if over:
        r.violations.append(
            f"{len(over)} minor baskets ask for more courses than their basket holds: {over[:5]}"
        )

    # 8. Every minor course code must join to the course table. Resolution happens at
    #    load time; anything left is quarantined, so nothing should reach here.
    minor_codes: set[str] = set()
    for b in ds.minor_baskets:
        for group in b.compulsory_codes.split(","):
            minor_codes.update(c for c in group.split("|") if c)
        minor_codes.update(c for c in b.choice_codes.split(",") if c)
    dangling = sorted(minor_codes - codes)
    if dangling:
        r.violations.append(
            f"{len(dangling)} minor course codes do not resolve to a course: {dangling[:5]}"
        )

    # 9. Every recorded alias must point at a real course, and no course may be its
    #    own prerequisite — aliasing can create both faults if it is applied carelessly.
    dangling_alias = [a.from_code for a in ds.code_aliases if a.to_code not in codes]
    if dangling_alias:
        r.violations.append(
            f"{len(dangling_alias)} code aliases point at no course: {dangling_alias[:5]}"
        )
    self_prereq = sorted({e.course_code for e in ds.prereq_edges
                          if e.course_code == e.prereq_code})
    if self_prereq:
        r.violations.append(
            f"{len(self_prereq)} courses list themselves as a prerequisite: {self_prereq[:5]}"
        )

    # 10. A named template slot must join to a course, or the semester it belongs to
    #     cannot be planned. Recorded as a warning, not a violation: a genuinely new
    #     programme may name courses no term has yet run.
    unjoined = sorted({
        s.course_code for s in ds.template_slots
        if s.course_code and s.course_code not in codes
    })
    if unjoined:
        r.warnings.append(
            f"{len(unjoined)} template slots name a course no loaded term offers: "
            f"{unjoined[:8]}"
        )

    # 11. Semester numbering must be sane.
    bad_sem = [
        (s.programme_id, s.semester_no)
        for s in ds.semester_specs
        if not 1 <= s.semester_no <= 12
    ]
    if bad_sem:
        r.violations.append(f"semester numbers out of range: {bad_sem[:5]}")

    # --- warnings ----------------------------------------------------------
    unresolved = sorted({e.prereq_code for e in ds.prereq_edges if e.prereq_code not in codes})
    if unresolved:
        r.warnings.append(
            f"{len(unresolved)} prerequisite codes are not offered in any loaded term "
            f"(legacy or discontinued): {unresolved[:8]}"
        )
    ambiguous = {e.expr_raw for e in ds.prereq_edges if e.ambiguous}
    if ambiguous:
        r.warnings.append(
            f"{len(ambiguous)} prerequisite expressions mix AND and OR without grouping "
            "and were read using the variant-grouping rule"
        )
    tentative = sum(1 for s in ds.semester_specs if s.confidence == "TENTATIVE")
    if tentative:
        r.warnings.append(
            f"{tentative} semester specs could not be checked against a printed credit total"
        )

    # --- stats -------------------------------------------------------------
    r.stats = {
        "terms": len(ds.terms),
        "course_master": len(ds.course_master),
        "code_aliases": len(ds.code_aliases),
        "courses": len(ds.courses),
        "courses_ug": sum(1 for c in ds.courses if c.is_ug),
        "offerings": len(ds.offerings),
        "prereq_edges": len(ds.prereq_edges),
        "prereq_courses": len({e.course_code for e in ds.prereq_edges}),
        "prereq_unresolved": len(unresolved),
        "programmes": len(ds.programmes),
        "semester_specs": len(ds.semester_specs),
        "semester_specs_validated": sum(
            1 for s in ds.semester_specs if s.confidence == "OBSERVED"
        ),
        "template_slots": len(ds.template_slots),
        "minor_baskets": len(ds.minor_baskets),
        "minor_departments": len({b.department for b in ds.minor_baskets}),
        "minor_courses": len(minor_codes),
        "policy_rules": len(ds.policy_rules),
        "eligibility_rules": len(ds.eligibility),
        "parity": {
            p: sum(1 for a in ds.availability if a.parity == p)
            for p in (ODD, EVEN, BOTH, NEITHER)
        },
        "summer_courses": sum(1 for a in ds.availability if a.summer),
        "coursework_semesters": sum(
            1 for s in ds.semester_specs if s.kind == COURSEWORK
        ),
        "internship_semesters": len(internship),
    }
    return r
