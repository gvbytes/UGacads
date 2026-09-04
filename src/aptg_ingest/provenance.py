"""Source tracking. Every extracted fact must be traceable to a file and a locator."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# Confidence levels, applied to every emitted row.
OBSERVED = "OBSERVED"    # read directly from an authoritative source
DERIVED = "DERIVED"      # computed from observed facts by a documented rule
TENTATIVE = "TENTATIVE"  # source itself says it is provisional, or extraction is uncertain

CONFIDENCES = (OBSERVED, DERIVED, TENTATIVE)


@lru_cache(maxsize=256)
def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class Source:
    """Where a fact came from.

    locator is human-checkable: "Sheet1!row=42" for spreadsheets, "p.7" for PDFs,
    "semesters.2[4]" for structured programme files.
    """

    file: str
    locator: str

    # Facts the pipeline computes rather than reads have no file to hash. They are
    # still sourced — to a named rule in the code — so they carry a Source, with this
    # in place of a digest.
    DERIVED_FILE = "(derived)"

    @classmethod
    def derived(cls, locator: str) -> "Source":
        return cls(cls.DERIVED_FILE, locator)

    @property
    def sha256(self) -> str:
        if self.file == self.DERIVED_FILE:
            return ""
        return file_sha256(self.file)

    @property
    def name(self) -> str:
        return Path(self.file).name

    def at(self, locator: str) -> "Source":
        return Source(self.file, locator)

    def as_row(self) -> dict:
        return {
            "source_file": self.name,
            "source_locator": self.locator,
            "source_sha256": self.sha256,
        }


@dataclass
class Quarantine:
    """Rows we could not parse. Never dropped silently; written to out/quarantine/."""

    items: list[dict] = field(default_factory=list)

    def add(self, kind: str, source: Source, reason: str, raw: str = "") -> None:
        self.items.append(
            {
                "kind": kind,
                "reason": reason,
                "raw": (raw or "")[:500],
                **source.as_row(),
            }
        )

    def count(self, kind: str | None = None) -> int:
        if kind is None:
            return len(self.items)
        return sum(1 for i in self.items if i["kind"] == kind)

    def kinds(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for i in self.items:
            out[i["kind"]] = out.get(i["kind"], 0) + 1
        return dict(sorted(out.items()))
