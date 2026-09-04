"""Parser for Pingala prerequisite expressions.

Pingala stores prerequisites as small boolean strings, e.g.::

    ( ESO201 OR ESO201A AND ESO204A OR ESO204  )
    ( AE201M  ) AND (AE209  )
    ( ESO202 OR ESO202A  )

The first form is genuinely ambiguous. Read with ordinary boolean precedence
(AND binds tighter than OR) it means::

    ESO201 OR (ESO201A AND ESO204A) OR ESO204

but the intended reading is almost certainly::

    (ESO201 OR ESO201A) AND (ESO204A OR ESO204)

because ESO201/ESO201A and ESO204/ESO204A are old/new code pairs for the same two
courses. Rather than silently choose, this module produces *both* readings:

* ``ast``        - the mechanical parse, no guessing
* ``normalized`` - the variant-grouped reading
* ``ambiguous``  - True when the two disagree, so a human can sign the case off

The variant-grouping rule is purely structural and stated here in full: within a run
of codes joined by AND/OR at one parenthesis depth, codes sharing a *stem* (the code
with a single trailing letter removed, e.g. ESO201A -> ESO201) are treated as
alternatives to one another, and the resulting stem-groups are combined with AND.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

CODE_RE = re.compile(r"[A-Z]{2,4}\s?\d{3}[A-Z]?")
TOKEN_RE = re.compile(r"\(|\)|\bAND\b|\bOR\b|[A-Z]{2,4}\s?\d{3}[A-Z]?")


class PrereqParseError(ValueError):
    pass


def normalize_code(raw: str) -> str:
    return raw.replace(" ", "").upper()


def stem(code: str) -> str:
    """ESO201A -> ESO201, AE201M -> AE201, ESO201 -> ESO201."""
    m = re.fullmatch(r"([A-Z]{2,4}\d{3})([A-Z])?", code)
    return m.group(1) if m else code


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for m in TOKEN_RE.finditer(text.upper()):
        t = m.group()
        out.append(t if t in ("(", ")", "AND", "OR") else normalize_code(t))
    return out


def repair(tokens: list[str]) -> tuple[list[str], list[str]]:
    """Fix two data-entry artefacts seen in the Pingala export.

    1. A dangling operator immediately after "(" or at the start, e.g.
       ``(OR MTH301 MTH408)``. The leading operator is dropped.
    2. Two codes juxtaposed with no operator between them, e.g.
       ``CGS602  MTH211``. Pingala renders a conjunction this way, so AND is inserted.

    Every repair is recorded and surfaced in the ingest report rather than applied
    silently.
    """
    out: list[str] = []
    notes: list[str] = []
    for t in tokens:
        if t in ("AND", "OR") and (not out or out[-1] == "("):
            notes.append(f"dropped dangling {t}")
            continue
        is_code = t not in ("(", ")", "AND", "OR")
        if is_code and out and out[-1] not in ("(", "AND", "OR"):
            out.append("AND")
            notes.append(f"inserted implicit AND before {t}")
        if t == "(" and out and out[-1] == ")":
            out.append("AND")
            notes.append("inserted implicit AND between groups")
        out.append(t)
    # drop a trailing operator, if any
    while out and out[-1] in ("AND", "OR"):
        notes.append(f"dropped trailing {out[-1]}")
        out.pop()
    return out, notes


# --- mechanical parse -------------------------------------------------------


@dataclass
class Parser:
    tokens: list[str]
    pos: int = 0

    def peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> str:
        t = self.peek()
        if t is None:
            raise PrereqParseError("unexpected end of expression")
        self.pos += 1
        return t

    def parse(self) -> dict:
        node = self.parse_or()
        if self.peek() is not None:
            raise PrereqParseError(f"trailing tokens at {self.pos}: {self.tokens[self.pos:]}")
        return node

    def parse_or(self) -> dict:
        arms = [self.parse_and()]
        while self.peek() == "OR":
            self.take()
            arms.append(self.parse_and())
        return arms[0] if len(arms) == 1 else {"op": "OR", "args": arms}

    def parse_and(self) -> dict:
        arms = [self.parse_atom()]
        while self.peek() == "AND":
            self.take()
            arms.append(self.parse_atom())
        return arms[0] if len(arms) == 1 else {"op": "AND", "args": arms}

    def parse_atom(self) -> dict:
        t = self.take()
        if t == "(":
            node = self.parse_or()
            if self.peek() != ")":
                raise PrereqParseError("unbalanced parenthesis")
            self.take()
            return node
        if t in ("AND", "OR", ")"):
            raise PrereqParseError(f"unexpected token {t!r}")
        return {"op": "COURSE", "code": t}


# --- variant-grouped reading ------------------------------------------------


def _flatten_codes(tokens: list[str]) -> list[str] | None:
    """Return the code sequence if tokens are a flat run of codes and operators."""
    codes = [t for t in tokens if t not in ("(", ")", "AND", "OR")]
    if not codes:
        return None
    # only flat if no nested grouping changes the shape
    depth = 0
    for t in tokens:
        if t == "(":
            depth += 1
        elif t == ")":
            depth -= 1
    return codes if depth == 0 else None


def variant_grouped(tokens: list[str]) -> dict | None:
    """Group codes by stem into OR-arms, then AND the groups together.

    Only meaningful when the expression mixes AND and OR at one depth; returns None
    when the expression has no such ambiguity to resolve.
    """
    has_and = "AND" in tokens
    has_or = "OR" in tokens
    if not (has_and and has_or):
        return None
    codes = _flatten_codes(tokens)
    if not codes:
        return None

    groups: list[list[str]] = []
    for c in codes:
        if groups and stem(groups[-1][-1]) == stem(c):
            groups[-1].append(c)
        else:
            groups.append([c])

    def as_node(group: list[str]) -> dict:
        if len(group) == 1:
            return {"op": "COURSE", "code": group[0]}
        return {"op": "OR", "args": [{"op": "COURSE", "code": c} for c in group]}

    if len(groups) == 1:
        return as_node(groups[0])
    return {"op": "AND", "args": [as_node(g) for g in groups]}


# --- public API -------------------------------------------------------------


def codes_in(node: dict) -> list[str]:
    if node["op"] == "COURSE":
        return [node["code"]]
    out: list[str] = []
    for a in node["args"]:
        out.extend(codes_in(a))
    return out


def alternative_groups(node: dict) -> dict[str, int]:
    """Map each code to an OR-arm index; codes in the same OR are interchangeable."""
    out: dict[str, int] = {}
    counter = [0]

    def walk(n: dict, group: int | None) -> None:
        if n["op"] == "COURSE":
            out[n["code"]] = group if group is not None else -1
            return
        if n["op"] == "OR":
            counter[0] += 1
            g = counter[0]
            for a in n["args"]:
                walk(a, g)
        else:
            for a in n["args"]:
                walk(a, None)

    walk(node, None)
    return out


@dataclass
class ParsedPrereq:
    raw: str
    ast: dict
    normalized: dict
    ambiguous: bool
    codes: list[str]
    error: str | None = None
    repairs: tuple[str, ...] = ()

    def ast_json(self) -> str:
        return json.dumps(self.ast, sort_keys=True, separators=(",", ":"))

    def normalized_json(self) -> str:
        return json.dumps(self.normalized, sort_keys=True, separators=(",", ":"))


def parse(text: str) -> ParsedPrereq:
    tokens = tokenize(text)
    if not tokens:
        return ParsedPrereq(text, {}, {}, False, [], error="no tokens")
    tokens, notes = repair(tokens)
    try:
        ast = Parser(tokens).parse()
    except PrereqParseError as exc:
        return ParsedPrereq(text, {}, {}, False, [], error=str(exc), repairs=tuple(notes))

    grouped = variant_grouped(tokens)
    normalized = grouped if grouped is not None else ast
    ambiguous = grouped is not None and json.dumps(grouped, sort_keys=True) != json.dumps(
        ast, sort_keys=True
    )
    return ParsedPrereq(
        text, ast, normalized, ambiguous, sorted(set(codes_in(ast))), repairs=tuple(notes)
    )
