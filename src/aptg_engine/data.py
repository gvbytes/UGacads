"""Read-only view of the ingested database, loaded once and shared by the engine."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from functools import cached_property

ODD, EVEN, BOTH = "ODD", "EVEN", "BOTH"
# A course offered only in the summer term has no regular-semester parity. Summer is
# not a regular semester (UG Manual 4.3.6), so it is a separate flag, not a parity.
NEITHER = "NEITHER"


@dataclass(frozen=True)
class Course:
    code: str
    title: str
    department: str | None
    level: int | None
    is_ug: bool
    credits: int | None
    parity: str                 # ODD | EVEN | BOTH | NEITHER (summer only)
    types: frozenset[str]
    seen_current: bool = True   # still present in the current published schedule
    summer: bool = False        # offered in the summer term


@dataclass(frozen=True)
class Slot:
    programme_id: str
    semester_no: int
    slot_label: str
    slot_type: str
    course_code: str | None
    credits: int | None
    confidence: str


@dataclass(frozen=True)
class Minor:
    """One minor stream.

    ``compulsory`` holds one entry per required course. An entry is a tuple of
    alternatives: usually one code, but ``("CE643A", "CE644A")`` where the source states
    that either satisfies the requirement.
    """

    minor_id: str
    department: str
    title: str
    stream: str
    ugarc_variant: str
    batch_from: str | None
    batch_to: str | None
    compulsory: tuple[tuple[str, ...], ...]
    choose_n: int
    choices: tuple[str, ...]
    unresolved: tuple[str, ...]
    source_course_count: int
    credits_min: int | None
    credits_max: int | None
    cpi_min: float | None
    seat_cap: int | None
    notes: str | None
    citation: str
    confidence: str

    @property
    def label(self) -> str:
        return self.title if self.stream == self.title else f"{self.title} — {self.stream}"

    @property
    def course_count(self) -> int:
        return len(self.compulsory) + self.choose_n


@dataclass
class Catalogue:
    courses: dict[str, Course] = field(default_factory=dict)
    slots: list[Slot] = field(default_factory=list)
    minors: dict[str, Minor] = field(default_factory=dict)
    programmes: dict[str, dict] = field(default_factory=dict)
    semester_kinds: dict[tuple[str, int], str] = field(default_factory=dict)
    prereqs: dict[str, dict] = field(default_factory=dict)   # code -> normalized AST
    prereq_meta: dict[str, dict] = field(default_factory=dict)
    policy: dict[str, dict] = field(default_factory=dict)
    eligibility: list[dict] = field(default_factory=list)
    aliases: dict[str, tuple[str, str]] = field(default_factory=dict)  # old -> (new, method)

    def resolve_code(self, code: str) -> tuple[str | None, str | None]:
        """Map a course code a student typed onto the code the catalogue uses.

        Transcripts of students admitted under the old UGARC carry codes the current
        schedules no longer print, and some of the changes are renumberings that no
        structural rule can derive: PHY102A became PHY112. Returns the resolved code and
        the method, or (None, None) when the code is unknown.
        """
        code = (code or "").strip().upper()
        if not code:
            return None, None
        if code in self.courses:
            return code, None
        hit = self.aliases.get(code)
        if hit:
            return hit[0], hit[1]
        return None, None
    aliases: dict[str, str] = field(default_factory=dict)   # spelling -> course code
    master: dict[str, dict] = field(default_factory=dict)   # every approved course

    # --- code identity -----------------------------------------------------

    def canonical(self, code: str) -> str:
        """The course-table code for a course however it is spelled.

        The build records every alias it relied on, so a student who types the code
        printed on their template (``MTH111``) is understood to mean the course the
        schedule lists (``MTH111M``). Without this a completed course goes unrecognised
        and the engine schedules it again.
        """
        code = code.strip().upper().replace(" ", "")
        if code in self.courses:
            return code
        return self.aliases.get(code, code)

    def canonical_all(self, codes) -> set[str]:
        return {self.canonical(c) for c in codes if c and c.strip()}

    def describe_unknown(self, code: str) -> str:
        """Why a code names no schedulable course — the master separates two cases."""
        row = self.master.get(code)
        if row is None:
            return (
                f"{code} is not in the institute's approved course master, so it is "
                "most likely a mistyped or long-withdrawn code"
            )
        if row["discontinued"]:
            return f"{code} is recorded as discontinued in the approved course master"
        return (
            f"{code} ({row['title'].title()}) is an approved course but appears in none "
            "of the loaded schedule exports, so no term can be shown for it"
        )

    # --- derived -----------------------------------------------------------

    @cached_property
    def slots_by_programme(self) -> dict[str, list[Slot]]:
        out: dict[str, list[Slot]] = {}
        for s in self.slots:
            out.setdefault(s.programme_id, []).append(s)
        for v in out.values():
            v.sort(key=lambda s: (s.semester_no, s.slot_type, s.slot_label))
        return out

    @cached_property
    def dependents(self) -> dict[str, set[str]]:
        """prereq code -> courses that depend on it."""
        out: dict[str, set[str]] = {}
        for code, meta in self.prereq_meta.items():
            for p in meta["codes"]:
                out.setdefault(p, set()).add(code)
        return out

    def depth(self, code: str, _seen: tuple[str, ...] = ()) -> int:
        """Longest prerequisite chain ending at ``code``."""
        if code in _seen:
            return 0
        meta = self.prereq_meta.get(code)
        if not meta or not meta["codes"]:
            return 0
        return 1 + max(self.depth(p, _seen + (code,)) for p in meta["codes"])

    def criticality(self, code: str, _seen: tuple[str, ...] = ()) -> int:
        """Longest chain of things that depend on ``code``, directly or otherwise."""
        if code in _seen:
            return 0
        deps = self.dependents.get(code)
        if not deps:
            return 0
        return 1 + max(self.criticality(d, _seen + (code,)) for d in deps)

    def offered_in(self, code: str, semester_no: int) -> bool:
        """Is this course offered in a *regular* semester of that parity?"""
        c = self.courses.get(code)
        if c is None:
            return False
        if c.parity == BOTH:
            return True
        if c.parity == NEITHER:
            return False  # summer term only
        want = ODD if semester_no % 2 else EVEN
        return c.parity == want

    def offered_in_summer(self, code: str) -> bool:
        c = self.courses.get(code)
        return bool(c and c.summer)


def _ast_codes(node: dict) -> list[str]:
    if not node:
        return []
    if node.get("op") == "COURSE":
        return [node["code"]]
    out: list[str] = []
    for a in node.get("args", ()):
        out.extend(_ast_codes(a))
    return out


def satisfied(node: dict, have: set[str]) -> bool:
    """Evaluate a prerequisite expression against the set of completed courses."""
    if not node:
        return True
    op = node.get("op")
    if op == "COURSE":
        return node["code"] in have
    if op == "AND":
        return all(satisfied(a, have) for a in node["args"])
    if op == "OR":
        return any(satisfied(a, have) for a in node["args"])
    return True


def missing(node: dict, have: set[str], offered: set[str] | None = None) -> list[str]:
    """Codes that would need to be added for ``node`` to become satisfiable.

    For an OR, one arm is reported rather than every alternative, so the explanation
    names a single concrete route. ``offered`` — the courses some loaded term actually
    runs — decides which. Without it the shortest arm wins, and the shortest arm is
    often the withdrawn one: the requirement for ESO207 is "ESC101A, or ESC111M and one
    of ESC112M/ESC113M", and naming the single legacy course ESC101A tells a current
    student to take something no term offers. An arm the student can actually register
    for is preferred even when it is longer.
    """
    if not node or satisfied(node, have):
        return []
    op = node.get("op")
    if op == "COURSE":
        return [node["code"]]
    if op == "AND":
        out: list[str] = []
        for a in node["args"]:
            out.extend(missing(a, have, offered))
        return out
    if op == "OR":
        arms = [missing(a, have, offered) for a in node["args"]]
        if not arms:
            return []
        if offered is None:
            return min(arms, key=len)
        return min(arms, key=lambda arm: (sum(1 for c in arm if c not in offered), len(arm)))
    return []


def load(db_path: str) -> Catalogue:
    import json

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cat = Catalogue()
    try:
        parity, current, summer = {}, {}, {}
        for r in conn.execute(
            "SELECT course_code, parity, summer, seen_current FROM course_availability"
        ):
            parity[r["course_code"]] = r["parity"]
            current[r["course_code"]] = bool(r["seen_current"])
            summer[r["course_code"]] = bool(r["summer"])
        types: dict[str, set[str]] = {}
        for r in conn.execute("SELECT course_code, course_types FROM offering"):
            if r["course_types"]:
                types.setdefault(r["course_code"], set()).update(
                    t for t in r["course_types"].split(",") if t
                )
        for r in conn.execute(
            "SELECT code,title,department,level,is_ug,credits FROM course WHERE code IS NOT NULL"
        ):
            cat.courses[r["code"]] = Course(
                code=r["code"],
                title=r["title"],
                department=r["department"],
                level=r["level"],
                is_ug=bool(r["is_ug"]),
                credits=r["credits"],
                parity=parity.get(r["code"], BOTH),
                types=frozenset(types.get(r["code"], ())),
                seen_current=current.get(r["code"], True),
                summer=summer.get(r["code"], False),
            )

        for r in conn.execute(
            "SELECT programme_id,semester_no,slot_label,slot_type,course_code,credits,"
            "confidence FROM template_slot"
        ):
            cat.slots.append(Slot(*[r[k] for k in r.keys()]))

        for r in conn.execute("SELECT * FROM programme"):
            cat.programmes[r["programme_id"]] = dict(r)
        for r in conn.execute("SELECT programme_id,semester_no,kind FROM semester_spec"):
            cat.semester_kinds[(r["programme_id"], r["semester_no"])] = r["kind"]

        for r in conn.execute(
            "SELECT course_code, expr_normalized, expr_raw, ambiguous FROM prereq_edge"
        ):
            code = r["course_code"]
            if code in cat.prereqs:
                continue
            node = json.loads(r["expr_normalized"])
            cat.prereqs[code] = node
            cat.prereq_meta[code] = {
                "codes": sorted(set(_ast_codes(node))),
                "raw": r["expr_raw"],
                "ambiguous": bool(r["ambiguous"]),
            }

        for r in conn.execute("SELECT * FROM minor_basket"):
            cat.minors[r["minor_id"]] = Minor(
                minor_id=r["minor_id"],
                department=r["department"],
                title=r["title"],
                stream=r["stream"],
                ugarc_variant=r["ugarc_variant"],
                batch_from=r["batch_from"],
                batch_to=r["batch_to"],
                compulsory=tuple(
                    tuple(c for c in group.split("|") if c)
                    for group in (r["compulsory_codes"] or "").split(",")
                    if group
                ),
                choose_n=r["choose_n"] or 0,
                choices=tuple(c for c in (r["choice_codes"] or "").split(",") if c),
                unresolved=tuple(c for c in (r["unresolved_codes"] or "").split(",") if c),
                source_course_count=r["source_course_count"],
                credits_min=r["credits_min"],
                credits_max=r["credits_max"],
                cpi_min=r["cpi_min"],
                seat_cap=r["seat_cap"],
                notes=r["notes"],
                citation=r["citation"],
                confidence=r["confidence"],
            )

        for r in conn.execute("SELECT from_code, to_code FROM code_alias"):
            cat.aliases[r["from_code"]] = r["to_code"]
        for r in conn.execute("SELECT code, branch, title, discontinued FROM course_master"):
            cat.master[r["code"]] = {
                "branch": r["branch"], "title": r["title"],
                "discontinued": bool(r["discontinued"]),
            }

        for r in conn.execute("SELECT from_code, to_code, method FROM code_alias"):
            cat.aliases[r["from_code"]] = (r["to_code"], r["method"])

        for r in conn.execute("SELECT * FROM policy_rule"):
            cat.policy[r["rule_id"]] = dict(r)
        cat.eligibility = [dict(r) for r in conn.execute("SELECT * FROM programme_eligibility")]
    finally:
        conn.close()
    return cat
