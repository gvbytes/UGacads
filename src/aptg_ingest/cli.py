"""Command line entry point: build the dataset, validate it, write outputs."""
from __future__ import annotations

import argparse
import csv
import dataclasses
import glob
import json
import sys
from pathlib import Path

from . import db
from .codes import Resolver
from .model import CodeAlias, Dataset
from .parse_equivalences import (
    applies_to,
    codes_in as eq_codes_in,
    load_equivalences,
    rewrite as eq_rewrite,
)
from .parse_master import parse_master, title_aliases, titles_conflict
from .parse_minors import institute_minor_rules, parse_minors
from .parse_programmes import load_curated, parse_bcyber
from .parse_schedule import compute_availability, parse_schedule
from .parse_templates import parse_template
from .provenance import DERIVED, OBSERVED, Quarantine, Source
from .validate import validate

# Term identity. The 2026-27/I schedule published by DOAA
# (Course_Schedule_2026-27-1.pdf) contains exactly the course set of
# odd_sem_recent.xlsx, which fixes that export as the current odd semester. The other
# odd export is an earlier academic year; the even export is contemporary with the
# current year but its own published label is not stated in the sources held here.
SCHEDULES = [
    ("schedule/odd_sem_recent.xlsx", "odd_recent", "ODD", "recent", "2026-27/I", True),
    ("schedule/odd_sem_prior.xlsx", "odd_prior", "ODD", "prior", "earlier year, odd", False),
    ("schedule/even_sem.xlsx", "even", "EVEN", "recent", "current year, even", True),
    # The summer term is not a regular semester (UG Manual 4.3.6). It carries its own
    # much smaller course set, a 27-credit ceiling, and is recorded as its own term so
    # that summer availability is a fact about a course rather than a third parity.
    ("schedule/summer_sem.xlsx", "summer", "SUMMER", "recent", "summer term", True),
]


def build(data_dir: Path, q: Quarantine) -> Dataset:
    ds = Dataset()
    courses: dict[str, object] = {}

    for rel, term_id, parity, era, label, is_current in SCHEDULES:
        path = data_dir / rel
        if not path.exists():
            q.add("input.missing", _src(path), "schedule file not found", rel)
            continue
        term, cs, offs, edges = parse_schedule(
            str(path), term_id, parity, era, label, is_current, q
        )
        ds.terms.append(term)
        for c in cs:
            courses.setdefault(c.code, c)
        ds.offerings.extend(offs)
        ds.prereq_edges.extend(edges)

    ds.courses = list(courses.values())
    ds.availability = compute_availability(ds.terms, ds.offerings)

    for path in sorted(glob.glob(str(data_dir / "templates" / "*.pdf"))):
        try:
            progs, specs, slots = parse_template(path, q)
        except Exception as exc:  # noqa: BLE001 - one bad PDF must not stop the build
            q.add("template.fatal", _src(Path(path)), f"{type(exc).__name__}: {exc}", path)
            continue
        ds.programmes.extend(progs)
        ds.semester_specs.extend(specs)
        ds.template_slots.extend(slots)

    master_pdf = data_dir / "policy" / "approved_course_master.pdf"
    aliases: dict[str, str] = {}
    if master_pdf.exists():
        ds.course_master = parse_master(str(master_pdf), q)
        aliases = title_aliases(
            ds.course_master, {c.code: c.title for c in ds.courses if c.code}
        )
        for old, new in sorted(aliases.items()):
            q.add(
                "master.title_alias",
                _src(master_pdf),
                f"{old} -> {new}: same stem and the same title in the course master",
                next(r.title for r in ds.course_master if r.code == old),
            )
    else:
        q.add("input.missing", _src(master_pdf), "approved course master not found", "")

    # One resolver, shared by everything that names a course, so that a spelling
    # resolves the same way in a template, a prerequisite and a minor basket alike.
    resolver = Resolver(
        {c.code for c in ds.courses if c.code},
        title_aliases=aliases,
        course_titles={c.code: c.title for c in ds.courses if c.code},
        master_titles={r.code: r.title for r in ds.course_master},
        on_veto=lambda code, hit, mt, ct: q.add(
            "alias.title_conflict",
            Source.derived("aptg_ingest.codes.Resolver"),
            f"{code} -> {hit} refused: the course master calls {code} {mt!r} but {hit} "
            f"is {ct!r}; they share no significant word, so they are not one course",
            f"{code}/{hit}",
        ),
    )

    # Every course reference — template slot and prerequisite alike — is pointed at a
    # row of the course table before anything downstream reads it.
    resolve_codes(ds, q, aliases, resolver)
    reconcile_template_credits(ds, q)

    # Minors are loaded after the schedules because their course codes are resolved
    # against the course table: the catalogue prints ESO207A where the schedule prints
    # ESO207, and an unresolved code would leave a basket silently short of a course.
    minors = data_dir / "curated" / "minor_baskets.json"
    if minors.exists():
        ds.minor_baskets = parse_minors(
            str(minors), {c.code: c.credits for c in ds.courses if c.code}, q, resolver
        )
    else:
        q.add("input.missing", _src(minors), "curated minor catalogue not found", str(minors))

    bcyber = data_dir / "programmes" / "bcyber.json"
    if bcyber.exists():
        prog, specs, slots, title_courses = parse_bcyber(str(bcyber), q)
        ds.programmes.append(prog)
        ds.semester_specs.extend(specs)
        ds.template_slots.extend(slots)
        ds.courses.extend(title_courses)

    pol = data_dir / "curated" / "policy_rules.json"
    elig = data_dir / "curated" / "programme_eligibility.json"
    if pol.exists() and elig.exists():
        ds.policy_rules, ds.eligibility = load_curated(str(pol), str(elig))
    if minors.exists():
        # The DOAA-level minor rules are stated in the minor catalogue's own preamble,
        # so they are emitted alongside the hand-transcribed UG Manual rules.
        ds.policy_rules.extend(institute_minor_rules(str(minors)))

    equiv = data_dir / "curated" / "course_equivalences.json"
    if equiv.exists():
        ds.course_equivalences, eq_rules = load_equivalences(str(equiv))
        ds.policy_rules.extend(eq_rules)
        apply_equivalences(ds, q, resolver)

    return ds


