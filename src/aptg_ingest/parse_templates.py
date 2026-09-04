"""Parse DOAA department course templates into programmes, semester specs and slots.

Each template is a visual table with one column per semester, plus a credit table
stating the allowable range and the department's own totals. The grid is recovered by
:mod:`aptg_ingest.pdfgrid`; this module turns grid cells into slots.

Extraction is checked, never trusted. Credits summed per semester are compared against
the total printed at the foot of that column. A column that disagrees is marked
TENTATIVE and reported, so a wrong template is visible rather than silent.
"""
from __future__ import annotations

import re
from pathlib import Path

from .model import COURSEWORK, Programme, SemesterSpec, TemplateSlot
from .pdfgrid import Grid, build_grids, linear_grids
from .provenance import OBSERVED, TENTATIVE, Quarantine, Source

# Header labels appear as "Semester 1", "Semester1", "Semester-1" and "Sem. 1".
HEADER = re.compile(r"Sem(?:ester)?[\s.\-]{0,2}\d")
CODE = re.compile(r"[A-Z]{2,4}\d{3}[A-Z]?")
CREDIT = re.compile(r"[\(\[](\d+)(?:\s*-\s*(\d+))?[\)\]]")
TOTALS_ROW = re.compile(r"^\d{2}(?:\s*-\s*\d{2})?\**$")

SLOT_TYPES = [
    (re.compile(r"^OE-?\d*", re.I), "OE"),
    (re.compile(r"^DE[HS]?-?\d*", re.I), "DE"),
    (re.compile(r"^UGP-?\d*", re.I), "UGP"),
    (re.compile(r"^MTB-?\d*", re.I), "MTB"),
    (re.compile(r"SCHEME|HSS|EME|ELC|ETH|^PE\d", re.I), "SCHEME"),
    (re.compile(r"^ESO", re.I), "ESO"),
    (re.compile(r"^(ESC|MTH1|PHY1|CHM1|TA1|LIF1|MSO)", re.I), "IC"),
]

# Templates whose file covers an explicit batch window.
BATCH_WINDOWS = {
    "PHY_Y22_Y24": ("Y22", "Y24"),
    "PHY_Y25Batch_later": ("Y25", None),
}

PROGRAMME_KINDS = ("BTH", "BTM", "BSM", "DUAL", "BS", "BT")


def slot_body(label: str) -> str:
    """The slot label with its credit figure and footnote markers removed.

    Template cells read "ME301(9)" or "MTH 111 (6) **"; the credit suffix has to come
    off before the remainder can be tested for being a bare course code.
    """
    body = CREDIT.sub("", label)
    body = re.sub(r"[*†‡]+", "", body)
    return body.replace(" ", "").strip(" -/,")


def classify_slot(label: str) -> str:
    for pattern, kind in SLOT_TYPES:
        if pattern.search(label):
            return kind
    body = slot_body(label)
    if CODE.fullmatch(body):
        return "DC"
    # "EE200/IS202" and similar: a choice between named courses is still a course slot.
    parts = [p for p in body.split("/") if p]
    if len(parts) > 1 and all(CODE.fullmatch(p) for p in parts):
        return "DC"
    return "UNKNOWN"


def _clean(cell: str) -> str:
    """PDF extraction leaves stray spaces inside codes: "M E231" -> "ME231"."""
    return re.sub(r"(?<=[A-Z])\s+(?=[A-Z0-9])", "", cell).strip()


def _totals_agree(total: int, printed: str | None) -> bool:
    if not printed:
        return False
    nums = [int(n) for n in re.findall(r"\d+", printed)]
    return bool(nums) and min(nums) <= total <= max(nums)


def _programme_kind(caption: str, page) -> str:
    """Identify the programme variant a table belongs to.

    The caption directly above each table names it ("Template for BTH Program in ..."),
    which matters on pages that print several variants one after another; falling back
    to the page text would give every table on such a page the same label.
    """
    for text in (caption.upper(), (page.extract_text() or "")[:400].upper()):
        for kind in PROGRAMME_KINDS:
            if re.search(rf"\b{kind}\b", text):
                return kind
    return "BT"


def _split_rows(grid: Grid) -> tuple[list[list[str]], list[str | None]]:
    """Split grid rows into slot cells per column and the printed totals row."""
    per_col: list[list[str]] = [[] for _ in grid.columns]
    totals: list[str | None] = [None] * len(grid.columns)
    for row in grid.rows:
        if sum(1 for c in row if TOTALS_ROW.match(c.strip())) >= 3:
            for i, c in enumerate(row):
                if c.strip():
                    totals[i] = c.strip()
            break
        for i, cell in enumerate(row):
            text = cell.strip()
            if not text:
                continue
            # A cell holding only "(9)" is the wrapped credit of the slot above it.
            if per_col[i] and re.fullmatch(r"[\(\[]\d+(?:\s*-\s*\d+)?[\)\]]\s*\**", text):
                per_col[i][-1] += " " + text
            else:
                per_col[i].append(text)
    return per_col, totals


