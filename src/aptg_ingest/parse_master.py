"""Parse the institute's Approved Course Master.

`data/policy/approved_course_master.pdf` is the registry of every course the institute
has approved: branch, title and the canonical course ID. It is the only source that
states which codes *exist*, as distinct from which are offered in a given term, and it
carries the suffixed form of every code (``ESO207A``, ``AE201A``, ``CE241A``) that the
schedule exports print without a suffix or omit entirely.

That distinction matters twice over:

* **Aliasing by title.** Two codes that share a stem and carry the same title in the
  master are the same course under two numbering schemes. This is stronger evidence than
  the structural stem rule in :mod:`aptg_ingest.codes`, which can only say that a
  suffixed and an unsuffixed code *might* match, and can never confirm a pair like
  ``AE201A`` / ``AE201M`` where both are suffixed.
* **Existence without availability.** A template or minor course present in the master
  but in no loaded term is a course that exists and simply is not on the three schedules
  held here. Reporting that differently from a code that exists nowhere at all is the
  difference between "confirm the term it runs in" and "this code is wrong".

The master also marks withdrawn courses with the title ``DISCONTINUED``, which is
recorded so the engine never schedules one.
"""
from __future__ import annotations

import re

from .model import CourseMaster
from .provenance import OBSERVED, Quarantine, Source

# A row reads "26 AE COMBUSTION DIAGNOSTICS AE666A 0", with the title free to wrap.
ROW = re.compile(
    r"(?:^|\n)\s*(\d{1,5})\s+([A-Z]{2,5})\s+(.+?)\s+([A-Z]{2,5}\d{3}[A-Z]?)\.?(?=[\s<]|$)",
    re.S,
)
DISCONTINUED = "DISCONTINUED"


def parse_master(path: str, q: Quarantine) -> list[CourseMaster]:
    from pypdf import PdfReader

    reader = PdfReader(path)
    pages = [(i + 1, p.extract_text() or "") for i, p in enumerate(reader.pages)]
    src = Source(path, "p.1")

    def page_of(offset: int) -> int:
        run = 0
        for pno, text in pages:
            run += len(text) + 1
            if offset < run:
                return pno
        return len(pages)

    full = "\n".join(t for _, t in pages)
    flat = re.sub(r"[ \t]+", " ", full)
    # A title wrapped onto a new line resumes in lower case or an opening bracket.
    flat = re.sub(r"\n(?=[a-z(])", " ", flat)

    out: list[CourseMaster] = []
    seen: dict[str, str] = {}
    for m in ROW.finditer(flat):
        _sno, branch, title, code = m.groups()
        title = " ".join(title.split())
        if len(title) > 120:
            # Runaway match: the row's content column bled into the title.
            q.add("master.row_rejected", src.at(f"p.{page_of(m.start())}"),
                  f"{code}: title of {len(title)} chars is not a course title", title[:200])
            continue
        if code in seen:
            if seen[code] != title:
                q.add("master.duplicate_code", src.at(f"p.{page_of(m.start())}"),
                      f"{code} appears twice with different titles; the first is kept",
                      f"{seen[code]} / {title}")
            continue
        seen[code] = title
        out.append(
            CourseMaster(
                code=code,
                branch=branch,
                title=title,
                discontinued=title.upper().startswith(DISCONTINUED),
                confidence=OBSERVED,
                **src.at(f"p.{page_of(m.start())}").as_row(),
            )
        )
    out.sort(key=lambda r: r.code)
    return out


def _norm_title(t: str) -> str:
    """Titles differ in punctuation and spacing between the master and the schedule."""
    return re.sub(r"[^A-Z0-9]", "", t.upper())


# Words that carry no identifying force, so sharing them says nothing about two titles
# naming the same course.
_STOPWORDS = frozenset(
    "A AN AND AS AT BY FOR FROM IN INTO OF ON OR THE TO WITH INTRODUCTION INTRO "
    "ADVANCED BASIC PRINCIPLES FUNDAMENTALS TOPICS SPECIAL I II III IV".split()
)


def _tokens(title: str) -> set[str]:
    # Two spelling differences must not read as different courses: British and American
    # forms (the schedule prints ORGANISATION where the master prints ORGANIZATION) and
    # singular against plural (EE320A is "Principles of Communication", EE320 is
    # "Principles of Communications" — one course).
    words = re.split(r"[^A-Z0-9]+", (title or "").upper().replace("Z", "S"))
    out = set()
    for w in words:
        if not w or w in _STOPWORDS or len(w) <= 2:
            continue
        out.add(w[:-1] if len(w) > 3 and w.endswith("S") else w)
    return out


def titles_conflict(a: str, b: str) -> bool:
    """Do two titles name courses that cannot be the same?

    Deliberately permissive. Departments rename and re-word courses constantly —
    "Process Control" becomes "Process Dynamics and Control", "Computer Organization"
    becomes "Introduction to Computer Organisation" — and refusing those would undo the
    aliasing the whole pipeline depends on. So a conflict is declared only in the
    unambiguous case: the two titles share **no** significant word at all. That is what
    separates "Microelectronics-I" from "Analog Electronics", and "Soil Mechanics" from
    "Foundation Design" — pairs the structural stem rule would otherwise merge into one
    course.
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False  # nothing to judge on; leave the structural rule alone
    return not (ta & tb)


def title_aliases(master: list[CourseMaster], course_titles: dict[str, str]) -> dict[str, str]:
    """Map a master code to the schedule code denoting the same course.

    A pair qualifies when the two codes share a stem *and* carry the same normalised
    title. Requiring both is what lets this confirm ``AE201A`` -> ``AE201M``, which the
    structural rule must refuse, while still declining to merge two genuinely different
    courses that happen to share a number.
    """
    from .codes import stem

    by_stem: dict[str, list[str]] = {}
    for code in sorted(course_titles):
        by_stem.setdefault(stem(code), []).append(code)

    out: dict[str, str] = {}
    for row in master:
        if row.code in course_titles or row.discontinued:
            continue
        want = _norm_title(row.title)
        for candidate in by_stem.get(stem(row.code), ()):
            if _norm_title(course_titles[candidate]) == want:
                out[row.code] = candidate
                break
    return out
