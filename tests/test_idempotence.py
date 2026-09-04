"""The build must be reproducible: identical inputs give identical outputs."""
import hashlib
import sqlite3
from pathlib import Path

from aptg_ingest import db
from aptg_ingest.cli import build
from aptg_ingest.provenance import Quarantine

DATA = Path(__file__).resolve().parents[1] / "data"


def _content_hash(path: str) -> str:
    """Hash every table's rows in a stable order, ignoring page-level file layout."""
    conn = sqlite3.connect(path)
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        h = hashlib.sha256()
        for t in tables:
            h.update(t.encode())
            for row in conn.execute(f"SELECT * FROM {t}"):
                h.update(repr(row).encode())
        return h.hexdigest()
    finally:
        conn.close()


def test_two_builds_produce_identical_database_content(tmp_path):
    hashes = []
    for i in range(2):
        q = Quarantine()
        ds = build(DATA, q)
        out = tmp_path / f"run{i}.sqlite"
        db.write(ds, q.items, str(out))
        hashes.append(_content_hash(str(out)))
    assert hashes[0] == hashes[1], "ingestion is not deterministic"


def test_every_row_carries_provenance(tmp_path):
    q = Quarantine()
    ds = build(DATA, q)
    out = tmp_path / "p.sqlite"
    db.write(ds, q.items, str(out))
    conn = sqlite3.connect(out)
    try:
        for table in ("course", "offering", "prereq_edge", "programme", "template_slot"):
            missing = conn.execute(
                f"SELECT COUNT(*) FROM {table} "
                "WHERE source_file IS NULL OR source_sha256 IS NULL"
            ).fetchone()[0]
            assert missing == 0, f"{table} has {missing} rows without provenance"
    finally:
        conn.close()


def test_every_row_carries_a_known_confidence(tmp_path):
    q = Quarantine()
    ds = build(DATA, q)
    out = tmp_path / "c.sqlite"
    db.write(ds, q.items, str(out))
    conn = sqlite3.connect(out)
    try:
        for table in ("course", "offering", "prereq_edge", "programme", "template_slot",
                      "semester_spec", "minor_basket", "policy_rule"):
            bad = conn.execute(
                f"SELECT COUNT(*) FROM {table} "
                "WHERE confidence NOT IN ('OBSERVED','DERIVED','TENTATIVE')"
            ).fetchone()[0]
            assert bad == 0, f"{table} has {bad} rows with an unknown confidence"
    finally:
        conn.close()
