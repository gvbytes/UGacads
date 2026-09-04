"""Reconstruct tabular layout from a PDF page using glyph coordinates.

The DOAA templates are visual tables: eight semester columns of course slots. Plain
text extraction interleaves the columns and is unusable.

The reliable structural signal is the *gutter*: a vertical band of the page that no
glyph ever occupies. Columns are the regions between gutters. Working from gutters
avoids the failure mode of merging text runs by estimated width, which readily bridges
two adjacent columns and corrupts both.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

Y_TOLERANCE = 2.0      # runs within this vertical distance belong to one line
MIN_GUTTER = 7.0       # a blank vertical band at least this wide separates columns
CHAR_EM = 0.55         # mean advance per character, in em


@dataclass
class Run:
    x: float
    y: float
    text: str
    size: float = 10.0

    @property
    def x_end(self) -> float:
        return self.x + len(self.text) * self.size * CHAR_EM


@dataclass
class Grid:
    columns: list[float] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    caption: str = ""     # nearest text above the header row, names the programme variant


def page_runs(page) -> list[Run]:
    """Text runs with device-space positions and effective font sizes.

    A run's position and size are only meaningful once the text matrix is composed with
    the current graphics matrix. Several department templates draw their table under a
    scaled CTM: reading ``tm`` alone reports every run at ``size`` 1.0 and squeezes eight
    semester columns into a thirteen-point span, which destroys the column ruler.
    """
    runs: list[Run] = []

    def visitor(text, cm, tm, font, size):
        t = text.strip()
        if not t:
            return
        # Compose tm with cm: [a b c d e f] as PDF matrices, taking the translation
        # through the graphics transform and the scale from both.
        a, b, c, d, e, f = tm
        ca, cb, cc, cd, ce, cf = cm
        x = ca * e + cc * f + ce
        y = cb * e + cd * f + cf
        # Horizontal scale is the composed x-scale; fall back to the raw size when the
        # composition degenerates so a well-formed page is never made worse.
        sx = (a * ca + b * cc)
        eff = abs((size or 10.0) * sx)
        if eff < 0.5 or eff > 200:
            eff = abs(size * (a if a else 1.0)) or 10.0
        runs.append(Run(round(x, 1), round(y, 1), t, eff))

    page.extract_text(visitor_text=visitor)
    return runs


def build_lines(runs: list[Run]) -> list[list[Run]]:
    lines: list[list[Run]] = []
    for run in sorted(runs, key=lambda r: (-r.y, r.x)):
        if lines and abs(lines[-1][0].y - run.y) <= Y_TOLERANCE:
            lines[-1].append(run)
        else:
            lines.append([run])
    for line in lines:
        line.sort(key=lambda r: r.x)
    return lines


def line_chars(line: list[Run]) -> tuple[str, list[float]]:
    """Concatenate a line into text, keeping the x position of every character.

    Character positions are interpolated across each run's advance. This is only used
    to locate header labels, where sub-character precision is irrelevant.
    """
    text_parts: list[str] = []
    xs: list[float] = []
    ordered = [r for r in sorted(line, key=lambda r: r.x) if r.text]
    for idx, run in enumerate(ordered):
        # Prefer the true advance implied by the next run's origin; the per-character
        # estimate is only a fallback for the last run on the line.
        width = run.x_end - run.x
        if idx + 1 < len(ordered):
            gap = ordered[idx + 1].x - run.x
            if gap > 0:
                width = gap
        step = width / len(run.text)
        for i, ch in enumerate(run.text):
            text_parts.append(ch)
            xs.append(run.x + i * step)
    return "".join(text_parts), xs


def anchor_columns(
    line: list[Run], pattern: re.Pattern, min_gap: float = 25.0
) -> list[float]:
    """Column x-positions, taken from where each header label starts on the header line.

    Works regardless of how the extractor fragments the header text: "Semester 1" may
    arrive as one run or as ten single-glyph runs, and the position of the match's
    first character is the column's left edge either way.
    """
    text, xs = line_chars(line)
    starts: list[float] = []
    for m in pattern.finditer(text):
        x = xs[m.start()]
        if starts and x - starts[-1] < min_gap:
            continue
        starts.append(x)
    return starts


def _col_of(x: float, columns: list[float]) -> int:
    idx = 0
    for i, cx in enumerate(columns):
        if x >= cx - 4:
            idx = i
    return idx


def row_text(line: list[Run], columns: list[float]) -> list[str]:
    """Concatenate runs per column. Runs are never merged across a column boundary."""
    cells = [[] for _ in columns]
    for run in line:
        cells[_col_of(run.x, columns)].append(run)
    out = []
    for group in cells:
        group.sort(key=lambda r: r.x)
        text = ""
        prev_end: float | None = None
        for r in group:
            if text and prev_end is not None and r.x - prev_end > 2.0:
                text += " "
            text += r.text
            prev_end = r.x_end
        out.append(text.strip())
    return out


def find_header_lines(lines: list[list[Run]], pattern: re.Pattern) -> list[int]:
    """Every line that names at least three semesters, in page order.

    A single page can carry more than one template table (a department often prints its
    BT, BTH and BTM variants together), so all candidates are returned.
    """
    out = []
    for i, line in enumerate(lines):
        text, _ = line_chars(line)
        if len(pattern.findall(text)) >= 3:
            out.append(i)
    return out


def build_grids(page, header_pattern: re.Pattern) -> list[Grid]:
    """Every template table on a page, each ending where the next one begins."""
    runs = page_runs(page)
    if not runs:
        return []
    lines = build_lines(runs)
    headers = find_header_lines(lines, header_pattern)
    grids: list[Grid] = []
    for n, h in enumerate(headers):
        columns = anchor_columns(lines[h], header_pattern)
        if len(columns) < 3:
            continue
        stop = headers[n + 1] if n + 1 < len(headers) else len(lines)
        caption = ""
        for back in range(h - 1, max(-1, h - 5), -1):
            text, _ = line_chars(lines[back])
            if len(text.strip()) > 8:
                caption = text.strip()
                break
        grid = Grid(
            columns=columns, headers=row_text(lines[h], columns), caption=caption
        )
        for line in lines[h + 1:stop]:
            grid.rows.append(row_text(line, columns))
        grids.append(grid)
    return grids


CELL = re.compile(r"[^()]*?\(\s*\d+(?:\s*[-\u2013]\s*\d+)?\s*\)\s*\**")


def linear_grids(page, header_pattern: re.Pattern) -> list[Grid]:
    """Recover a table from pages that carry no usable geometry.

    Some department templates (EE and ES among them) emit each table *row* as a single
    text run, with every run reported at the same y and at x positions that increment
    sequentially rather than tracking the columns. There is no ruler to recover, so the
    geometric path returns nothing.

    What those runs do preserve is reading order: a row's cells appear left to right, and
    the rows are left-packed, so the nth cell of a row belongs to the nth semester. Cells
    are split on their credit figure, which every slot carries.

    This is a weaker signal than geometry and is used only where geometry fails. The
    caller still checks the recovered credits against the totals printed on the template,
    so a misaligned row is rejected rather than believed.
    """
    runs = page_runs(page)
    if not runs:
        return []
    lines = build_lines(runs)
    grids: list[Grid] = []
    for line in lines:
        header_run = None
        for i, r in enumerate(line):
            if len(header_pattern.findall(r.text)) >= 3:
                header_run = i
                break
        if header_run is None:
            continue
        ncols = len(header_pattern.findall(line[header_run].text))
        grid = Grid(
            columns=[float(i) for i in range(ncols)],
            headers=header_pattern.findall(line[header_run].text),
            caption="",
        )
        for r in line[header_run + 1:]:
            cells = [c.strip() for c in CELL.findall(r.text) if c.strip()]
            if not cells:
                continue
            row = [""] * ncols
            for i, cell in enumerate(cells[:ncols]):
                row[i] = cell
            grid.rows.append(row)
        if grid.rows:
            grids.append(grid)
    return grids


def build_grid(page, header_pattern: re.Pattern) -> Grid | None:
    grids = build_grids(page, header_pattern)
    return grids[0] if grids else None
