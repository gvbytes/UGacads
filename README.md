# APTG — Algorithmic Academic Pathway and Template Generator

Turns a real IIT Kanpur student profile into a semester-by-semester roadmap that is
mathematically valid under the institute's published rules — and says why, in full,
including why it refused what it refused.

Built for the Anweshan'26 UG Academics problem statement by pool **Kshatriyas**.

## Quick start

```bash
python3 -m aptg_ingest.cli --data data --out out --strict   # build the database
python3 serve.py --port 8732                                # start the application
python3 -m pytest tests -q                                  # 124 tests
```

Python 3.11+ and `pypdf`. The web application itself uses only the standard library.

## What it does

Given batch, department, programme, current semester, CPI, completed courses and
preferences, it produces a roadmap, an explainability log, and visible risk flags, and
classifies the request as `FEASIBLE`, `FEASIBLE_WITH_ADJUSTMENT` or
`CURRENTLY_INFEASIBLE` — computing a concrete alternative pathway when the requested one
does not work.

The scheduler is deterministic and contains no generative model. The same profile always
yields the same roadmap; a test asserts it.

## Documentation

| | |
|---|---|
| [Algorithmic Framework Report](docs/AFR/README.md) | architecture, ingestion, graph routing, constraints, explainability, worked edge cases |
| [Database schema](SCHEMA.md) | all 16 tables, with provenance and confidence semantics |
| [Deployment](docs/PWA/DEPLOYMENT.md) | running locally, endpoints, and the browser build published to Pages |
| [Video notes](docs/VCA/README.md) | what the five-minute demonstration should cover |

## Layout

```
src/aptg_ingest/   sources → aptg.sqlite: parsers, validation, quarantine
src/aptg_engine/   CSED scheduler, DPGR ranking, and the web application
data/              published sources: schedules, templates, UG Manual, minors
ui/                the application page and the static data explorer
tests/             124 tests
docs/              AFR, PWA and VCA material
```

## Data

Built from published sources only: three Pingala pre-registration exports plus the
summer term, the 2026-27/I schedule, the Approved Course Master, 19 DOAA department
templates, the institute Minor list, the UG Manual, and the WSAIS and DIS programme
pages.

```
courses     1,482      programmes        52      prereq edges   460
offerings   2,441      template slots 2,281      minor baskets   28
parity      ODD 705 / EVEN 518 / BOTH 226 / NEITHER 17
```

Every row carries its source file, locator and SHA-256, and a confidence of `OBSERVED`,
`DERIVED` or `TENTATIVE`. Input the parsers cannot handle is quarantined with a reason
rather than dropped.

## Design commitments

- **Deterministic.** No LLM in the scheduling path. Two builds over the same inputs give
  byte-identical database content.
- **No hardcoded roadmaps.** Course arrays are never pre-calculated per profile; every
  plan is generated at runtime from the graph and the constraint set.
- **Cross-batch by construction.** Batch windows are first-class: minor baskets carry
  their UGARC window, PHY's two templates are distinct programmes, and Double Major
  duration differs by batch.
- **Uncertainty is shown, not hidden.** Seat caps are unpublished, some availability is
  observed in one parity only, some prerequisites are ambiguous. All are flagged.
