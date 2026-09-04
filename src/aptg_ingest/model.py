"""Entity definitions for the APTG data model.

Design notes that matter downstream:

* ``Course.identity_mode`` distinguishes courses that carry an institute course code
  from those published by title only (B.Cyber). Title-only courses can be placed in a
  template but can never participate in prerequisite resolution.
* ``SemesterSpec.kind`` marks a semester as coursework, internship or thesis. Elective
  capacity is zero outside coursework, which is what makes B.Cyber's Semesters 5-8
  structurally unable to host a Minor without any special-casing in the engine.
* ``Programme.batch_from`` / ``batch_to`` carry batch-gated eligibility, e.g. the
  Department of Intelligent Systems Minor being open only to Y26 and later.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# --- enumerations -----------------------------------------------------------

CODED = "CODED"
TITLE_ONLY = "TITLE_ONLY"

COURSEWORK = "COURSEWORK"
INTERNSHIP = "INTERNSHIP"
THESIS = "THESIS"

ODD = "ODD"
EVEN = "EVEN"
BOTH = "BOTH"
# A course offered only in the summer term has no regular-semester parity at all. The
# summer term is not a regular semester (UG Manual 4.3.6), so summer availability is
# tracked separately rather than as a third parity value.
NEITHER = "NEITHER"
SUMMER = "SUMMER"

REGULAR = "REGULAR"
FIRST_HALF = "FIRST-HALF"
SECOND_HALF = "SECOND-HALF"


@dataclass
class Term:
    term_id: str          # "odd_recent" | "odd_prior" | "even"
    parity: str           # ODD | EVEN
    era: str              # coarse ordering label, e.g. "recent" / "prior"
    label: str            # academic term as published, e.g. "2026-27/I"
    is_current: bool      # part of the current academic year's published schedule
    source_file: str
    source_sha256: str


@dataclass
class Course:
    code: str | None      # None for TITLE_ONLY
    title: str
    department: str | None
    identity_mode: str    # CODED | TITLE_ONLY
    level: int | None     # numeric part of the code, e.g. 201 -> 201
    is_ug: bool | None    # level < 500
    ltps: str | None      # raw "3-0-0-0" component
    credits: int | None   # total units, the bracketed figure
    confidence: str
    source_file: str
    source_locator: str
    source_sha256: str


@dataclass
class CourseMaster:
    """A row of the institute's Approved Course Master: a course that *exists*.

    Distinct from `Course`, which is a course *observed being offered* in a loaded term.
    A code in the master but not in `course` is a real course this data cannot see
    running; a code in neither is simply wrong.
    """

    code: str
    branch: str
    title: str
    discontinued: bool
    confidence: str
    source_file: str
    source_locator: str
    source_sha256: str


@dataclass
class CodeAlias:
    """One course spelled two ways, and the evidence that they are the same course.

    Persisted rather than recomputed so that the mapping is auditable, and so that
    everything reading the database — the engine, the interface, a student typing a
    course code they remember — resolves codes the same way the build did.
    """

    from_code: str
    to_code: str
    method: str        # TITLE_MATCH (course master) | STEM (structural rule)
    confidence: str


@dataclass
class CourseEquivalence:
    """A legacy course replaced by a combination of new ones.

    Distinct from `CodeAlias`, which says two codes name the same course. ESC101 was
    modularised into ESC111 plus one of ESC112/ESC113 — a requirement no one-to-one
    mapping can carry. `expression` holds the replacement as a prerequisite AST.
    """

    equivalence_id: str
    name: str
    legacy_code: str
    member_codes: str          # comma-joined; the whole family, legacy code included
    expression: str            # JSON AST: the correct reading of the requirement
    text: str
    citation: str
    confidence: str


@dataclass
class Offering:
    course_code: str
    term_id: str
    slot_name: str | None
    section: str | None        # lecture section, e.g. "A"/"B" when a course runs in parallel
    course_types: str          # comma-joined role tags: DC, DE, OE, IC, Minor, PRF...
    mode: str                  # REGULAR | FIRST-HALF | SECOND-HALF
    instructors: str | None
    timing: str | None
    venue: str | None
    confidence: str
    source_file: str
    source_locator: str
    source_sha256: str


@dataclass
class CourseAvailability:
    """Derived: which terms a course is actually offered in.

    ``parity`` covers the regular semesters only, and is ``NEITHER`` for a course seen
    solely in the summer term. ``summer`` is separate because the summer term is not a
    regular semester (UG Manual 4.3.6): a course can run in odd semesters *and* summer,
    and the two facts constrain scheduling differently.
    """

    course_code: str
    parity: str      # ODD | EVEN | BOTH | NEITHER
    summer: bool     # offered in the summer term
    terms: str       # comma-joined term_ids
    seen_current: bool   # offered in a current-year term
    confidence: str


@dataclass
class PrereqEdge:
    """One prerequisite dependency, plus the expression it came from.

    ``group_id`` ties edges belonging to the same source expression together, and
    ``alternative_group`` marks codes that are alternatives to one another (an OR arm),
    so the engine can require exactly one of them.
    """

    course_code: str
    prereq_code: str
    group_id: str
    alternative_group: int | None
    resolved: bool             # does prereq_code exist in the course table?
    expr_raw: str
    expr_ast: str              # mechanical parse, JSON
    expr_normalized: str       # after variant grouping, JSON
    ambiguous: bool
    equivalence_id: str | None # set when a curated equivalence rewrote the reading
    confidence: str
    source_file: str
    source_locator: str
    source_sha256: str


@dataclass
class Programme:
    programme_id: str          # "ME-BT", "IS-BT", "BCYBER"
    name: str
    kind: str                  # BT | BS | BTH | BTM | DD | BCYBER
    department: str | None
    school: str | None
    batch_from: str | None     # e.g. "Y22"
    batch_to: str | None
    total_semesters: int
    confidence: str
    source_file: str
    source_locator: str
    source_sha256: str


@dataclass
class SemesterSpec:
    programme_id: str
    semester_no: int
    kind: str                  # COURSEWORK | INTERNSHIP | THESIS
    credits_min: int | None
    credits_max: int | None
    printed_total: str | None  # as printed in the template, for cross-checking
    confidence: str


@dataclass
class TemplateSlot:
    programme_id: str
    semester_no: int
    slot_label: str            # "ME301", "OE-1", "SCHEME HSS-2", "DE-1/UGP-1"
    slot_type: str             # IC | ESO | DC | DE | OE | SCHEME | UGP | INTERNSHIP | UNKNOWN
    course_code: str | None    # set when the slot names a specific course
    credits: int | None
    is_choice: bool            # slot offers alternatives, e.g. "DE-1/UGP-1"
    confidence: str
    source_file: str
    source_locator: str
    source_sha256: str


@dataclass
class CreditRule:
    programme_id: str
    course_type: str           # Institute Core | ESO | Department | Open Electives | SCHEME | Total
    recommended_min: int | None
    recommended_max: int | None
    required_min: int | None
    required_max: int | None
    confidence: str
    source_file: str
    source_locator: str
    source_sha256: str


@dataclass
class MinorBasket:
    """One minor *stream*, not one department.

    CSE publishes four streams, CHM three, HSS three and EE five, each with its own
    courses and credit total. A student reads one of them, so each is a row.

    ``compulsory_codes`` is comma-joined, and a group may carry alternatives separated
    by ``|`` where the source states them: ``CE643A|CE644A`` means either satisfies that
    requirement. ``choose_n`` courses are then taken from ``choice_codes``.
    """

    minor_id: str              # "CSE-AI", "AE-NEW_Y22"
    department: str
    title: str
    stream: str
    ugarc_variant: str         # ALL | OLD_Y21 | NEW_Y22
    batch_from: str | None
    batch_to: str | None
    compulsory_codes: str      # comma-joined groups, "|" between alternatives
    choose_n: int | None
    choice_codes: str          # comma-joined
    unresolved_codes: str      # published courses no loaded term offers, comma-joined
    source_course_count: int   # courses the source publishes, before resolution
    credits_min: int | None
    credits_max: int | None
    cpi_min: float | None      # departmental selection-committee cutoff, where stated
    seat_cap: int | None
    template_ref: str | None
    notes: str | None
    citation: str
    confidence: str
    source_file: str
    source_locator: str
    source_sha256: str


@dataclass
class PolicyRule:
    rule_id: str
    scope: str                 # UG | PROGRAMME | DEPARTMENT
    name: str
    value_min: int | None
    value_max: int | None
    unit: str | None
    text: str
    citation: str
    confidence: str


@dataclass
class ProgrammeEligibility:
    rule_id: str
    programme_id: str | None
    option: str                # MINOR | DOUBLE_MAJOR | DUAL_DEGREE
    department: str | None
    batch_from: str | None
    batch_to: str | None
    eligible: bool
    text: str
    citation: str
    confidence: str


@dataclass
class Dataset:
    """Everything the pipeline produces, before it is written to SQLite."""

    terms: list[Term] = field(default_factory=list)
    course_master: list[CourseMaster] = field(default_factory=list)
    code_aliases: list[CodeAlias] = field(default_factory=list)
    course_equivalences: list[CourseEquivalence] = field(default_factory=list)
    courses: list[Course] = field(default_factory=list)
    offerings: list[Offering] = field(default_factory=list)
    availability: list[CourseAvailability] = field(default_factory=list)
    prereq_edges: list[PrereqEdge] = field(default_factory=list)
    programmes: list[Programme] = field(default_factory=list)
    semester_specs: list[SemesterSpec] = field(default_factory=list)
    template_slots: list[TemplateSlot] = field(default_factory=list)
    credit_rules: list[CreditRule] = field(default_factory=list)
    minor_baskets: list[MinorBasket] = field(default_factory=list)
    policy_rules: list[PolicyRule] = field(default_factory=list)
    eligibility: list[ProgrammeEligibility] = field(default_factory=list)
