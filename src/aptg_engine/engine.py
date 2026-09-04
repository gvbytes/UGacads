"""CSED - the constraint satisfaction and scheduling engine.

The engine is deterministic. Given the same catalogue, profile and preferences it
produces the same roadmap, and no part of it consults a language model. Placement order
is fixed by an explicit key, never by iteration order over a set or dict.

Hard constraints, all enforced before any preference is considered:

1. **Prerequisite completion.** A course may only be placed in a semester where its
   prerequisite expression evaluates true against everything completed or already
   scheduled earlier.
2. **Semester availability.** A course observed only in odd terms cannot be placed in an
   even semester, and vice versa.
3. **Credit limits.** Per-semester load stays within the UG Manual range and the
   student's own ceiling.
4. **Semester type.** Internship semesters carry no course capacity at all.
5. **Batch eligibility.** Programme options gated to a batch window are refused outside
   it.

Preferences - graduating on time, workload, career alignment - only order the candidates
that survive those constraints.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from .data import BOTH, Catalogue, missing, satisfied
from .state import Preferences, StudentProfile

FEASIBLE = "FEASIBLE"
FEASIBLE_WITH_ADJUSTMENT = "FEASIBLE_WITH_ADJUSTMENT"
INFEASIBLE = "CURRENTLY_INFEASIBLE"


def _count(n: int, noun: str) -> str:
    """"1 summer term" / "2 summer terms" — these strings are read by students."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"

# Slot types that need a specific named course rather than a choice from a pool.
NAMED = {"DC", "IC", "ESO"}
# Slot types filled from a pool of eligible courses.
# ELECTIVE is deliberately absent: it marks a basket whose eligible courses the
# institute has not published, so there is no pool to choose from and inventing one
# would misrepresent the curriculum.
POOLED = {"DE", "OE", "SCHEME", "MTB"}
# Slot types that carry credit but no course code to resolve.
NON_COURSE = {"UGP", "INTERNSHIP", "FIXED"}
# Slot types whose semester is dictated by the published curriculum rather than chosen
# by the scheduler. A B.Cyber taught course and an internship block both sit where the
# programme puts them; there is no freedom to move them.
PINNED = {"INTERNSHIP", "FIXED"}


@dataclass
class Requirement:
    key: str
    kind: str                 # NAMED | POOLED | NON_COURSE | MINOR
    slot_type: str
    template_semester: int
    credits: int
    course_code: str | None = None
    pool: tuple[str, ...] = ()
    label: str = ""
    slot_label: str = ""
    origin: str = "template"  # template | minor
    satisfied_by: str | None = None
    blocked_reason: str = ""
    pinned: bool = False


@dataclass
class Placement:
    semester: int
    course_code: str | None
    label: str
    slot_type: str
    credits: int
    origin: str
    reason: str


@dataclass
class Rejection:
    semester: int
    course_code: str | None
    label: str
    reason: str


@dataclass
class RiskFlag:
    level: str        # HIGH | MEDIUM | LOW
    kind: str
    message: str


@dataclass
class Roadmap:
    verdict: str = FEASIBLE
    summary: str = ""
    placements: list[Placement] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    unplaced: list[Requirement] = field(default_factory=list)
    risks: list[RiskFlag] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    semesters: dict[int, list[Placement]] = field(default_factory=dict)
    credits_per_semester: dict[int, int] = field(default_factory=dict)
    # Summer terms, keyed by the regular semester each one follows. Kept apart from
    # `semesters` because the summer term is not a regular semester (UG Manual 4.3.6):
    # it does not advance the graduation semester and does not count against the
    # maximum programme duration.
    summers: dict[int, list[Placement]] = field(default_factory=dict)
    credits_per_summer: dict[int, int] = field(default_factory=dict)
    graduation_semester: int | None = None

    def _terms_as_list(self) -> list[dict]:
        """Every term in the order a student would sit them, summer included.

        A summer term is emitted after the semester it follows and is labelled as such;
        it carries no semester number of its own, because it is not one.
        """
        def courses(ps):
            return [
                {
                    "code": p.course_code,
                    "label": p.label,
                    "slot_type": p.slot_type,
                    "credits": p.credits,
                    "origin": p.origin,
                    "reason": p.reason,
                }
                for p in ps
            ]

        out = []
        for s in sorted(set(self.semesters) | set(self.summers)):
            if s in self.semesters:
                out.append({
                    "semester": s,
                    "parity": "odd" if s % 2 else "even",
                    "is_summer": False,
                    "credits": self.credits_per_semester.get(s, 0),
                    "courses": courses(self.semesters[s]),
                })
            if s in self.summers:
                out.append({
                    "semester": s,
                    "parity": "summer",
                    "is_summer": True,
                    "label": f"Summer after semester {s}",
                    "credits": self.credits_per_summer.get(s, 0),
                    "courses": courses(self.summers[s]),
                })
        return out

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "summary": self.summary,
            "graduation_semester": self.graduation_semester,
            "semesters": self._terms_as_list(),
            "unplaced": [
                {"label": r.label or r.course_code, "slot_type": r.slot_type,
                 "credits": r.credits, "origin": r.origin, "reason": r.blocked_reason}
                for r in self.unplaced
            ],
            "rejections": [
                {"semester": r.semester, "code": r.course_code, "label": r.label,
                 "reason": r.reason}
                for r in self.rejections
            ],
            "risks": [{"level": f.level, "kind": f.kind, "message": f.message} for f in self.risks],
            "log": self.log,
        }


