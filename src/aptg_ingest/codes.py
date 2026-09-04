"""Course-code aliasing between sources that spell the same course differently.

The three Pingala schedule exports print course codes without the UGARC suffix
(``ESO207``, ``CS345``, ``MSE604``). The Courses of Study, the department templates and
the institute minor list print the suffixed form (``ESO207A``, ``CS345A``, ``MSE604A``).
They are the same courses. Nothing in the sources maps one spelling to the other, so
until this module existed every minor course code failed to join to the ``course``
table: the engine could not read a minor course's credits, its semester parity or its
prerequisites, and quietly skipped it.

The rule, stated in full: **two codes denote the same course when they share a stem and
at least one of them carries no suffix.** The unsuffixed form is the course without a
variant claim, so pairing it with a marked variant is safe; pairing two *different*
marked variants is not. ``ESO207A`` and ``ESO207`` are one course; ``AE201A`` (Aerospace
Dynamics, Old UGARC) and ``AE201M`` (Introduction to Aerospace Engineering, New UGARC)
are two, and collapsing them would erase the very batch distinction the data model
exists to preserve. So:

1. exact match;
2. the code with its trailing letter removed, where that bare code is known
   (``ESO207A`` -> ``ESO207``);
3. for a code that is *already* bare, the single known variant sharing its stem
   (``CGS702`` -> ``CGS702A``); an ambiguous stem is left unresolved.

A suffixed code never resolves to a differently-suffixed one. Anything that survives all
three is returned unresolved for the caller to quarantine.
"""
from __future__ import annotations

import re

CODE_RE = re.compile(r"^([A-Z]{2,4}\d{3})([A-Z])?$")


def stem(code: str) -> str:
    """``ESO207A`` -> ``ESO207``; a code with no trailing letter is returned unchanged."""
    m = CODE_RE.match(code)
    return m.group(1) if m else code


def build_index(known: set[str]) -> dict[str, list[str]]:
    """Group the known course codes by stem, so suffix variants can be found."""
    index: dict[str, list[str]] = {}
    for code in sorted(known):
        index.setdefault(stem(code), []).append(code)
    return index


def resolve(code: str, known: set[str], index: dict[str, list[str]] | None = None) -> str | None:
    """The known course code denoting ``code``, or None if there is no unambiguous one."""
    if code in known:
        return code
    base = stem(code)
    if base in known:
        return base
    if base != code:
        # A suffixed code must not be matched to a differently-suffixed one: the suffix
        # is what separates the Old and New UGARC versions of a course.
        return None
    index = index if index is not None else build_index(known)
    variants = index.get(base, [])
    return variants[0] if len(variants) == 1 else None


class Resolver:
    """The one place a foreign course code is turned into a course-table code.

    Every source that names a course — the department templates, the prerequisite
    expressions, the minor catalogue — goes through this, so that a spelling resolves
    the same way wherever it appears. Three rules, in order of the strength of their
    evidence:

    1. the course master gives two codes sharing a stem the same title, so they are one
       course renumbered (``AE201A`` = ``AE201M``);
    2. the structural stem rule (``ESO207A`` = ``ESO207``) — but **vetoed** when the
       master's title for the suffixed code shares no significant word with the course
       the rule would map to. ``EE210A`` is Microelectronics-I and ``EE210`` is Analog
       Electronics: two courses, one stem. Without the veto the EE minor silently
       required the wrong course;
    3. otherwise unresolved, for the caller to quarantine.
    """

    def __init__(self, known, title_aliases=None, course_titles=None, master_titles=None,
                 on_veto=None, senate_aliases=None, department_rules=None):
        self.known = set(known)
        self.index = build_index(self.known)
        self.senate_aliases = dict(senate_aliases or {})
        self.department_rules = list(department_rules or [])
        self.title_aliases = dict(title_aliases or {})
        self.course_titles = dict(course_titles or {})
        self.master_titles = dict(master_titles or {})
        self._on_veto = on_veto
        self.used: dict[str, tuple[str, str]] = {}

    def resolve(self, code: str) -> str | None:
        if code in self.known:
            return code
        # A Senate-approved mapping outranks anything inferred. It is the only source
        # that can state a renumbering the codes themselves do not reveal, such as
        # PHY102A becoming PHY112, and the only one that can correct an inference that
        # would otherwise be plausible and wrong, such as MSE497A -> MSE497.
        hit = self.senate_aliases.get(code)
        method = "SENATE"
        if hit is not None and hit not in self.known:
            hit = None
        if hit is None:
            hit, method = self._department_rule(code), "SENATE_DEPT"
        if hit is None:
            hit = self.title_aliases.get(code)
            method = "TITLE_MATCH"
        if hit is None:
            hit, method = resolve(code, self.known, self.index), "STEM"
            if hit is not None and self._conflicts(code, hit):
                if self._on_veto:
                    self._on_veto(code, hit,
                                  self.master_titles.get(code, ""),
                                  self.course_titles.get(hit, ""))
                return None
        if hit and hit != code:
            self.used[code] = (hit, method)
        return hit

    def _department_rule(self, code: str) -> str | None:
        """Apply a department-wide renumbering, e.g. IMExyzA -> DMSxyz.

        Only used when the resulting code actually exists, so a rule stated in general
        terms never invents a course.
        """
        for rule in self.department_rules:
            pre, to, suf = rule["from_prefix"], rule["to_prefix"], rule.get("requires_suffix", "")
            if not code.startswith(pre):
                continue
            rest = code[len(pre):]
            if suf and not rest.endswith(suf):
                continue
            if suf:
                rest = rest[: -len(suf)]
            candidate = to + rest
            if candidate in self.known:
                return candidate
        return None

    def _conflicts(self, code: str, hit: str) -> bool:
        from .parse_master import titles_conflict

        return titles_conflict(self.master_titles.get(code, ""), self.course_titles.get(hit, ""))

    def resolve_all(self, codes) -> tuple[list[str], list[str]]:
        out: list[str] = []
        missing: list[str] = []
        for c in codes:
            hit = self.resolve(c)
            if hit is None:
                missing.append(c)
            elif hit not in out:
                out.append(hit)
        return out, missing


def resolve_all(
    codes: list[str], known: set[str], index: dict[str, list[str]] | None = None
) -> tuple[list[str], list[str]]:
    """Resolve a list of codes, preserving order and dropping duplicates.

    Returns ``(resolved, unresolved)``. The caller is expected to quarantine the second
    list rather than discard it.
    """
    index = index if index is not None else build_index(known)
    out: list[str] = []
    missing: list[str] = []
    for c in codes:
        hit = resolve(c, known, index)
        if hit is None:
            missing.append(c)
        elif hit not in out:
            out.append(hit)
    return out, missing
