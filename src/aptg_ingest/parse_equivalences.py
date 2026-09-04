"""Old-to-new course equivalences, and the prerequisite rewrites they imply.

A :mod:`aptg_ingest.codes` alias says two codes name one course. An *equivalence* is
the other case the UGARC restructuring produced: one legacy course replaced by a
combination of new ones. ESC101 became ESC111 plus one of ESC112/ESC113, which no
one-to-one mapping can express.

This matters because the Pingala prerequisite strings flatten the family into a single
alternation. ESO207's prerequisite reads ``( ESC101A OR ESC111M OR ESC112M OR ESC113M )``
— any one of four — when the requirement is ESC111 *and* one of the other two. Read
literally, a single 7-credit module unlocks the whole CSE chain.

The rewrite is stated once, here, and applied uniformly: where an expression names two
or more members of an equivalence, the members are pruned out and the remainder is
ANDed with the equivalence's own expression. Everything else about the edge, including
the original string and the mechanical parse, is left untouched.
"""
from __future__ import annotations

import json

from .model import CourseEquivalence, PolicyRule
from .provenance import OBSERVED


def load_equivalences(path: str) -> tuple[list[CourseEquivalence], list[PolicyRule]]:
    with open(path) as fh:
        data = json.load(fh)

    equivalences = [
        CourseEquivalence(
            equivalence_id=e["equivalence_id"],
            name=e["name"],
            legacy_code=e["legacy_code"],
            member_codes=",".join(e["members"]),
            expression=json.dumps(e["expression"], sort_keys=True, separators=(",", ":")),
            text=e["text"],
            citation=e["citation"],
            confidence=e.get("confidence", OBSERVED),
        )
        for e in data["equivalences"]
    ]
    rules = [
        PolicyRule(
            rule_id=r["rule_id"], scope=r["scope"], name=r["name"],
            value_min=r["value_min"], value_max=r["value_max"], unit=r["unit"],
            text=r["text"], citation=r["citation"],
            confidence=r.get("confidence", OBSERVED),
        )
        for r in data.get("policy_rules", [])
    ]
    return equivalences, rules


def codes_in(node: dict) -> list[str]:
    if not node:
        return []
    if node.get("op") == "COURSE":
        return [node["code"]]
    out: list[str] = []
    for a in node.get("args", ()):
        out.extend(codes_in(a))
    return out


def prune(node: dict, drop: set[str]) -> dict:
    """Remove every reference to a dropped code, collapsing empty branches away."""
    if not node:
        return {}
    if node.get("op") == "COURSE":
        return {} if node["code"] in drop else node
    args = [a for a in (prune(a, drop) for a in node.get("args", ())) if a]
    if not args:
        return {}
    if len(args) == 1:
        return args[0]
    return {"op": node["op"], "args": args}


def rewrite(node: dict, equivalence_expr: dict, members: set[str]) -> dict:
    """Replace an equivalence's members in ``node`` with the equivalence itself."""
    remainder = prune(node, members)
    if not remainder:
        return equivalence_expr
    return {"op": "AND", "args": [remainder, equivalence_expr]}


def applies_to(node: dict, modules: set[str]) -> bool:
    """Two or more *modules* present means the expression is spelling out the family.

    Counting the legacy code towards the total would be wrong. MTH201 reads
    ``( MTH113M OR MTH102A )`` — the new Linear Algebra module, or the old course that
    contained it — which is a genuine either/or and must be left alone. Rewriting it
    with the equivalence would demand Ordinary Differential Equations as well, which
    MTH201 does not need. Only when two modules of a family appear together is the
    string spelling out the whole replacement.
    """
    return len(set(codes_in(node)) & modules) >= 2


def simplify(node: dict) -> dict:
    """Collapse duplicate arms and single-child groups.

    Code resolution can map two spellings onto one course, leaving ``ESO207 OR ESO207``
    where the source wrote ``ESO207 OR ESO207A``. The duplicate is noise in every
    explanation the engine goes on to print.
    """
    if not node or node.get("op") == "COURSE":
        return node
    seen, args = set(), []
    for a in (simplify(a) for a in node.get("args", ())):
        if not a:
            continue
        key = json.dumps(a, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        args.append(a)
    if not args:
        return {}
    if len(args) == 1:
        return args[0]
    return {"op": node["op"], "args": args}