class Engine:
    # Placement classes, in the order the scheduler settles them within one semester.
    # A curriculum-pinned block has no freedom at all; a named core is a fixed
    # requirement; an elective slot can be filled many ways, so it is settled last, once
    # the courses that had to be there are placed and the remaining budget is known.
    CLASS_PINNED = 0
    CLASS_NAMED = 1
    CLASS_PROJECT = 2
    CLASS_POOLED = 3

    def __init__(self, cat: Catalogue):
        self.cat = cat
        self._assumed_met = 0
        self._minor_hosts_available = 0
        self._minor_slots_needed = 0
        self._minor_already_done = 0
        self._minor_retargeted = 0
        self._seen_rejections: set[tuple] = set()

    # --- requirement derivation -------------------------------------------

    def requirements(self, profile: StudentProfile, prefs: Preferences) -> list[Requirement]:
        cat = self.cat
        self._assumed_met = 0
        slots = cat.slots_by_programme.get(profile.programme_id, [])
        reqs: list[Requirement] = []
        used_generic: dict[str, int] = {}

        # Completed courses that do not match a named slot are credited against pooled
        # slots of the matching type, oldest slot first.
        named_codes = {s.course_code for s in slots if s.course_code}
        spare = sorted(profile.completed - named_codes)

        for i, s in enumerate(slots):
            if s.slot_type == "UNKNOWN":
                continue
            credits = s.credits or 9
            if s.course_code and s.slot_type in NAMED:
                if s.course_code in profile.completed:
                    continue
                reqs.append(
                    Requirement(
                        key=f"slot:{i}",
                        kind="NAMED",
                        slot_type=s.slot_type,
                        template_semester=s.semester_no,
                        credits=credits,
                        course_code=s.course_code,
                        label=s.course_code,
                    )
                )
            elif s.slot_type in POOLED:
                # A pooled slot belonging to a semester the student has already passed
                # is taken as met. The profile records completed course codes, not which
                # elective slot each one filled, so slot-level detail for past semesters
                # cannot be reconstructed; assuming otherwise would re-schedule electives
                # the student has already sat. The assumption is logged and flagged.
                if s.semester_no < profile.current_semester:
                    self._assumed_met += 1
                    continue
                n = used_generic.get(s.slot_type, 0)
                pool_for_type = [c for c in spare if self._matches_type(c, s.slot_type)]
                if n < len(pool_for_type):
                    used_generic[s.slot_type] = n + 1
                    continue
                used_generic[s.slot_type] = n + 1
                reqs.append(
                    Requirement(
                        key=f"slot:{i}",
                        kind="POOLED",
                        slot_type=s.slot_type,
                        template_semester=s.semester_no,
                        credits=credits,
                        label=s.slot_label,
                        slot_label=s.slot_label,
                    )
                )
            elif s.slot_type in NON_COURSE:
                if s.slot_type in PINNED and s.semester_no < profile.current_semester:
                    continue
                reqs.append(
                    Requirement(
                        key=f"slot:{i}",
                        kind="NON_COURSE",
                        slot_type=s.slot_type,
                        template_semester=s.semester_no,
                        credits=credits,
                        label=s.slot_label,
                        pinned=s.slot_type in PINNED,
                    )
                )
            elif s.slot_type == "ELECTIVE":
                # An elective whose basket contents the institute has not published.
                # It occupies its credits so the semester load stays truthful, but no
                # course can be named for it.
                if s.semester_no < profile.current_semester:
                    continue
                reqs.append(
                    Requirement(
                        key=f"slot:{i}",
                        kind="NON_COURSE",
                        slot_type="ELECTIVE",
                        template_semester=s.semester_no,
                        credits=credits,
                        label=s.slot_label,
                        pinned=True,
                    )
                )

        if prefs.target_minor:
            reqs = self._apply_minor(reqs, profile, prefs)
        return reqs

    # UG Manual 7.4: "A student may take Minor courses in OE, DE, ESO, or SCHEME slot."
    # A Minor therefore consumes elective capacity the template already provides; it does
    # not add credits on top of it. Retargeting existing slots rather than appending new
    # requirements is what keeps the semester loads and the feasibility verdict honest.
    MINOR_SLOT_PRIORITY = ("OE", "DE", "ESO", "SCHEME")

    def _apply_minor(self, reqs, profile, prefs):
        """Retarget elective slots onto the requested minor's courses.

        A compulsory entry is a group of alternatives — usually one code, but
        ``("CE643A", "CE644A")`` where the source says either satisfies it. A group with
        one code becomes a NAMED requirement; a group with several becomes a POOLED one
        over exactly those codes, so the scheduler picks whichever is actually reachable
        rather than committing to an arbitrary arm.
        """
        m = self.cat.minors.get(prefs.target_minor or "")
        if m is None:
            return reqs

        # A group already discharged by a completed course needs no slot.
        pending_groups = [
            g for g in m.compulsory if not any(c in profile.completed for c in g)
        ]
        already = sum(1 for c in m.choices if c in profile.completed)
        n_choice = max(0, m.choose_n - already)

        host_order = {t: i for i, t in enumerate(self.MINOR_SLOT_PRIORITY)}
        hosts = sorted(
            (r for r in reqs if r.kind == "POOLED" and r.slot_type in host_order),
            key=lambda r: (host_order[r.slot_type], r.template_semester, r.label),
        )
        self._minor_hosts_available = len(hosts)
        self._minor_slots_needed = len(pending_groups) + n_choice
        self._minor_already_done = len(m.compulsory) - len(pending_groups) + already
        self._minor_retargeted = 0

        out = list(reqs)
        for group in pending_groups:
            if not hosts:
                break
            host = hosts.pop(0)
            slot_name = host.slot_label or host.slot_type
            host.origin = "minor"
            self._minor_retargeted += 1
            if len(group) == 1:
                code = group[0]
                c = self.cat.courses.get(code)
                host.kind = "NAMED"
                host.course_code = code
                host.label = f"{code} (minor core, in {slot_name} slot)"
                host.slot_label = slot_name
                if c and c.credits:
                    host.credits = c.credits
            else:
                host.kind = "POOLED"
                host.pool = group
                host.label = (
                    f"{' or '.join(group)} (minor core, in {slot_name} slot)"
                )
                host.slot_label = slot_name
        for n in range(n_choice):
            if not hosts:
                break
            host = hosts.pop(0)
            host.origin = "minor"
            self._minor_retargeted += 1
            host.pool = tuple(c for c in m.choices if c not in profile.completed)
            host.label = (
                f"{m.stream} minor elective {n + 1} of {n_choice} "
                f"(in {host.slot_type} slot)"
            )
        return out

    def _matches_type(self, code: str, slot_type: str) -> bool:
        c = self.cat.courses.get(code)
        if c is None:
            return False
        if slot_type == "OE":
            return True
        if slot_type == "DE":
            return "DE" in c.types or (c.level or 0) >= 300
        if slot_type == "SCHEME":
            return "HSS" in c.types or (c.department in ("HSS", "ECO"))
        if slot_type == "MTB":
            return c.department in ("DOMS", "IME", "ECO")
        return True

    # --- candidate pools ---------------------------------------------------

    def template_courses(self, programme_id: str) -> set[str]:
        """Courses named by the programme's own template.

        These are core requirements, so they must not also be offered as candidates for
        an open-elective slot in the same programme.
        """
        return {
            s.course_code
            for s in self.cat.slots_by_programme.get(programme_id, [])
            if s.course_code
        }

    # Registrations that are not taught courses a student can simply choose. A project,
    # research or thesis registration needs a supervisor and a department's consent, and
    # the templates carry their own UGP slots for exactly that; offering one as an open
    # elective produces a plan the student could not act on. Matched on the title, which
    # is what the schedule publishes, rather than on a guessed code range.
    NON_ELECTIVE_TITLES = (
        "UNDER GRADUATE PROJECT",
        "UNDER GRADUATE RESEARCH",
        "UNDERGRADUATE PROJECT",
        "UNDERGRADUATE RESEARCH",
        "SPECIAL TOPICS",
        "INDEPENDENT STUDY",
        "SUPERVISED",
        "SEMINAR",
        "THESIS",
        "DISSERTATION",
        "M.TECH PROJECT",
        "PROJECT-",
        "PROJECT I",
        "PROJECT II",
    )

    def _is_taught_elective(self, c) -> bool:
        title = (c.title or "").upper()
        return not any(t in title for t in self.NON_ELECTIVE_TITLES)

    def pool_for(self, req: Requirement, profile: StudentProfile, prefs: Preferences,
                 taken: set[str]) -> list[str]:
        # An explicit pool — a minor basket, or a stated pair of alternatives — is the
        # source's own list and is used as given. Minor baskets legitimately contain
        # 600-level courses, so none of the general filtering below applies to them.
        if req.pool:
            return sorted(c for c in req.pool if c not in taken)
        cat = self.cat
        core = self.template_courses(profile.programme_id)
        out = []
        for code in sorted(cat.courses):
            if code in taken or code in core:
                continue
            c = cat.courses[code]
            if c.credits is None or c.credits <= 0:
                continue
            if not self._matches_type(code, req.slot_type):
                continue
            if not self._is_taught_elective(c):
                continue
            level = c.level or 0
            if req.slot_type in ("OE", "DE") and level < 200:
                # First-year institute-core courses are not elective material.
                continue
            if level >= 700:
                # 700-level entries are research-level registrations rather than taught
                # courses an undergraduate fills a template slot with.
                continue
            if req.slot_type == "DE" and c.department != profile.department:
                continue
            if req.slot_type == "DE" and level < 300:
                continue
            out.append(code)
        return out

    def interest_score(self, code: str, prefs: Preferences) -> int:
        if not prefs.career_interests:
            return 0
        c = self.cat.courses.get(code)
        if c is None:
            return 0
        text = f"{c.title} {c.department or ''}".lower()
        return sum(1 for kw in prefs.career_interests if kw.strip().lower() in text)

    # --- structural checks -------------------------------------------------

    def structural_blocks(self, profile: StudentProfile, prefs: Preferences) -> list[str]:
        """Reasons the request cannot work at all, independent of scheduling."""
        cat = self.cat
        blocks: list[str] = []

        # CPI gate. Honours variants carry a stated CPI criterion, so a profile below it
        # cannot be planned onto that programme however the courses are arranged.
        prog = cat.programmes.get(profile.programme_id, {})
        rule = cat.policy.get("honours.cpi_min")
        if rule and prog.get("kind") in ("BTH", "BSH"):
            threshold = rule["value_min"]
            if profile.cpi is not None and profile.cpi < threshold:
                blocks.append(
                    f"{prog.get('name', profile.programme_id)} is an Honours programme "
                    f"requiring a CPI of at least {threshold:.1f} ({rule['citation']}); "
                    f"this student's CPI is {profile.cpi:.2f}."
                )

        if prefs.target_minor:
            kinds = [
                cat.semester_kinds.get((profile.programme_id, s))
                for s in range(profile.current_semester, prefs.max_semesters + 1)
            ]
            course_sems = [k for k in kinds if k == "COURSEWORK"]
            if not course_sems:
                prog = cat.programmes.get(profile.programme_id, {})
                blocks.append(
                    f"{prog.get('name', profile.programme_id)} has no coursework semesters "
                    f"remaining from semester {profile.current_semester}: every remaining "
                    "semester is internship or thesis, so there is no elective capacity in "
                    "which minor courses could be scheduled. This is a structural property "
                    "of the programme, not a scheduling shortfall."
                )

            m = cat.minors.get(prefs.target_minor)
            if m is None:
                blocks.append(f"minor {prefs.target_minor} is not in the catalogue")
            else:
                by = profile.batch_year

                # DOAA: a Minor may be taken in any department except the student's own.
                own = cat.policy.get("minor.own_department")
                if own and m.department == profile.department:
                    blocks.append(
                        f"{m.label} is offered by {m.department}, which is this "
                        f"student's own department. {own['text']} ({own['citation']})"
                    )

                # Departmental selection committees state their own CPI cutoffs; these
                # are separate from the Honours criterion and apply per minor.
                if m.cpi_min is not None:
                    if profile.cpi is None:
                        blocks.append(
                            f"{m.label} applies a CPI cutoff of {m.cpi_min:.1f} "
                            f"({m.citation}), but no CPI was supplied, so eligibility "
                            "cannot be established."
                        )
                    elif profile.cpi < m.cpi_min:
                        blocks.append(
                            f"{m.label} applies a CPI cutoff of {m.cpi_min:.1f} "
                            f"({m.citation}); this student's CPI is {profile.cpi:.2f}."
                        )

                if by and m.batch_from:
                    lo = 2000 + int("".join(ch for ch in m.batch_from if ch.isdigit()))
                    if by < lo:
                        blocks.append(
                            f"{m.label} ({m.ugarc_variant}) applies to batches from "
                            f"{m.batch_from} onward; this student is {profile.batch}."
                        )
                if by and m.batch_to:
                    hi = 2000 + int("".join(ch for ch in m.batch_to if ch.isdigit()))
                    if by > hi:
                        blocks.append(
                            f"{m.label} ({m.ugarc_variant}) applies to batches up to "
                            f"{m.batch_to}; this student is {profile.batch}."
                        )

            for rule in cat.eligibility:
                if not rule["eligible"] and rule.get("programme_id") == profile.programme_id:
                    blocks.append(rule["text"])
                if rule["eligible"] and rule["option"] == "MINOR" and rule.get("department"):
                    m2 = cat.minors.get(prefs.target_minor)
                    if m2 and m2.department == rule["department"] and rule.get("batch_from"):
                        lo = 2000 + int("".join(ch for ch in rule["batch_from"] if ch.isdigit()))
                        if by and by < lo:
                            blocks.append(rule["text"])
        return blocks

    # --- the scheduler -----------------------------------------------------

    def solve(
        self,
        profile: StudentProfile,
        prefs: Preferences,
        _allow_fallback: bool = True,
    ) -> Roadmap:
        """Produce a roadmap.

        ``_allow_fallback`` guards the one recursive call this method makes. When a hard
        block is reported the engine re-solves without the requested minor so that an
        alternative pathway can still be shown; a block that is not minor-related (a CPI
        criterion, say) would otherwise fire again on that second pass and recurse
        without end.
        """
        cat = self.cat
        rm = Roadmap()
        self._seen_rejections = set()

        # A completed course is recognised however the student spells it. The templates
        # and the schedule exports disagree on suffixes — a student reading MTH111 off
        # their own template means the course the schedule calls MTH111M — and an
        # unrecognised completion is scheduled all over again.
        raw = set(profile.completed)
        canonical = cat.canonical_all(raw)
        if canonical != raw:
            profile = replace(profile, completed=canonical)
            renamed = sorted(c for c in raw if cat.canonical(c) != c)
            if renamed:
                rm.log.append(
                    f"{len(renamed)} completed course code(s) were matched to the "
                    "spelling the schedule uses: "
                    + ", ".join(f"{c} = {cat.canonical(c)}" for c in renamed[:6])
                    + ("…" if len(renamed) > 6 else "")
                    + "."
                )

        prog = cat.programmes.get(profile.programme_id)
        if prog is None:
            rm.verdict = INFEASIBLE
            rm.summary = f"Unknown programme {profile.programme_id}."
            return rm

        ceiling = min(prefs.max_credits, cat.policy["load.absolute_max"]["value_max"])
        manual_max, duration_rule = self._max_duration(profile)
        rm.log.append(
            f"Loaded {prog.get('name')} for batch {profile.batch}. "
            f"{len(profile.completed)} courses recorded as complete. "
            f"Per-semester ceiling {ceiling} credits "
            f"(student asked for {prefs.max_credits}; UG Manual 4.3.6.1 allows at most "
            f"{cat.policy['load.absolute_max']['value_max']})."
        )

        prog_kind = prog.get("kind")
        cpi_rule = cat.policy.get("honours.cpi_min")
        if profile.cpi is None:
            rm.log.append("No CPI supplied; CPI-dependent rules were not evaluated.")
            if prog_kind in ("BTH", "BSH"):
                rm.risks.append(
                    RiskFlag(
                        "HIGH", "cpi_unknown",
                        f"{prog.get('name')} carries a CPI criterion of "
                        f"{cpi_rule['value_min']:.1f}, but no CPI was supplied, so "
                        "eligibility could not be checked.",
                    )
                )
        else:
            rm.log.append(
                f"CPI {profile.cpi:.2f} recorded."
                + (
                    f" Honours programmes require {cpi_rule['value_min']:.1f} "
                    f"({cpi_rule['citation']}); this profile "
                    f"{'meets' if profile.cpi >= cpi_rule['value_min'] else 'does not meet'} it."
                    if cpi_rule and prog_kind in ("BTH", "BSH") else
                    " No CPI threshold applies to this programme."
                )
            )
            if cpi_rule and prog_kind not in ("BTH", "BSH") and profile.cpi >= cpi_rule["value_min"]:
                rm.risks.append(
                    RiskFlag(
                        "LOW", "honours_available",
                        f"CPI {profile.cpi:.2f} meets the {cpi_rule['value_min']:.1f} "
                        "criterion departments state for the Honours variant, which this "
                        "profile is not currently registered on.",
                    )
                )

        blocks = self.structural_blocks(profile, prefs)
        if blocks:
            rm.verdict = INFEASIBLE
            rm.summary = blocks[0]
            for b in blocks:
                rm.log.append(f"Hard block: {b}")
                rm.risks.append(RiskFlag("HIGH", "structural", b))
            # Where the block came from the requested minor, the degree itself is still
            # schedulable, so re-solve without the minor to offer a concrete alternative.
            if _allow_fallback and prefs.target_minor:
                alt = Preferences(**{**prefs.__dict__, "target_minor": None})
                base = self.solve(profile, alt, _allow_fallback=False)
                if base.verdict != INFEASIBLE:
                    rm.placements = base.placements
                    rm.semesters = base.semesters
                    rm.credits_per_semester = base.credits_per_semester
                    rm.graduation_semester = base.graduation_semester
                    rm.unplaced = base.unplaced
                    rm.log.append(
                        "Alternative pathway: the degree itself is scheduled below "
                        "without the requested minor."
                    )
            return rm

        reqs = self.requirements(profile, prefs)
        if prefs.target_minor:
            m = cat.minors.get(prefs.target_minor)
            short = self._minor_slots_needed - self._minor_hosts_available
            rm.log.append(
                f"Requested minor: {m.label} ({m.minor_id}), "
                f"{len(m.compulsory)} compulsory course(s) plus {m.choose_n} from a "
                f"basket of {len(m.choices)}, "
                f"{m.credits_min}-{m.credits_max} credits. Source: {m.citation}."
            )
            if self._minor_already_done:
                rm.log.append(
                    f"{self._minor_already_done} of the minor's course requirements are "
                    "already discharged by courses on the transcript."
                )
            rm.log.append(
                f"The minor needs {self._minor_slots_needed} further course slot(s); "
                f"{self._minor_hosts_available} elective slot(s) remain in the template. "
                "UG Manual 7.4 allows minor courses to occupy OE, DE, ESO or SCHEME "
                "slots, so they consume existing capacity rather than adding credits."
            )
            if short > 0:
                rm.risks.append(
                    RiskFlag(
                        "HIGH", "minor_slot_shortfall",
                        f"The minor requires {short} more elective slot(s) than the "
                        "template leaves. The remaining courses would have to be taken "
                        "as an overload, which needs DUGC and SUGC approval "
                        "(UG Manual 4.3.7).",
                    )
                )
            if m.seat_cap:
                rm.risks.append(
                    RiskFlag(
                        "HIGH", "minor_seat_cap",
                        f"{m.label} caps intake at {m.seat_cap} students "
                        f"({m.citation}). Selection is competitive, so a valid schedule "
                        "does not imply admission to the minor.",
                    )
                )
            if m.confidence == "TENTATIVE":
                rm.risks.append(
                    RiskFlag(
                        "HIGH", "minor_basket_tentative",
                        f"The basket recorded for {m.label} is not a settled requirement: "
                        "either the source states a rule over a course family rather than "
                        "a closed list, or too few of its published courses appear in the "
                        f"loaded schedules to reach the stated {m.credits_min} credits. "
                        f"{m.notes or ''} Confirm the current requirement with the "
                        f"department before relying on this plan.".strip(),
                    )
                )
            if m.unresolved:
                rm.risks.append(
                    RiskFlag(
                        "MEDIUM", "minor_basket_thinned",
                        f"{len(m.unresolved)} course(s) the source lists for "
                        f"{m.label} are not offered in any of the three loaded schedule "
                        f"exports ({', '.join(m.unresolved[:6])}"
                        + (" and others" if len(m.unresolved) > 6 else "")
                        + "), so they were excluded from the basket. Confirm the current "
                        "offering with the department; the requirement may be satisfiable "
                        "by a course this data cannot see.",
                    )
                )
            if m.notes and m.confidence != "TENTATIVE":
                rm.log.append(f"Note on {m.minor_id}: {m.notes}")
        if self._assumed_met:
            rm.log.append(
                f"{self._assumed_met} elective and scheme slots from semesters before "
                f"{profile.current_semester} are treated as already met. The profile lists "
                "completed course codes but not which slot each filled, so slot-level "
                "detail for past semesters cannot be reconstructed."
            )
            rm.risks.append(
                RiskFlag(
                    "MEDIUM",
                    "assumed_history",
                    f"{self._assumed_met} elective/scheme slots from semesters 1-"
                    f"{profile.current_semester - 1} were assumed complete rather than "
                    "verified against the transcript.",
                )
            )
        rm.log.append(
            f"{len(reqs)} requirements remain: "
            f"{sum(1 for r in reqs if r.kind == 'NAMED')} named courses, "
            f"{sum(1 for r in reqs if r.kind == 'POOLED')} elective slots, "
            f"{sum(1 for r in reqs if r.kind == 'NON_COURSE')} project or internship slots."
        )

        # A named requirement whose course appears in no loaded term can never be
        # placed. Separate those first so they are reported with a cause instead of
        # silently surviving every semester and ending up in a bare unplaced list.
        pending: list[Requirement] = []
        unschedulable: list[Requirement] = []
        for r in reqs:
            if r.kind == "NAMED" and r.course_code not in cat.courses:
                r.blocked_reason = (
                    f"{r.course_code} is named by the template but is not offered in any "
                    "loaded term, so no semester can host it. It is most likely a "
                    "renamed or discontinued code."
                )
                unschedulable.append(r)
            else:
                pending.append(r)
        if unschedulable:
            rm.log.append(
                f"{len(unschedulable)} required course(s) are not offered in any loaded "
                f"term: {', '.join(sorted(r.course_code for r in unschedulable))}."
            )
            rm.risks.append(
                RiskFlag(
                    "HIGH",
                    "course_not_offered",
                    f"{len(unschedulable)} template requirement(s) name courses absent "
                    "from all three schedule exports; confirm the current code with the "
                    "DUGC.",
                )
            )

        taken = set(profile.completed)
        start = max(1, profile.current_semester)
        last = prefs.max_semesters if prefs.allow_extension else prefs.target_semesters
        if manual_max is not None and last > manual_max:
            rm.log.append(
                f"Planning horizon capped at semester {manual_max} by "
                f"{duration_rule['citation']}: {duration_rule['name']} is "
                f"{manual_max} semesters for this programme and batch."
            )
            last = manual_max

        for sem in range(start, last + 1):
            if not pending:
                break
            kind = cat.semester_kinds.get((profile.programme_id, sem), "COURSEWORK")
            if kind != "COURSEWORK":
                fixed = [r for r in pending if r.pinned and r.template_semester == sem]
                for req in fixed:
                    p = Placement(
                        semester=sem, course_code=None, label=req.label,
                        slot_type=req.slot_type, credits=req.credits, origin=req.origin,
                        reason=f"{kind.lower().capitalize()} block fixed by the "
                               f"programme curriculum at semester {sem}.",
                    )
                    rm.placements.append(p)
                    rm.semesters.setdefault(sem, []).append(p)
                    rm.credits_per_semester[sem] = (
                        rm.credits_per_semester.get(sem, 0) + req.credits
                    )
                    pending.remove(req)
                rm.log.append(
                    f"Semester {sem} is {kind.lower()}; it carries no elective capacity, "
                    f"so no course can be scheduled into it."
                )
                continue

            budget = ceiling
            placed_here: list[Placement] = []
            # One pass per semester; each iteration picks the single best eligible
            # requirement, so that a placement can unlock others within the same pass.
            while True:
                choice = self._best_for_semester(
                    pending, sem, taken, budget, profile, prefs, rm
                )
                if choice is None:
                    break
                req, code, credits, reason = choice
                p = Placement(
                    semester=sem,
                    course_code=code,
                    label=req.label if code is None else code,
                    slot_type=req.slot_type,
                    credits=credits,
                    origin=req.origin,
                    reason=reason,
                )
                placed_here.append(p)
                rm.placements.append(p)
                budget -= credits
                pending.remove(req)
                if code:
                    taken.add(code)

            if placed_here:
                rm.semesters[sem] = placed_here
                rm.credits_per_semester[sem] = ceiling - budget
                rm.log.append(
                    f"Semester {sem} ({'odd' if sem % 2 else 'even'}): placed "
                    f"{len(placed_here)} items, {ceiling - budget} credits."
                )

            # The summer term sits between an even semester and the odd one that
            # follows. It is offered only when the student has asked for it.
            if prefs.allow_summer and sem % 2 == 0 and pending:
                self._schedule_summer(rm, pending, sem, taken, profile, prefs)

        for r in pending:
            if not r.blocked_reason:  # keep any specific cause already recorded
                r.blocked_reason = (
                    f"no semester between {start} and {last} had both the prerequisites "
                    f"satisfied and room within the {prefs.max_credits}-credit ceiling"
                )
        rm.unplaced = unschedulable + pending
        self._verdict(rm, profile, prefs)
        self._check_minor_discharged(rm, profile, prefs)
        self._risks(rm, profile, prefs, reqs)
        return rm

    def _schedule_summer(self, rm, pending, after_sem, taken, profile, prefs):
        """Fill the summer term that follows semester ``after_sem``.

        The summer term is not a regular semester (UG Manual 4.3.6). Three things follow
        and all three are enforced here rather than by reusing the semester pass:

        * its own credit ceiling — 27, or 29 for a student expecting to graduate by the
          end of it — which is far below the regular 65;
        * a much smaller course set. The published summer schedule carries 157 courses
          against roughly 750 in a regular term, so most requirements simply cannot be
          met in summer and the engine must not pretend otherwise;
        * it does not advance the graduation semester or count against the maximum
          programme duration, so it is recorded separately from ``semesters``.

        Only a requirement the summer schedule can actually satisfy is placed. Nothing
        is moved into summer to make a plan look shorter than it is.
        """
        cat = self.cat
        rule = cat.policy.get("summer.max")
        grad_rule = cat.policy.get("summer.max_graduating")
        ceiling = min(prefs.max_credits, (rule or {}).get("value_max") or 27)

        placed: list[Placement] = []
        budget = ceiling
        while True:
            choice = self._best_for_summer(pending, after_sem, taken, budget, profile, prefs)
            if choice is None:
                break
            req, code, credits, reason = choice
            p = Placement(
                semester=after_sem,
                course_code=code,
                label=req.label if code is None else code,
                slot_type=req.slot_type,
                credits=credits,
                origin=req.origin,
                reason=reason,
            )
            placed.append(p)
            rm.placements.append(p)
            budget -= credits
            pending.remove(req)
            if code:
                taken.add(code)

        if not placed:
            return
        rm.summers[after_sem] = placed
        rm.credits_per_summer[after_sem] = ceiling - budget
        cite = (rule or {}).get("citation", "UG Manual 4.3.6")
        rm.log.append(
            f"Summer term after semester {after_sem}: placed {len(placed)} course(s), "
            f"{ceiling - budget} credits. The summer-term maximum is {ceiling} credits "
            f"({cite})"
            + (
                f", or {grad_rule['value_max']} for a student graduating by the end of it"
                if grad_rule else ""
            )
            + ". The summer term is not a regular semester, so it does not advance the "
            "graduation semester or count against the maximum programme duration."
        )

    def _best_for_summer(self, pending, after_sem, taken, budget, profile, prefs):
        """The best requirement the summer schedule can actually satisfy, or None."""
        cat = self.cat
        best = best_key = None
        for req in pending:
            if req.credits > budget or req.pinned:
                continue
            # A slot the curriculum places later than this point is not overdue, so
            # there is no case for spending scarce summer credits on it.
            if req.template_semester > after_sem:
                continue

            if req.kind == "NAMED":
                code = req.course_code
                c = cat.courses.get(code)
                if c is None or (c.credits or 0) > budget:
                    continue
                if not cat.offered_in_summer(code):
                    continue
                node = cat.prereqs.get(code)
                if node and not satisfied(node, taken):
                    continue
                key = (self.CLASS_NAMED, req.template_semester, -cat.criticality(code), code)
                cand = (
                    req, code, c.credits or req.credits,
                    f"offered in the summer term; taken in the summer after semester "
                    f"{after_sem} to clear a semester-{req.template_semester} "
                    "requirement earlier than the regular schedule allows",
                )
            elif req.kind == "POOLED":
                pool = [
                    code for code in self.pool_for(req, profile, prefs, taken)
                    if cat.offered_in_summer(code)
                ]
                ranked = []
                for code in pool:
                    c = cat.courses.get(code)
                    if c is None or c.credits is None or c.credits > budget:
                        continue
                    node = cat.prereqs.get(code)
                    if node and not satisfied(node, taken):
                        continue
                    ranked.append(
                        (
                            0 if c.seen_current else 1,
                            -self.interest_score(code, prefs),
                            0 if c.is_ug else 1,
                            abs((c.credits or 0) - req.credits),
                            code,
                        )
                    )
                if not ranked:
                    continue
                ranked.sort()
                code = ranked[0][-1]
                c = cat.courses[code]
                key = (self.CLASS_POOLED, req.template_semester, 0, req.label)
                cand = (
                    req, code, c.credits or req.credits,
                    f"fills {req.label} from the summer schedule, freeing regular-"
                    "semester credits for requirements summer cannot cover",
                )
            else:
                continue

            if best_key is None or key < best_key:
                best_key, best = key, cand
        return best

    def _check_minor_discharged(self, rm, profile, prefs):
        """A requested minor must actually be scheduled, or the verdict must say so.

        Before the catalogue was curated, thirteen baskets carried no compulsory course
        and no ``choose_n``, so requesting one of them retargeted nothing and the plan
        came back FEASIBLE having scheduled not a single minor course. The verdict now
        depends on the minor's requirements being discharged, not merely on the degree
        being schedulable.
        """
        if not prefs.target_minor:
            return
        m = self.cat.minors.get(prefs.target_minor)
        if m is None:
            return
        placed = sum(1 for p in rm.placements if p.origin == "minor")
        outstanding = self._minor_slots_needed - placed
        if outstanding <= 0:
            if placed:
                rm.log.append(
                    f"All {self._minor_slots_needed} outstanding minor requirement(s) "
                    f"are scheduled; {m.label} completes with the degree."
                )
            return

        detail = (
            f"{outstanding} of the {self._minor_slots_needed} outstanding requirement(s) "
            f"for {m.label} could not be scheduled"
        )
        if self._minor_slots_needed > self._minor_hosts_available:
            detail += (
                f": the template leaves only {self._minor_hosts_available} elective "
                "slot(s) for them"
            )
        rm.log.append(f"Minor not discharged. {detail}.")
        rm.risks.append(RiskFlag("HIGH", "minor_incomplete", detail + "."))
        if rm.verdict == FEASIBLE:
            rm.verdict = FEASIBLE_WITH_ADJUSTMENT
            rm.summary = (
                f"The degree is schedulable by semester {rm.graduation_semester}, but "
                f"{detail}. The minor would not be earned on this plan."
            )
        elif rm.verdict == FEASIBLE_WITH_ADJUSTMENT:
            rm.summary += f" {detail[0].upper()}{detail[1:]}."

    def _best_for_semester(self, pending, sem, taken, budget, profile, prefs, rm):
        """Pick the single best requirement to place in ``sem``, or None.

        Selection is a four-level sort key, applied uniformly so that requirements of
        different kinds compare meaningfully against one another:

            (class, template_semester, -criticality, code)

        ``class`` puts a curriculum-pinned block first, then named courses, then
        projects, then elective slots — a named core is a fixed requirement, an elective
        slot can be filled many ways, so cores are settled first. Within a class the
        earliest template semester goes first, because that is the most overdue; then
        the course that unlocks the longest chain; then the code, so the result is
        reproducible rather than dependent on iteration order.
        """
        cat = self.cat
        best = None
        best_key = None
        for req in pending:
            if req.credits > budget:
                continue

            # The template semester is a floor for every requirement the template
            # states. A course may slip later — that is how a backlog or an extension
            # is expressed — but it is never pulled in front of the semester its own
            # programme curriculum assigns it. Without this the scheduler placed
            # ESC201, a third-semester Institute Core, in semester 1 purely because it
            # unlocks a long chain and nothing in the sparse prerequisite data held it
            # back. Course sequencing that the prerequisite graph does not capture is
            # carried by the template, and this is where that is honoured.
            if req.template_semester > sem:
                continue

            if req.kind == "NON_COURSE":
                if req.pinned:
                    if req.template_semester != sem:
                        continue
                    if req.slot_type == "ELECTIVE":
                        why = (
                            "Elective required by the curriculum; the institute has not "
                            "published the list of eligible courses, so the slot is "
                            "reserved but no course can be named."
                        )
                    else:
                        why = (
                            f"Placed by the published curriculum, which fixes it to "
                            f"semester {sem}."
                        )
                    key = (self.CLASS_PINNED, req.template_semester, 0, req.label)
                else:
                    key = (self.CLASS_PROJECT, req.template_semester, 0, req.label)
                    why = "Project slot; no scheduled course."
                cand = (req, None, req.credits, why)
            elif req.kind == "NAMED":
                code = req.course_code
                c = cat.courses.get(code)
                if c is None:
                    continue
                node = cat.prereqs.get(code)
                if node and not satisfied(node, taken):
                    need = missing(node, taken)
                    self._reject(rm, sem, code, req.label,
                                 f"prerequisite not yet met: needs {', '.join(need)}")
                    continue
                if not cat.offered_in(code, sem):
                    self._reject(rm, sem, code, req.label,
                                 f"offered in {c.parity.lower()} semesters only")
                    continue
                crit = cat.criticality(code)
                key = (self.CLASS_NAMED, req.template_semester, -crit, code)
                # The explanation leads with what actually decided the semester. Since
                # the template's semester is a floor, a course sits either exactly where
                # the curriculum puts it or at the first later semester that works, and
                # saying which is more use than restating the sort key.
                if req.template_semester == sem:
                    why = [f"template semester {sem}"]
                else:
                    why = [
                        f"template semester {req.template_semester}, deferred to {sem} "
                        "— the earliest with prerequisites, parity and credits satisfied"
                    ]
                why.append("prerequisites met" if node else "no prerequisites recorded")
                why.append(
                    "offered in both parities" if c.parity == BOTH
                    else f"{c.parity.lower()}-semester course"
                )
                if crit:
                    why.append(f"unlocks a {crit}-deep chain, so it is taken first")
                cand = (req, code, c.credits or req.credits, "; ".join(why))
            else:  # POOLED
                pool = self.pool_for(req, profile, prefs, taken)
                ranked = []
                # Why each candidate was discarded, so an empty pool can be explained
                # rather than reported as a bare failure. The problem statement requires
                # every important failure to name the constraint responsible.
                blocked: list[tuple[str, str]] = []
                for code in pool:
                    c = cat.courses.get(code)
                    if c is None or c.credits is None:
                        blocked.append((code, "not offered in any loaded term"))
                        continue
                    if c.credits > budget:
                        blocked.append(
                            (code, f"needs {c.credits} credits, only {budget} left this semester")
                        )
                        continue
                    node = cat.prereqs.get(code)
                    if node and not satisfied(node, taken):
                        need = missing(node, taken)
                        blocked.append(
                            (code, f"prerequisite not met: needs {', '.join(need)}")
                        )
                        continue
                    if not cat.offered_in(code, sem):
                        blocked.append((code, f"offered in {c.parity.lower()} semesters only"))
                        continue
                    # Preference order: stated interest first, then a course whose
                    # credit weight actually fits the slot the template drew, then
                    # something that unlocks later options, then code order so the
                    # result is reproducible.
                    # Preference order: stated interest first, then a course whose
                    # credit weight fits the slot the template drew, then one at a level
                    # appropriate to where the student is, then code order so the result
                    # is reproducible rather than alphabetically biased by accident.
                    # Semesters map onto course levels two to a year: semesters 1-2 sit
                    # at the 100 level, 3-4 at 200, and so on, so a fourth-year student
                    # is offered 400-level work rather than first-year or thesis codes.
                    stage_level = 100 * max(1, min(4, (sem + 1) // 2))
                    ranked.append(
                        (
                            # A course absent from the current published schedule has
                            # most likely been withdrawn; never prefer one while a
                            # currently offered alternative exists.
                            0 if c.seen_current else 1,
                            -self.interest_score(code, prefs),
                            # A postgraduate course is open to an undergraduate only by
                            # exception, so it is taken up only when nothing at
                            # undergraduate level fits the slot. Ranking this above the
                            # credit fit is deliberate: matching a 6-credit slot with a
                            # 600-level course purely because the credits agree put
                            # Computational Methods in Economics into a second-year
                            # semester.
                            0 if c.is_ug else 1,
                            abs((c.level or 0) - stage_level),
                            abs((c.credits or 0) - req.credits),
                            -cat.criticality(code),
                            code,
                        )
                    )
                if not ranked:
                    # Only worth explaining for a named basket; a general OE pool of
                    # hundreds of courses being briefly empty says nothing useful.
                    if req.pool:
                        for code, why in blocked[:6]:
                            self._reject(rm, sem, code, req.label, why)
                        if blocked:
                            req.blocked_reason = (
                                f"no course in the {req.label} basket could be placed: "
                                + "; ".join(f"{c} — {w}" for c, w in blocked[:3])
                            )
                    continue
                ranked.sort()
                code = ranked[0][-1]  # the code is always the last, tie-breaking, term
                c = cat.courses[code]
                score = self.interest_score(code, prefs)
                why = [f"fills {req.label}"]
                if score:
                    why.append(f"matches stated interest ({_count(score, 'keyword')})")
                why.append(
                    "offered in both parities" if c.parity == BOTH
                    else f"{c.parity.lower()}-semester course"
                )
                if not c.seen_current:
                    why.append(
                        "not present in the current published schedule, so availability "
                        "is uncertain"
                    )
                key = (self.CLASS_POOLED, req.template_semester, 0, req.label)
                cand = (req, code, c.credits or req.credits, "; ".join(why))

            if best_key is None or key < best_key:
                best_key, best = key, cand
        return best

    def _explain_extension(self, rm, profile: StudentProfile, prefs: Preferences) -> None:
        """Say why the plan overran, and name the ceiling that would prevent it.

        An extension has two quite different causes and a student needs to know which.
        Either the work genuinely does not fit in the remaining semesters, or it would
        fit on total credits but cannot be packed because courses are indivisible. The
        second case is the common one at IIT Kanpur, where almost every open elective is
        a nine-credit course: a fifty-credit ceiling admits five of them per semester,
        never six, so two semesters hold ninety credits of electives however they are
        arranged.

        Reporting only "completes in semester 9" leaves the student to guess. Naming the
        smallest ceiling that avoids the overrun turns it into a decision they can make.
        """
        placed = sum(rm.credits_per_semester.values())
        span = prefs.target_semesters - max(1, profile.current_semester) + 1
        if span < 1:
            return
        capacity = prefs.max_credits * span
        overflow = sum(
            c for s, c in rm.credits_per_semester.items() if s > prefs.target_semesters
        )
        if overflow <= 0:
            return

        if placed > capacity:
            rm.log.append(
                f"The extension is a capacity shortfall: {placed} credits remain and "
                f"{span} semesters at {prefs.max_credits} credits hold only {capacity}."
            )
            return

        # Total credits fit; the overrun is a packing effect. Find the lowest ceiling
        # that removes it, so the student is told what would actually work.
        rm.log.append(
            f"{placed} credits remain and {span} semesters at {prefs.max_credits} "
            f"credits would hold {capacity}, so the total is not the problem. The "
            f"overrun is caused by course sizes: {overflow} credits could not be packed "
            f"into the remaining semesters without exceeding the ceiling."
        )
        fix = self._minimum_ceiling(profile, prefs)
        if fix:
            rm.log.append(
                f"Raising the per-semester ceiling to {fix} credits would complete the "
                f"plan by semester {prefs.target_semesters}."
            )
            rm.risks.append(
                RiskFlag(
                    "MEDIUM",
                    "packing_extension",
                    f"The extra semester is a packing effect, not a shortage of credits. "
                    f"A ceiling of {fix} credits removes it; {prefs.max_credits} does not, "
                    "because almost every elective on offer is a nine-credit course.",
                )
            )

    def _minimum_ceiling(self, profile: StudentProfile, prefs: Preferences) -> int | None:
        """Lowest ceiling, up to the UG Manual maximum, that avoids the extension."""
        cap = self.cat.policy["load.absolute_max"]["value_max"]
        for ceiling in range(prefs.max_credits + 1, cap + 1):
            trial = Preferences(**{**prefs.__dict__, "max_credits": ceiling})
            probe = self.solve(profile, trial, _allow_fallback=False)
            if (
                probe.verdict == FEASIBLE
                and probe.graduation_semester
                and probe.graduation_semester <= prefs.target_semesters
            ):
                return ceiling
        return None

    def _max_duration(self, profile: StudentProfile):
        """The registration ceiling the UG Manual sets for this programme and batch."""
        cat = self.cat
        kind = (cat.programmes.get(profile.programme_id, {}) or {}).get("kind", "BT")
        by = profile.batch_year
        if kind == "DUAL":
            rid = "duration.max.DUAL"
        elif kind == "DOUBLE_MAJOR":
            rid = (
                "duration.max.DOUBLE_MAJOR.Y21"
                if by and by <= 2021 else "duration.max.DOUBLE_MAJOR.Y22"
            )
        else:
            rid = "duration.max.BT"
        rule = cat.policy.get(rid)
        return (rule["value_max"] if rule else None), rule

    def _reject(self, rm, sem, code, label, reason):
        # The semester pass re-examines every pending requirement after each placement,
        # so the same rejection surfaces repeatedly; keep one entry per cause.
        key = (sem, code, reason)
        if key in self._seen_rejections or len(rm.rejections) >= 400:
            return
        self._seen_rejections.add(key)
        rm.rejections.append(Rejection(sem, code, label, reason))

    def _verdict(self, rm, profile, prefs):
        # Graduation is counted in regular semesters. A summer term the student sits
        # after their final semester does not extend the degree by a semester, but it
        # does mean requirements are still outstanding at that point, so the summer
        # that follows the last regular semester still counts as work remaining.
        placed_sems = sorted(rm.semesters)
        rm.graduation_semester = placed_sems[-1] if placed_sems else None

        if rm.unplaced:
            rm.verdict = INFEASIBLE
            named = [r.label for r in rm.unplaced][:4]
            cause = rm.unplaced[0].blocked_reason
            rm.summary = (
                f"{len(rm.unplaced)} requirement(s) could not be scheduled: "
                f"{', '.join(named)}"
                + (f" and {len(rm.unplaced) - 4} more" if len(rm.unplaced) > 4 else "")
                + f". First cause: {cause}"
            )
        elif rm.graduation_semester and rm.graduation_semester > prefs.target_semesters:
            self._explain_extension(rm, profile, prefs)
            rm.verdict = FEASIBLE_WITH_ADJUSTMENT
            rm.summary = (
                f"All requirements are schedulable, but not by semester "
                f"{prefs.target_semesters}. The plan completes in semester "
                f"{rm.graduation_semester}."
            )
        else:
            rm.verdict = FEASIBLE
            rm.summary = (
                f"All requirements scheduled by semester {rm.graduation_semester}, "
                f"within the {prefs.max_credits}-credit ceiling."
                + (
                    f" This plan depends on {_count(len(rm.summers), 'summer term')}."
                    if rm.summers else ""
                )
            )

    def _risks(self, rm, profile, prefs, reqs):
        cat = self.cat
        if rm.graduation_semester and rm.graduation_semester > prefs.target_semesters:
            rm.risks.append(
                RiskFlag(
                    "HIGH",
                    "extension",
                    f"This plan depends on registering in semester {rm.graduation_semester}, "
                    f"beyond the standard {prefs.target_semesters}.",
                )
            )
        ambiguous = [
            p.course_code for p in rm.placements
            if p.course_code and cat.prereq_meta.get(p.course_code, {}).get("ambiguous")
        ]
        if ambiguous:
            rm.risks.append(
                RiskFlag(
                    "MEDIUM",
                    "ambiguous_prerequisite",
                    "Prerequisites for "
                    + ", ".join(sorted(set(ambiguous))[:5])
                    + " are written with mixed AND/OR and no grouping; they were read using "
                    "the variant-grouping rule and should be confirmed with the DUGC.",
                )
            )
        stale = sorted({
            p.course_code for p in rm.placements
            if p.course_code and not cat.courses[p.course_code].seen_current
        })
        if stale:
            rm.risks.append(
                RiskFlag(
                    "HIGH",
                    "not_in_current_schedule",
                    f"{len(stale)} scheduled course(s) do not appear in the current "
                    f"published schedule ({', '.join(stale[:5])}"
                    + (" and others" if len(stale) > 5 else "")
                    + "); they were seen only in an earlier year and may have been "
                    "withdrawn. Confirm with the department before relying on them.",
                )
            )

        odd_only = [
            p.course_code for p in rm.placements
            if p.course_code and cat.courses[p.course_code].parity != BOTH
        ]
        if odd_only:
            rm.risks.append(
                RiskFlag(
                    "MEDIUM",
                    "single_parity",
                    f"{len(set(odd_only))} scheduled courses were observed in one semester "
                    "parity only, across three schedule exports. If a department changes "
                    "when it offers them, those placements move.",
                )
            )
        if prefs.target_minor:
            seat = cat.policy.get("minor.seat_cap")
            rm.risks.append(
                RiskFlag(
                    "MEDIUM",
                    "minor_seats",
                    "Admission to a Minor is by seat availability alone and there is no "
                    "CPI criterion (UG Manual 7.4.1(e), 7.4.6). A department admits at "
                    "most 20 percent of its batch strength, so completing these courses "
                    "does not by itself secure the Minor."
                    if seat else "Minor admission depends on seat availability.",
                )
            )

        unnamed = [p for p in rm.placements if p.slot_type == "ELECTIVE"]
        if unnamed:
            rm.risks.append(
                RiskFlag(
                    "HIGH",
                    "unpublished_basket",
                    f"{len(unnamed)} elective slot(s) are reserved with credits but no "
                    "course named, because the institute has not published which courses "
                    "are eligible: "
                    + "; ".join(sorted({p.label for p in unnamed}))
                    + ". Confirm the list with the department before relying on this plan.",
                )
            )
        titled = [p for p in rm.placements if p.slot_type == "FIXED"]
        if titled:
            rm.risks.append(
                RiskFlag(
                    "MEDIUM",
                    "title_only_curriculum",
                    f"{len(titled)} course(s) in this programme are published by title "
                    "with no institute course code, so they cannot be checked against the "
                    "timetable or used to resolve prerequisites.",
                )
            )
        if rm.summers:
            n = sum(len(v) for v in rm.summers.values())
            n_summer = sum(1 for c in cat.courses.values() if c.summer)
            rm.risks.append(
                RiskFlag(
                    "HIGH",
                    "summer_dependency",
                    f"{_count(n, 'course')} scheduled across "
                    f"{_count(len(rm.summers), 'summer term')}. "
                    f"The published summer schedule carries {n_summer} courses "
                    f"against {len(cat.courses)} across the regular terms, and which of "
                    "them run is at the department's discretion and changes year to "
                    "year. If a summer course is not offered, everything after it moves.",
                )
            )
        elif prefs.allow_summer:
            rm.log.append(
                "Summer terms were permitted but none was used: no outstanding "
                "requirement is met by a course the published summer schedule carries."
            )
        rm.risks.append(
            RiskFlag(
                "MEDIUM",
                "seats",
                "Scheduling a course does not guarantee a seat. Seat caps are not "
                "published per course, so no allocation check was performed.",
            )
        )
        unresolved = [
            code for code, meta in cat.prereq_meta.items()
            if any(p not in cat.courses for p in meta["codes"])
            and any(pl.course_code == code for pl in rm.placements)
        ]
        if unresolved:
            rm.risks.append(
                RiskFlag(
                    "LOW",
                    "unresolved_prerequisite",
                    "Some scheduled courses list prerequisites that no loaded term offers "
                    f"({', '.join(sorted(unresolved)[:4])}); these are likely legacy codes.",
                )
            )
