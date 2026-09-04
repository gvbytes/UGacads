"""Generate a static data explorer from the built database.

The output is a single self-contained HTML file: the data is embedded, so it opens
from the filesystem with no server and no build step. It exists so the extraction can
be inspected and disputed, not to be a product surface.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "ui" / "explorer_template.html"


def _rows(conn: sqlite3.Connection, sql: str) -> list[dict]:
    cur = conn.execute(sql)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def chain_spans(courses, edges, availability) -> list[dict]:
    """Earliest semester each prerequisite chain can finish, honouring parity.

    A course offered only in odd semesters cannot follow its prerequisite in the very
    next semester, so a chain of same-parity courses stretches two semesters per link.
    Spans are reported for a chain begun in semester 1 and for one begun in semester 5,
    the latter standing for a student who declares a Minor late.
    """
    parity = {a["course_code"]: a["parity"] for a in availability}
    adj: dict[str, set[str]] = {}
    for e in edges:
        adj.setdefault(e["course_code"], set()).add(e["prereq_code"])

    def paths(node: str, seen: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
        if node in seen or node not in adj:
            return [seen + (node,)]
        out: list[tuple[str, ...]] = []
        for parent in sorted(adj[node]):
            out.extend(paths(parent, seen + (node,)))
        return out

    def place(chain: tuple[str, ...], start: int) -> tuple[int, list[dict]]:
        sem = start - 1
        laid: list[dict] = []
        for code in reversed(chain):
            p = parity.get(code)
            nxt = sem + 1
            if p == "ODD":
                while nxt % 2 == 0:
                    nxt += 1
            elif p == "EVEN":
                while nxt % 2 == 1:
                    nxt += 1
            laid.append({"code": code, "sem": nxt, "parity": p or "UNKNOWN"})
            sem = nxt
        return sem, laid

    out: list[dict] = []
    for target in sorted(adj):
        for chain in paths(target):
            if len(chain) < 2:
                continue
            if any(parity.get(c) is None for c in chain):
                continue
            end1, laid = place(chain, 1)
            end5, _ = place(chain, 5)
            out.append(
                {
                    "target": target,
                    "depth": len(chain) - 1,
                    "from_sem1": end1,
                    "from_sem5": end5,
                    "forces_extension": end5 > 8,
                    "steps": laid,
                }
            )
    out.sort(key=lambda r: (-r["from_sem5"], -r["depth"], r["target"]))
    return out


def collect(db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = None
    try:
        courses = _rows(
            conn,
            "SELECT code, title, department, identity_mode, level, is_ug, ltps, credits,"
            " confidence FROM course ORDER BY department, code",
        )
        availability = _rows(conn, "SELECT course_code, parity, terms FROM course_availability")
        edges = _rows(
            conn,
            "SELECT course_code, prereq_code, alternative_group, resolved, expr_raw,"
            " ambiguous, source_file, source_locator FROM prereq_edge",
        )
        offerings = _rows(
            conn,
            "SELECT course_code, term_id, slot_name, section, course_types, mode,"
            " instructors, timing, venue FROM offering",
        )
        programmes = _rows(conn, "SELECT * FROM programme ORDER BY programme_id")
        specs = _rows(conn, "SELECT * FROM semester_spec ORDER BY programme_id, semester_no")
        slots = _rows(
            conn,
            "SELECT programme_id, semester_no, slot_label, slot_type, course_code,"
            " credits, is_choice, confidence FROM template_slot"
            " ORDER BY programme_id, semester_no",
        )
        minors = _rows(
            conn,
            "SELECT * FROM minor_basket ORDER BY department, stream, ugarc_variant",
        )
        policy = _rows(conn, "SELECT * FROM policy_rule ORDER BY rule_id")
        elig = _rows(conn, "SELECT * FROM programme_eligibility ORDER BY rule_id")
        quarantine = _rows(conn, "SELECT * FROM ingest_quarantine ORDER BY kind")
        terms = _rows(conn, "SELECT * FROM term ORDER BY term_id")
    finally:
        conn.close()

    parity = {a["course_code"]: a["parity"] for a in availability}
    for c in courses:
        c["parity"] = parity.get(c["code"])

    prereq_by_course: dict[str, list[dict]] = {}
    for e in edges:
        prereq_by_course.setdefault(e["course_code"], []).append(e)
    for c in courses:
        c["n_prereq"] = len(prereq_by_course.get(c["code"], []))

    return {
        "terms": terms,
        "courses": courses,
        "offerings": offerings,
        "edges": edges,
        "chains": chain_spans(courses, edges, availability),
        "programmes": programmes,
        "specs": specs,
        "slots": slots,
        "minors": minors,
        "policy": policy,
        "eligibility": elig,
        "quarantine": quarantine,
    }


def render(db_path: str, out_path: str, report_path: str | None = None) -> None:
    data = collect(db_path)
    if report_path and Path(report_path).exists():
        data["report"] = json.loads(Path(report_path).read_text())
    html = TEMPLATE_PATH.read_text()
    payload = json.dumps(data, separators=(",", ":"))
    html = html.replace("/*__APTG_DATA__*/null", payload)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(html)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="aptg-explorer")
    ap.add_argument("--db", default="out/aptg.sqlite")
    ap.add_argument("--out", default="ui/explorer.html")
    ap.add_argument("--report", default="out/reports/ingest_report.json")
    a = ap.parse_args(argv)
    render(a.db, a.out, a.report)
    size = Path(a.out).stat().st_size
    print(f"wrote {a.out} ({size/1_000_000:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
