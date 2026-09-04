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

## GitHub Pages

Pages serves files, not processes, so there is no server to answer `/api/*`. Rather than
ship a cut-down reimplementation in JavaScript — which would leave two engines to keep in
step and break the requirement that the result be reproducible — the site carries a
Python runtime and runs **the same engine source** in the browser under Pyodide.

Two pages are published:

| Page | What it is |
|---|---|
| `index.html` | the planner, solving client-side |
| `explorer.html` | the data explorer, on which the extraction can be checked |

The build is `python -m aptg_ingest.webbuild`, and it does three things: copies the
engine package verbatim, trims the database to the tables the engine actually reads
(2.8 MB to 1.8 MB — the course master, aliases and quarantine exist to audit the build,
not to solve), and rewrites `ui/app.html` so it waits for the runtime and routes its API
calls to it. The page is otherwise the file the local server serves, so the two cannot
drift apart.

A test checks the trim list against the SQL the engine issues, because dropping a table
would not fail the build — it would fail at solve time in a visitor's browser.

### What a visitor downloads

Roughly 1.8 MB of database, 29 kB of page, 81 kB of engine, and the Pyodide runtime from
jsDelivr. First load takes a few seconds behind a progress indicator; solving afterwards
is immediate. Nothing the visitor types leaves the tab.

### Enabling it

In the repository, **Settings → Pages → Source: GitHub Actions**. The workflow at
`.github/workflows/pages.yml` rebuilds the database from source, runs the test suite, and
publishes only if both succeed.

Pages on a private repository requires a paid plan; on the free tier the repository must
be public.

### If you would rather run it server-side

`serve.py` runs unmodified on any platform that executes Python — Render, Fly, Railway,
PythonAnywhere. That avoids the runtime download at the cost of hosting.
