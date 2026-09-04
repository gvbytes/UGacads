"""Assemble the static site published to GitHub Pages.

GitHub Pages serves files, not processes, so the scheduler cannot run on a server there.
Rather than ship a cut-down reimplementation in JavaScript — which would leave two
engines to keep in step and break the problem statement's requirement that the result be
reproducible — the site carries a Python runtime and runs *the same engine source* in the
browser under Pyodide.

What the site needs:

* the engine package, copied verbatim;
* a database trimmed to what the engine actually reads. The ingestion tables (the course
  master, the code aliases, the quarantine) exist so the build can be audited and are not
  consulted at solve time, so they are left out of the web copy;
* the pages themselves.
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

# Tables the engine reads in aptg_engine.data.load(); the list is verified against that
# module by a test, so trimming can never silently remove something the solver needs.
# Everything else in the database exists to audit the build — the ingestion quarantine
# and the course equivalences — and is dropped from the web copy.
ENGINE_TABLES = [
    "term",
    "course",
    "course_master",
    "code_alias",
    "course_availability",
    "offering",
    "prereq_edge",
    "programme",
    "semester_spec",
    "template_slot",
    "minor_basket",
    "policy_rule",
    "programme_eligibility",
]

# Offering columns the engine uses. The rest — instructors, timings, venues — are large
# free text that only the explorer displays.
OFFERING_COLUMNS = ["course_code", "term_id", "course_types", "mode"]


def trim_database(src: str, dst: str) -> tuple[int, int]:
    """Copy only the tables and columns the engine consults. Returns (bytes in, out)."""
    out = Path(dst)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()

    source = sqlite3.connect(src)
    target = sqlite3.connect(out)
    try:
        schema = {
            name: sql
            for name, sql in source.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL"
            )
        }
        for table in ENGINE_TABLES:
            if table not in schema:
                continue
            if table == "offering":
                cols = ", ".join(f"{c} TEXT" for c in OFFERING_COLUMNS)
                target.execute(f"CREATE TABLE offering ({cols})")
                rows = source.execute(
                    f"SELECT {', '.join(OFFERING_COLUMNS)} FROM offering"
                ).fetchall()
                target.executemany(
                    f"INSERT INTO offering VALUES ({','.join('?' * len(OFFERING_COLUMNS))})",
                    rows,
                )
                continue
            target.execute(schema[table])
            cols = [r[1] for r in source.execute(f"PRAGMA table_info({table})")]
            rows = source.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
            target.executemany(
                f"INSERT INTO {table} VALUES ({','.join('?' * len(cols))})", rows
            )
        # Indexes the engine's joins rely on; the rest are dead weight in the browser.
        target.execute("CREATE INDEX IF NOT EXISTS offering_course_ix ON offering(course_code)")
        target.execute("CREATE INDEX IF NOT EXISTS prereq_course_ix ON prereq_edge(course_code)")
        target.execute("CREATE INDEX IF NOT EXISTS slot_prog_ix ON template_slot(programme_id)")
        target.commit()
        target.execute("VACUUM")
        target.commit()
    finally:
        source.close()
        target.close()
    return Path(src).stat().st_size, out.stat().st_size


def copy_engine(root: Path, site: Path) -> list[str]:
    """Copy the engine package into the site, minus the server, which has no use here."""
    dest = site / "engine" / "aptg_engine"
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in ("__init__.py", "data.py", "state.py", "engine.py", "api.py"):
        shutil.copy2(root / "src" / "aptg_engine" / name, dest / name)
        copied.append(name)
    return copied


BOOT_STYLE = """
<style id="boot-style">
#boot{position:fixed;inset:0;background:var(--paper);z-index:99;display:flex;
  align-items:center;justify-content:center}
.boot-inner{max-width:38ch;text-align:center}
.boot-inner b{font:500 15px/1.4 var(--serif);display:block;margin-bottom:6px}
#boot-step{color:var(--muted);font-size:13px;margin:0 0 14px;min-height:2.6em}
.boot-bar{height:2px;background:var(--rule);overflow:hidden}
.boot-bar i{display:block;height:100%;width:0;background:var(--accent);
  transition:width .4s ease}
.boot-note{color:var(--faint);font-size:11.5px;margin:14px 0 0;line-height:1.5}
</style>
"""


def build_page(app_html: Path, site: Path) -> int:
    """Turn the server-backed page into the browser-backed one.

    The page is unchanged apart from two things: the bootstrap that stands up the Python
    runtime and intercepts /api/*, and deferring the page's own start until that runtime
    is ready. Everything else — markup, styling, behaviour — is the file the local server
    serves, so the two cannot drift apart.
    """
    html = app_html.read_text()
    html = html.replace("</head>", BOOT_STYLE + "</head>", 1)

    # On the published site the two pages sit side by side, so each links to the other.
    # The link is added here rather than in ui/app.html because the local server has no
    # explorer route to point at.
    html = html.replace(
        '<span class="right" id="cat-note">loading catalogue…</span>',
        '<span class="right" id="cat-note">loading catalogue…</span>'
        '<a class="right" href="explorer.html" style="margin-left:16px">Data explorer</a>',
        1,
    )
    html = html.replace(
        "<script>",
        '<script src="bootstrap.js"></script>\n<script>',
        1,
    )
    if "\nboot();" not in html:
        raise SystemExit("app.html no longer ends by calling boot(); update webbuild")
    html = html.replace(
        "\nboot();",
        "\nwindow.__APTG_READY__.then(boot).catch(() => {});",
        1,
    )
    out = site / "index.html"
    out.write_text(html)
    return len(html)


def build(root: Path, site: Path, db: Path) -> dict:
    site.mkdir(parents=True, exist_ok=True)
    before, after = trim_database(str(db), str(site / "aptg.sqlite"))
    modules = copy_engine(root, site)
    shutil.copy2(root / "ui" / "bootstrap.js", site / "bootstrap.js")
    size = build_page(root / "ui" / "app.html", site)

    # The explorer ships alongside the planner: it is the surface on which the
    # extraction itself can be checked, and it needs no runtime at all.
    from .explorer import render

    render(str(db), str(site / "explorer.html"), str(root / "out" / "reports" / "ingest_report.json"))
    explorer = site / "explorer.html"
    explorer.write_text(
        explorer.read_text().replace(
            "<nav>\n  <h1>APTG Data</h1>",
            '<nav>\n  <h1>APTG Data</h1>\n'
            '  <p class="sub" style="margin-bottom:8px">'
            '<a href="index.html">&larr; Back to the planner</a></p>',
            1,
        )
    )
    return {
        "db_before": before,
        "db_after": after,
        "modules": modules,
        "page_bytes": size,
        "explorer_bytes": (site / "explorer.html").stat().st_size,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="aptg-webbuild")
    ap.add_argument("--root", default=".")
    ap.add_argument("--db", default="out/aptg.sqlite")
    ap.add_argument("--site", default="site")
    a = ap.parse_args(argv)

    info = build(Path(a.root), Path(a.site), Path(a.db))
    print(
        f"database {info['db_before'] / 1e6:.1f} MB -> {info['db_after'] / 1e6:.1f} MB"
        f"  ({100 * info['db_after'] / info['db_before']:.0f}%)"
    )
    print(f"engine modules: {', '.join(info['modules'])}")
    print(f"planner page: {info['page_bytes'] / 1000:.0f} kB")
    print(f"explorer page: {info['explorer_bytes'] / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
