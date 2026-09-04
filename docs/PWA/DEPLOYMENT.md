# Prototype web application — running and deployment

## Run locally

```bash
python3 -m aptg_ingest.cli --data data --out out --strict   # build the database
python3 serve.py --port 8732                                # start the application
```

Open `http://localhost:8732`. Python 3.11+ and `pypdf` are the only requirements; the
web application itself uses nothing outside the standard library, so there is no install
step, no build step and no framework.

## Interface

A profile form on the left — batch, department, programme, current semester, CPI,
completed courses, target minor, workload ceiling, graduation target, willingness to
extend, summer availability, career interests — and the generated roadmap on the right
across four tabs: **Roadmap**, **Explainability**, **Risk flags** and **Rejected**. An
**Alternative** tab appears whenever the requested pathway does not work and a concrete
alternative exists.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | the application page |
| GET | `/api/catalogue` | programmes, minors, departments, policy limits |
| GET | `/api/template?programme_id=` | a programme's named courses, for the picker |
| GET | `/api/courses?q=` | course lookup |
| POST | `/api/plan` | profile and preferences in, roadmap out |

## A note on GitHub Pages

GitHub Pages serves static files only. It cannot execute the Python scheduler, so the
full application cannot run there unmodified.

What **is** published to Pages is the **data explorer** (`ui/explorer.html`) — a
self-contained page with the dataset embedded, which needs no server. It exposes the
course catalogue, the prerequisite expressions, computed chain spans, the templates,
minors, policy rules and the ingestion quarantine, and is the surface on which the
extraction can be inspected and disputed.

To host the interactive planner itself, the options are a platform that runs Python
(Render, Fly, Railway, PythonAnywhere — `serve.py` runs unmodified), or compiling the
engine to run in the browser under Pyodide. The second keeps everything on Pages at the
cost of shipping a Python runtime to the client.