def apply_equivalences(ds: Dataset, q: Quarantine, resolver) -> None:
    """Correct prerequisite readings that a curated equivalence supersedes.

    The Pingala strings flatten a modularised course family into one alternation, so
    ESO207 reads as "any one of ESC101A, ESC111M, ESC112M, ESC113M" when the
    requirement is ESC111 *and* one of the other two. Nine courses in the CSE chain
    carry the same pattern. The rule is stated in
    :mod:`aptg_ingest.parse_equivalences`; every rewrite is logged with the expression
    before and after, and expr_raw and expr_ast keep the original reading.
    """
    src = Source.derived("aptg_ingest.cli.apply_equivalences")
    known = {c.code for c in ds.courses if c.code}

    prepared = []
    for e in ds.course_equivalences:
        members, missing = resolver.resolve_all(e.member_codes.split(","))
        expr = json.loads(e.expression)
        # The equivalence's own expression is resolved too, and any arm naming a course
        # no term offers is pruned: requiring a course that cannot be taken would make
        # the whole chain unschedulable.
        mapping = {c: resolver.resolve(c) for c in eq_codes_in(expr)}
        expr = _remap_ast(expr, {k: v for k, v in mapping.items() if v})
        # Arms naming a course no loaded term offers are deliberately kept. Being
        # offered and having been completed are different things: a Y21 student holds
        # ESC101A, which no current term runs, and must still be credited for it. The
        # engine reports what is *missing* using only routes a student can actually
        # take, which is where schedulability belongs.
        if missing:
            q.add("equivalence.member_missing", src,
                  f"{e.equivalence_id}: {', '.join(missing)} not offered in any loaded "
                  "term; the rewrite still applies to the members that are",
                  e.member_codes)
        prepared.append((e, set(members) | {m for m in e.member_codes.split(",")}, expr))

    for edge in ds.prereq_edges:
        node = json.loads(edge.expr_normalized)
        for e, members, expr in prepared:
            if not applies_to(node, members):
                continue
            node = dedupe_ast(eq_rewrite(node, expr, members))
            edge.expr_normalized = json.dumps(node, sort_keys=True, separators=(",", ":"))
            edge.equivalence_id = e.equivalence_id
            edge.confidence = DERIVED
            q.add("prereq.equivalence_applied", src.at(edge.course_code),
                  f"{edge.course_code}: {e.equivalence_id} rewrote the reading of "
                  f"{e.name}", edge.expr_raw)

    for edge in ds.prereq_edges:
        node = dedupe_ast(json.loads(edge.expr_normalized))
        edge.expr_normalized = json.dumps(node, sort_keys=True, separators=(",", ":"))

    # An edge whose prereq_code the rewrite removed no longer describes a dependency.
    kept = []
    for edge in ds.prereq_edges:
        if edge.equivalence_id and edge.prereq_code not in eq_codes_in(
            json.loads(edge.expr_normalized)
        ):
            continue
        kept.append(edge)
    ds.prereq_edges = kept