def _emit_grid(
    grid: Grid,
    page,
    psrc: Source,
    dept: str,
    batch: tuple[str | None, str | None],
    seen: set[str],
    out: tuple[list, list, list],
    q: Quarantine,
) -> None:
    programmes, specs, slots = out

    # Quality gate. A correctly recovered template is mostly credit-bearing slots
    # spread across its columns. A grid that fails this is a misread of some other
    # layout (AE and PHY print their templates as rotated or grouped tables), and
    # emitting it even as TENTATIVE would put fabricated structure into the database.
    per_col_probe, totals_probe = _split_rows(grid)
    cells = [c for col in per_col_probe for c in col]
    with_credits = sum(1 for c in cells if CREDIT.search(c))
    used_cols = sum(1 for col in per_col_probe if col)
    if not cells or with_credits / len(cells) < 0.30 or used_cols < 3:
        q.add(
            "template.grid_rejected",
            psrc,
            f"{dept}: {with_credits}/{len(cells)} cells carry credits across "
            f"{used_cols} columns; layout not recognised",
            grid.caption[:200],
        )
        return

    kind = _programme_kind(grid.caption, page)
    # A department that publishes separate templates per batch window produces genuinely
    # different programmes, so the window belongs in the identifier. PHY prints one
    # template for Y22-Y24 and another for Y25 onward; without the tag the two collide
    # and the later file silently overwrites the earlier one.
    tag = ""
    if batch[0] and batch[1]:
        tag = f"-{batch[0]}_{batch[1]}"
    elif batch[0]:
        tag = f"-{batch[0]}"
    elif batch[1]:
        tag = f"-upto{batch[1]}"
    programme_id = f"{dept}-{kind}{tag}"
    suffix = 2
    while programme_id in seen:
        programme_id = f"{dept}-{kind}{tag}-{suffix}"
        suffix += 1
    seen.add(programme_id)

    programmes.append(
        Programme(
            programme_id=programme_id,
            name=f"{dept} {kind}",
            kind=kind,
            department=dept,
            school=None,
            batch_from=batch[0],
            batch_to=batch[1],
            total_semesters=len(grid.columns),
            confidence=OBSERVED,
            **psrc.as_row(),
        )
    )

    per_col, totals = per_col_probe, totals_probe
    for i, cells in enumerate(per_col):
        sem_no = i + 1
        total = 0
        for cell in cells:
            label = _clean(cell)
            cm = CREDIT.search(label)
            credits = int(cm.group(1)) if cm else None
            if credits is not None:
                total += credits
            slot_type = classify_slot(label)
            body = slot_body(label)
            code_m = CODE.match(body.split("/")[0]) if body else None
            slots.append(
                TemplateSlot(
                    programme_id=programme_id,
                    semester_no=sem_no,
                    slot_label=label,
                    slot_type=slot_type,
                    course_code=(
                        code_m.group() if code_m and slot_type in ("DC", "IC", "ESO") else None
                    ),
                    credits=credits,
                    is_choice="/" in label,
                    confidence=OBSERVED,
                    **psrc.as_row(),
                )
            )
        printed = totals[i]
        ok = _totals_agree(total, printed)
        specs.append(
            SemesterSpec(
                programme_id=programme_id,
                semester_no=sem_no,
                kind=COURSEWORK,
                credits_min=total or None,
                credits_max=total or None,
                printed_total=printed,
                confidence=OBSERVED if ok else TENTATIVE,
            )
        )
        if not ok:
            q.add(
                "template.credit_mismatch",
                psrc,
                f"{programme_id} sem {sem_no}: extracted {total}, printed {printed!r}",
                " ; ".join(cells)[:400],
            )


def parse_template(path: str, q: Quarantine):
    """Return (programmes, semester_specs, slots) for one template PDF."""
    from pypdf import PdfReader

    stem = Path(path).stem
    dept = stem.split("-")[0].split("_")[0].upper()
    src = Source(path, "p.1")
    batch = BATCH_WINDOWS.get(stem, (None, None))

    out: tuple[list, list, list] = ([], [], [])
    seen: set[str] = set()

    for page_no, page in enumerate(PdfReader(path).pages, start=1):
        psrc = src.at(f"p.{page_no}")
        try:
            grids = build_grids(page, HEADER)
        except Exception as exc:  # noqa: BLE001 - extraction is best-effort by design
            q.add("template.grid_error", psrc, f"{type(exc).__name__}: {exc}", stem)
            continue
        if not grids:
            # No usable geometry on this page. Fall back to reading the rows linearly;
            # the credit check downstream still decides whether the result is believed.
            grids = linear_grids(page, HEADER)
            if grids:
                q.add(
                    "template.linear_fallback",
                    psrc,
                    f"{dept}: page has no column geometry; rows read in reading order",
                    stem,
                )
        if not grids:
            q.add("template.no_grid", psrc, "no semester header row found", stem)
            continue
        for grid in grids:
            _emit_grid(grid, page, psrc, dept, batch, seen, out, q)

    return out
