"""Parse Pingala course-schedule exports into courses, offerings and prerequisite edges."""
from __future__ import annotations

import re

from . import parse_prereq
from .model import (
    BOTH,
    NEITHER,
    SUMMER,
    CODED,
    EVEN,
    ODD,
    Course,
    CourseAvailability,
    Offering,
    PrereqEdge,
    Term,
)
from .provenance import DERIVED, OBSERVED, TENTATIVE, Quarantine, Source

# Column layout of the Pingala export.
C_SNO, C_BR, C_NAME, C_PRE, C_SLOT, C_CRED = "A", "B", "C", "D", "E", "F"
C_INSTR, C_EMAIL = "G", "H"
TIME_COLS = ("I", "K", "M")
VENUE_COLS = ("J", "L", "N")

# Titles end with the course code, optionally followed by a lecture-section suffix
# such as "/A" or "/B" when a course runs in parallel sections.
CODE_IN_TITLE = re.compile(r"\(([A-Z]{2,4}\d{3}[A-Z]?)\)\s*(?:/\s*([A-Za-z0-9]+))?\s*$")
CREDIT_RE = re.compile(r"^\s*(\d+)-(\d+)-(\d+)-(\d+)\s*\((\d+)\)\s*$")
MODE_RE = re.compile(r"/\s*(REGULAR|FIRST-HALF|SECOND-HALF)\s*$", re.I)
# Leading slot identifier, e.g. "SLOT-3-1", "OE-1", "PG-13", "CORE 12", "SLOT-BLANK".
SLOT_PREFIX = re.compile(r"^(SLOT-\S+|OE-\d+|PG-\d+\w*|CORE\s*\d+|LEC-\S+)\s*", re.I)

KNOWN_TYPES = {
    "DC", "DE", "OE", "IC", "PRF", "MINOR", "ESO", "HSS", "UGP-1", "UGP-2",
    "UGP-3", "UGP-4", "THESIS", "PROJECT",
}


def _split_slot(raw: str) -> tuple[str | None, str, str]:
    """Return (slot_name, course_types, mode) from the 'Slot Name/Course Type' cell."""
    mode = REGULAR_DEFAULT
    m = MODE_RE.search(raw)
    if m:
        mode = m.group(1).upper()
        raw = raw[: m.start()]
    head = raw.strip().rstrip("/").strip()
    slot_name = None
    sm = SLOT_PREFIX.match(head)
    if sm:
        slot_name = sm.group(1).strip()
        head = head[sm.end():]
    types = []
    for tok in re.split(r"[,\s]+", head):
        tok = tok.strip().strip(",")
        if not tok:
            continue
        if tok.upper() in KNOWN_TYPES or re.fullmatch(r"(DE|OE|UGP)-\d+", tok.upper()):
            types.append(tok.upper())
    return slot_name, ",".join(dict.fromkeys(types)), mode


REGULAR_DEFAULT = "REGULAR"