def dedupe_ast(node: dict) -> dict:
    """Collapse arms that became identical once codes were aliased.

    ESO207 and ESO207A resolve to the same course, so an expression that offered them
    as alternatives reads "ESO207 OR ESO207" after aliasing. Harmless to evaluate, but
    it makes every explanation the engine prints look like a mistake.
    """
    if not node or node.get("op") == "COURSE":
        return node
    seen, args = set(), []
    for a in (dedupe_ast(a) for a in node.get("args", ())):
        key = json.dumps(a, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        args.append(a)
    if len(args) == 1:
        return args[0]
    return {"op": node["op"], "args": args}


def _src(path: Path):
    from .provenance import Source

    return Source(str(path), "-")


def _remap_ast(node: dict, mapping: dict[str, str]) -> dict:
    """Rewrite the course codes inside a parsed prerequisite expression."""
    if not node:
        return node
    if node.get("op") == "COURSE":
        return {"op": "COURSE", "code": mapping.get(node["code"], node["code"])}
    return {
        "op": node["op"],
        "args": [_remap_ast(a, mapping) for a in node.get("args", ())],
    }


def _prune_self(node: dict, code: str) -> dict:
    """Drop references to ``code`` from its own prerequisite expression.

    Aliasing can make a course its own prerequisite. CE351's prerequisite reads
    ``( CE252 OR CE351A )`` — the department's way of saying "CE252, or the old CE351A
    you may already hold" — and once CE351A is recognised as CE351 the expression names
    the course itself. A self-edge is not a real dependency and would make the graph
    cyclic, so the arm is removed and the rest of the expression kept.
    """
    if not node:
        return node
    if node.get("op") == "COURSE":
        return {} if node["code"] == code else node
    args = [a for a in (_prune_self(a, code) for a in node.get("args", ())) if a]
    if not args:
        return {}
    if len(args) == 1:
        return args[0]
    return {"op": node["op"], "args": args}


def resolve_codes(ds: Dataset, q: Quarantine, aliases: dict[str, str] | None = None,
                  resolver: Resolver | None = None) -> None:
    """Point every course reference in the dataset at a row of the course table.

    The sources spell the same course three ways. The schedule exports print the code
    the registrar uses that term (``ESC111M``, ``MTH111M``); the department templates
    and the Courses of Study print the unsuffixed form (``ESC111``, ``MTH111``); the
    minor catalogue prints the UGARC form (``ESO207A``). Until these are reconciled a
    template slot naming ``MTH111`` matches no course, so the engine reports the
    Institute Core of every first-year semester as "not offered in any loaded term" and
    refuses to schedule it.

    :mod:`aptg_ingest.codes` states the matching rule. Every rewrite is recorded in the
    quarantine log as ``*.code_aliased`` so the mapping can be audited, and a reference
    that still resolves to nothing is left as it is and quarantined rather than dropped:
    a template requirement naming a course no term offers is a real finding about the
    data, not something to paper over.
    """
    known = {c.code for c in ds.courses if c.code}
    src = Source.derived("aptg_ingest.cli.resolve_codes")

    def _veto(code, hit, master_title, course_title):
        q.add(
            "alias.title_conflict",
            src,
            f"{code} -> {hit} refused: the course master calls {code} "
            f"{master_title!r} but {hit} is {course_title!r}; they share no "
            "significant word, so they are not one course",
            f"{code}/{hit}",
        )

    resolver = resolver or Resolver(
        known,
        title_aliases=aliases,
        course_titles={c.code: c.title for c in ds.courses if c.code},
        master_titles={r.code: r.title for r in ds.course_master},
        on_veto=_veto,
    )
    lookup = resolver.resolve
    used = resolver.used

    for slot in ds.template_slots:
        if not slot.course_code or slot.course_code in known:
            continue
        hit = lookup(slot.course_code)
        if hit:
            q.add(
                "template.code_aliased",
                src.at(f"{slot.programme_id} sem {slot.semester_no}"),
                f"{slot.course_code} -> {hit}",
                slot.slot_label,
            )
            slot.course_code = hit
        else:
            q.add(
                "template.unresolved_code",
                src.at(f"{slot.programme_id} sem {slot.semester_no}"),
                f"{slot.course_code} is named by the template but no loaded term offers it",
                slot.slot_label,
            )

    # Prerequisite expressions are rewritten as a whole: the engine evaluates
    # expr_normalized, so remapping only the flat prereq_code column would leave the
    # expression still pointing at codes that resolve to nothing.
    for e in ds.prereq_edges:
        mapping = {}
        for raw in set(_ast_codes_json(e.expr_normalized)) | set(_ast_codes_json(e.expr_ast)):
            if raw in known:
                continue
            hit = lookup(raw)
            if hit:
                mapping[raw] = hit
        if not mapping:
            continue
        e.expr_ast = json.dumps(
            _remap_ast(json.loads(e.expr_ast), mapping), sort_keys=True, separators=(",", ":")
        )
        e.expr_normalized = json.dumps(
            _remap_ast(json.loads(e.expr_normalized), mapping),
            sort_keys=True, separators=(",", ":"),
        )
        if e.prereq_code in mapping:
            q.add(
                "prereq.code_aliased",
                src.at(e.course_code),
                f"{e.prereq_code} -> {mapping[e.prereq_code]}",
                e.expr_raw,
            )
            e.prereq_code = mapping[e.prereq_code]

    # Aliasing can make a course its own prerequisite, where a department writes
    # "the new course, or the old code for it" into one expression.
    kept = []
    for e in ds.prereq_edges:
        codes = _ast_codes_json(e.expr_normalized)
        if e.course_code in codes:
            e.expr_ast = json.dumps(
                _prune_self(json.loads(e.expr_ast), e.course_code),
                sort_keys=True, separators=(",", ":"),
            )
            e.expr_normalized = json.dumps(
                _prune_self(json.loads(e.expr_normalized), e.course_code),
                sort_keys=True, separators=(",", ":"),
            )
            q.add(
                "prereq.self_reference",
                src.at(e.course_code),
                f"{e.course_code} lists itself as a prerequisite once its own former "
                "code is recognised; the self-reference is dropped and the rest kept",
                e.expr_raw,
            )
        if e.prereq_code == e.course_code:
            continue
        kept.append(e)
    ds.prereq_edges = kept

    for e in ds.prereq_edges:
        e.resolved = e.prereq_code in known

    # Every alias the build relied on, kept so the engine and the interface resolve a
    # course code exactly as the build did — including one a student types in from
    # memory. The whole master mapping is recorded, not only the codes that happened to
    # appear in a template or a basket.
    for frm, to in aliases.items():
        used.setdefault(frm, (to, "TITLE_MATCH"))
    ds.code_aliases = [
        CodeAlias(from_code=frm, to_code=to, method=method,
                  confidence=OBSERVED if method == "TITLE_MATCH" else DERIVED)
        for frm, (to, method) in sorted(used.items())
        if to in known
    ]


def reconcile_template_credits(ds: Dataset, q: Quarantine) -> None:
    """Settle each semester's slot credits against the total printed on the template.

    Grid extraction yields slots whose credit figure did not survive — a bare ``SCHEME``
    cell, an ``ELC111/ELC112`` choice — and the engine was defaulting those to 9. On the
    ME template that inflated semester 1 from the printed 55 to 73 and semesters 7 and 8
    from 51 and 45 to 60 and 54, which pushed nineteen elective placements into
    semesters 7 to 10 and turned an eight-semester degree into a ten-semester one.

    The printed per-semester total is read directly off the template and cross-checked,
    so it is the authority. Where slots carry no credit figure, the residual between the
    printed total and the slots that do carry one is shared among them; where the slots
    that carry a figure already account for the whole printed total, the remainder are
    extraction artefacts and are dropped. Both outcomes are quarantined.
    """
    src = Source.derived("aptg_ingest.cli.reconcile_template_credits")
    printed = {
        (s.programme_id, s.semester_no): s
        for s in ds.semester_specs
        if s.confidence == "OBSERVED" and s.credits_min
    }

    by_sem: dict[tuple[str, int], list] = {}
    for slot in ds.template_slots:
        by_sem.setdefault((slot.programme_id, slot.semester_no), []).append(slot)

    drop: set[int] = set()
    for key, slots in sorted(by_sem.items()):
        spec = printed.get(key)
        if spec is None:
            continue
        # UNKNOWN slots are already excluded from scheduling, so they neither consume
        # nor claim credits here.
        real = [s for s in slots if s.slot_type != "UNKNOWN"]
        known = sum(s.credits for s in real if s.credits)
        blank = sorted(
            (s for s in real if not s.credits), key=lambda s: (s.slot_type, s.slot_label)
        )
        residual = spec.credits_min - known
        loc = src.at(f"{key[0]} sem {key[1]}")

        if blank and residual > 0:
            share, extra = divmod(residual, len(blank))
            for i, s in enumerate(blank):
                s.credits = share + (1 if i < extra else 0)
            q.add(
                "template.credits_apportioned", loc,
                f"{residual} credits of the printed {spec.credits_min} were not carried "
                f"by any extracted cell; shared across {len(blank)} slot(s) with no "
                "figure of their own",
                ", ".join(s.slot_label for s in blank),
            )
        elif blank:
            for s in blank:
                drop.add(id(s))
            q.add(
                "template.phantom_slot", loc,
                f"the slots carrying a credit figure already total the printed "
                f"{spec.credits_min}; {len(blank)} further slot(s) with no figure are "
                "extraction artefacts and are dropped",
                ", ".join(s.slot_label for s in blank),
            )
        elif residual > 0:
            q.add(
                "template.credits_unaccounted", loc,
                f"{residual} of the printed {spec.credits_min} credits are not carried "
                "by any extracted slot; the semester will be planned light",
                ", ".join(s.slot_label for s in slots if s.slot_type == "UNKNOWN"),
            )
        elif residual < 0:
            q.add(
                "template.credits_over_extracted", loc,
                f"extracted slots total {known} against a printed {spec.credits_min}; "
                "the grid has yielded more than the template states",
                ", ".join(f"{s.slot_label}={s.credits}" for s in real),
            )

    if drop:
        ds.template_slots = [s for s in ds.template_slots if id(s) not in drop]


def _ast_codes_json(blob: str) -> list[str]:
    def walk(n):
        if not n:
            return []
        if n.get("op") == "COURSE":
            return [n["code"]]
        out = []
        for a in n.get("args", ()):
            out.extend(walk(a))
        return out

    try:
        return walk(json.loads(blob))
    except Exception:  # noqa: BLE001 - a malformed expression is caught by the validator
        return []


def export_json(ds: Dataset, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for field in dataclasses.fields(ds):
        items = getattr(ds, field.name)
        rows = [dataclasses.asdict(i) for i in items]
        rows.sort(key=lambda r: tuple(str(v) for v in r.values()))
        (out_dir / f"{field.name}.json").write_text(
            json.dumps(rows, indent=1, sort_keys=True) + "\n"
        )


def write_quarantine(q: Quarantine, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    by_kind: dict[str, list[dict]] = {}
    for item in q.items:
        by_kind.setdefault(item["kind"], []).append(item)
    for kind, items in sorted(by_kind.items()):
        path = out_dir / f"{kind.replace('.', '_')}.csv"
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(items[0].keys()))
            w.writeheader()
            w.writerows(sorted(items, key=lambda r: tuple(str(v) for v in r.values())))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="aptg-ingest", description=__doc__)
    ap.add_argument("--data", default="data", help="raw data directory")
    ap.add_argument("--out", default="out", help="output directory")
    ap.add_argument(
        "--strict", action="store_true", help="exit non-zero if any invariant is violated"
    )
    args = ap.parse_args(argv)

    data_dir = Path(args.data)
    out_dir = Path(args.out)

    q = Quarantine()
    ds = build(data_dir, q)
    report = validate(ds)

    db.write(ds, q.items, str(out_dir / "aptg.sqlite"))
    export_json(ds, out_dir / "export")
    write_quarantine(q, out_dir / "quarantine")

    summary = {
        "stats": report.stats,
        "violations": report.violations,
        "warnings": report.warnings,
        "quarantine": q.kinds(),
    }
    (out_dir / "reports").mkdir(parents=True, exist_ok=True)
    (out_dir / "reports" / "ingest_report.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )

    print("APTG ingestion")
    print("-" * 62)
    for k, v in report.stats.items():
        print(f"  {k:28s} {v}")
    print(f"\n  quarantined                  {q.count()}")
    for kind, n in q.kinds().items():
        print(f"    {kind:26s} {n}")
    if report.warnings:
        print("\n  warnings")
        for w in report.warnings:
            print(f"    - {w}")
    if report.violations:
        print("\n  VIOLATIONS")
        for v in report.violations:
            print(f"    - {v}")
    print(f"\n  wrote {out_dir/'aptg.sqlite'}")
    return 1 if (args.strict and report.violations) else 0


if __name__ == "__main__":
    sys.exit(main())
