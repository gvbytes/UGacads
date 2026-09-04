"""Student academic state and stated preferences."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StudentProfile:
    batch: str                       # "Y24"
    department: str                  # "ME"
    programme_id: str                # "ME-BT"
    current_semester: int            # semester just completed is current_semester - 1
    cpi: float | None = None
    completed: set[str] = field(default_factory=set)
    declared_minors: list[str] = field(default_factory=list)

    @property
    def batch_year(self) -> int | None:
        digits = "".join(c for c in self.batch if c.isdigit())
        if not digits:
            return None
        n = int(digits)
        return 2000 + n if n < 100 else n


@dataclass
class Preferences:
    target_minor: str | None = None          # minor_id
    max_credits: int = 55                    # per-semester ceiling the student asks for
    target_semesters: int = 8                # preferred graduation point
    allow_extension: bool = True
    max_semesters: int = 10                  # hard ceiling on extension
    allow_summer: bool = False
    career_interests: list[str] = field(default_factory=list)