def parse_schedule(path: str, term_id: str, parity: str, era: str,
                   label: str, is_current: bool, q: Quarantine):
    """Yield (term, courses, offerings, prereq_edges) for one export file."""
    from .xlsx import read_sheets

    src = Source(path, "Sheet1")
    sheets = read_sheets(path)
    sheet_name, rows = next(iter(sheets.items()))
    term = Term(term_id, parity, era, label, is_current, src.name, src.sha256)

    courses: dict[str, Course] = {}
    offerings: list[Offering] = []
    edges: list[PrereqEdge] = []

    for row_no, cells in rows:
        title = cells.get(C_NAME, "").strip()
        if not title or title.lower().startswith("course name"):
            continue
        rsrc = src.at(f"{sheet_name}!row={row_no}")

        m = CODE_IN_TITLE.search(title)
        if not m:
            q.add("schedule.no_course_code", rsrc, "title has no trailing (CODE)", title)
            continue
        code = m.group(1)
        section = m.group(2)
        plain_title = title[: m.start()].strip()

        raw_credits = cells.get(C_CRED, "").strip()
        cm = CREDIT_RE.match(raw_credits)
        if cm:
            ltps = "-".join(cm.group(1, 2, 3, 4))
            credits = int(cm.group(5))
        else:
            ltps, credits = None, None
            q.add("schedule.bad_credits", rsrc, "credit string did not match L-T-P-S(N)", raw_credits)

        level_m = re.search(r"\d{3}", code)
        level = int(level_m.group()) if level_m else None

        if code not in courses:
            courses[code] = Course(
                code=code,
                title=plain_title,
                department=cells.get(C_BR),
                identity_mode=CODED,
                level=level,
                is_ug=(level < 500) if level is not None else None,
                ltps=ltps,
                credits=credits,
                confidence=OBSERVED,
                **rsrc.as_row(),
            )

        slot_name, course_types, mode = _split_slot(cells.get(C_SLOT, ""))
        timing = " | ".join(cells[c] for c in TIME_COLS if cells.get(c))
        venue = " | ".join(cells[c] for c in VENUE_COLS if cells.get(c))
        offerings.append(
            Offering(
                course_code=code,
                term_id=term_id,
                slot_name=slot_name,
                section=section,
                course_types=course_types,
                mode=mode,
                instructors=cells.get(C_INSTR),
                timing=timing or None,
                venue=venue or None,
                confidence=OBSERVED,
                **rsrc.as_row(),
            )
        )

        raw_pre = cells.get(C_PRE, "").strip()
        if raw_pre:
            parsed = parse_prereq.parse(raw_pre)
            if parsed.repairs:
                q.add(
                    "prereq.repaired", rsrc,
                    "; ".join(parsed.repairs), raw_pre,
                )
            if parsed.error:
                q.add("prereq.parse_error", rsrc, parsed.error, raw_pre)
            else:
                groups = parse_prereq.alternative_groups(parsed.normalized)
                gid = f"{term_id}:{code}:{row_no}"
                for pc in parsed.codes:
                    edges.append(
                        PrereqEdge(
                            course_code=code,
                            prereq_code=pc,
                            group_id=gid,
                            alternative_group=(groups.get(pc) if groups.get(pc, -1) > 0 else None),
                            resolved=False,  # filled in once all courses are known
                            expr_raw=raw_pre,
                            expr_ast=parsed.ast_json(),
                            expr_normalized=parsed.normalized_json(),
                            ambiguous=parsed.ambiguous,
                            equivalence_id=None,
                            confidence=OBSERVED,
                            **rsrc.as_row(),
                        )
                    )

    return term, list(courses.values()), offerings, edges


def compute_availability(terms: list[Term], offerings: list[Offering]) -> list[CourseAvailability]:
    """Derive per-course semester parity from which terms actually offer it.

    Confidence distinguishes a course the current published schedule still carries from
    one seen only in an older export. The latter has most likely been withdrawn, so it
    is marked TENTATIVE rather than treated as reliably available; scheduling one is a
    risk the interface has to show rather than hide.
    """
    parity_of = {t.term_id: t.parity for t in terms}
    current = {t.term_id for t in terms if t.is_current}
    seen: dict[str, set[str]] = {}
    term_ids: dict[str, set[str]] = {}
    for o in offerings:
        seen.setdefault(o.course_code, set()).add(parity_of[o.term_id])
        term_ids.setdefault(o.course_code, set()).add(o.term_id)
    out = []
    for code, terms_seen in sorted(seen.items()):
        # Summer is not a regular semester, so it is recorded alongside the parity
        # rather than as one of its values: a course can run in odd semesters and in
        # summer, and only the former constrains which semester can host it.
        regular = terms_seen & {ODD, EVEN}
        if regular == {ODD, EVEN}:
            parity = BOTH
        elif regular:
            parity = next(iter(regular))
        else:
            parity = NEITHER
        seen_current = bool(term_ids[code] & current)
        out.append(
            CourseAvailability(
                course_code=code,
                parity=parity,
                summer=SUMMER in terms_seen,
                terms=",".join(sorted(term_ids[code])),
                seen_current=seen_current,
                confidence=DERIVED if seen_current else TENTATIVE,
            )
        )
    return out
