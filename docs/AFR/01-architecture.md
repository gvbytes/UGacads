# 1. Architecture: CSED and DPGR

The system separates two concerns the problem statement names separately, and the
separation is enforced in code rather than by convention: **CSED** decides what is
*legal*, **DPGR** decides which of the legal options is *preferable*. No preference can
promote a candidate that CSED rejected, and every preference is applied only to
candidates that already survived every hard constraint.

```
 sources ──► aptg_ingest ──► aptg.sqlite ──► aptg_engine ──► roadmap + log + flags
   PDF          parse,         16 tables      CSED: legality
   XLSX         validate,      provenance     DPGR: preference
   HTML         quarantine     per row
```

## CSED — Constraint Satisfaction Engine Design

`src/aptg_engine/engine.py`. Deterministic, with no generative model anywhere in the
path. Placement order is fixed by an explicit sort key, never by iteration order over a
set or dictionary, so the same profile always yields the same roadmap. A test asserts it
by solving twice and comparing the full result.

Six hard constraints, all applied before any preference is consulted:

| # | Constraint | Source |
|---|---|---|
| 1 | Prerequisite completion, evaluated over the parsed boolean expression | Pingala prerequisite field |
| 2 | Semester availability — an odd-only course cannot sit in an even semester | Three schedule exports |
| 3 | Credit limits, 35–65 per regular semester | UG Manual 4.3.6.1 |
| 4 | Semester type — internship and thesis semesters hold no course capacity | Programme curricula |
| 5 | Batch eligibility, including the Honours CPI criterion of 8.0 | DIS site; department templates |
| 6 | Maximum programme duration, by programme *and* batch | UG Manual 3.2 |

## DPGR — Dynamic Personalisation and Graph Routing

The optimisation layer. Candidates that pass CSED are ordered by a composite key:

1. courses still present in the current published schedule, ahead of withdrawn ones;
2. stated career interest;
3. credit fit against the slot the template drew;
4. course level appropriate to the student's year;
5. criticality — how deep a prerequisite chain the course unlocks;
6. course code, so ties resolve reproducibly rather than by accident of ordering.

Criticality is what makes the router more than a greedy filler: a course that unlocks a
three-deep chain is scheduled before one that unlocks nothing, even when both are legal
in the same semester.

## Verdicts

- `FEASIBLE` — everything scheduled within the requested horizon.
- `FEASIBLE_WITH_ADJUSTMENT` — schedulable, but not on the requested terms.
- `CURRENTLY_INFEASIBLE` — a hard constraint blocks it. The responsible constraint is
  named, and a concrete alternative pathway is computed where one exists.

The distinction the problem statement asks for — a hard infeasibility against a mere
preference conflict — falls out of *which* check failed, not out of a heuristic.
