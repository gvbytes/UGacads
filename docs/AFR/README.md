# Algorithmic Framework Report

Source sections for the AFR. The problem statement asks for Data Ingestion, Constraint
Application, Graph Traversal, Optimisation and Explainability to be clearly classified
and navigable; each has its own section here.

| Section | Covers |
|---|---|
| [1. Architecture](01-architecture.md) | CSED and DPGR, and how legality is separated from preference |
| [2. Data ingestion](02-data-ingestion.md) | sources, guarantees, and the three problems the sources contain |
| [3. Graph routing](03-graph-routing.md) | the prerequisite graph, parity stretching, traversal, summer |
| [4. Constraint application](04-constraints.md) | the six hard constraints and their citations |
| [5. Explainability](05-explainability.md) | per-placement reasons, rejections, failure causes, risk flags |
| [6. Edge cases](06-edge-cases.md) | five worked profiles, each reproducible and tested |

The database schema is documented separately in [`SCHEMA.md`](../../SCHEMA.md).

The report is capped at 15 pages when assembled to PDF.
