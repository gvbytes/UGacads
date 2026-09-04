# APTG data schema

Thirteen tables plus a quarantine log, built deterministically from published IIT Kanpur
sources. Every row carries `source_file`, `source_locator`, `source_sha256` and a
`confidence` of `OBSERVED`, `DERIVED` or `TENTATIVE`.

## Course identity

The sources spell the same course three ways. The schedule exports print the code the
registrar used that term (`MTH111M`, `ESC111M`, `ESO207`); the department templates and
the Courses of Study print the unsuffixed form (`MTH111`, `ESC111`); the minor catalogue
prints the UGARC form (`ESO207A`). Reconciling them is a precondition for everything
else — until it was done, the Institute Core of every first-year semester matched no
course row and could not be scheduled at all.

Two rules do the matching, and both are recorded in `code_alias`:

* **`TITLE_MATCH`** — the approved course master gives two codes sharing a stem the same
  title, so they are one course renumbered. This is what establishes `AE201A` = `AE201M`,
  a pair the structural rule must refuse.
* **`STEM`** — two codes share a stem and at least one carries no suffix, so the
  unsuffixed one is the same course without a variant claim. `MTH111` = `MTH111M`.
  This rule reads the *shape* of a code, which is not enough on its own, so it is
  **vetoed** when the master's title for the code shares no significant word with the
  course it would map to. `EE210A` is Microelectronics-I and `EE210` is Analog
  Electronics: one stem, two courses. Refusals are quarantined as
  `alias.title_conflict`. The check is deliberately narrow — departments reword courses
  constantly, and singular/plural and British/American spellings are normalised — so
  only a flat contradiction blocks an alias.

A suffixed code never resolves to a *differently* suffixed one on the structural rule
alone: the suffix is what separates the Old and New UGARC versions of a course.

## Confidence

| Value | Meaning |
|---|---|
| `OBSERVED` | Read directly from an authoritative source. |
| `DERIVED` | Computed from observed facts by a rule stated in the code. |
| `TENTATIVE` | The source itself says it is provisional, or extraction could not be cross-checked. |

## Tables

### `term`
The four Pingala schedule exports. `parity` is `ODD`, `EVEN` or `SUMMER`; `label` is the
term as published and `is_current` marks it as part of the current academic year.

The DOAA schedule `Course_Schedule_2026-27-1.pdf` contains exactly the course set of
`odd_sem_recent.xlsx`, which fixes that export as **2026-27/I**. The other odd export is
an earlier academic year.

### `course_master`
The institute's Approved Course Master: every course that **exists**, with its branch,
title and canonical code. Distinct from `course`, which holds every course **observed
being offered** in a loaded term. The difference is what separates "this course is real,
it simply is not on the three schedules held here" from "this code is wrong" — the
engine reports those differently. `discontinued` carries the master's own withdrawal
marker.

### `code_alias`
`from_code` → `to_code`, with the `method` that established it (see *Course identity*).
Persisted rather than recomputed so the mapping is auditable, and so that everything
reading the database resolves a code the same way the build did — including a student
who types the code printed on their own template.

### `course`
One row per distinct course. `identity_mode` is `CODED` for courses with an institute
course code, or `TITLE_ONLY` for the B.Cyber curriculum, which is published by title
alone. Title-only courses can be placed in a template but can never be resolved as a
prerequisite — an invariant the validator enforces. `credits` is the bracketed total
from the `L-T-P-S(N)` string; `ltps` keeps the components.

### `offering`
One row per course per term. Carries the timetable slot, lecture `section` (courses that
run in parallel sections appear as `... (TA111) /A`), venue, mode
(`REGULAR`/`FIRST-HALF`/`SECOND-HALF`) and the role tags parsed from the slot cell
(`DC`, `DE`, `OE`, `IC`, `PRF`, `Minor`, `UGP-n`).

### `course_availability`
Derived. `parity` is `ODD`, `EVEN`, `BOTH` or `NEITHER`, computed from the *regular*
terms a course is offered in; `NEITHER` means the summer term alone runs it. `summer` is
a separate flag rather than a third parity value, because the summer term is not a
regular semester (UG Manual 4.3.6): a course can run in odd semesters *and* summer, and
only the former constrains which semester can host it. `seen_current` says whether the current published schedule still
carries it; a course seen only in an older export is marked `TENTATIVE`, since it has
most likely been withdrawn. This is the semester-availability hard constraint, and it is what
stretches prerequisite chains: a chain of same-parity courses advances two semesters per
link, not one.

### `prereq_edge`
One row per prerequisite dependency **per term**, so provenance is preserved; deduplicate
on `(course_code, prereq_code)` for graph work. `expr_ast` is the mechanical parse of the
Pingala string, `expr_normalized` the reading after variant grouping, and `ambiguous`
marks the rows where the two disagree. `alternative_group` ties codes that are
interchangeable (one OR-arm) so the engine can require exactly one of them. `resolved`
says whether the prerequisite is offered in any loaded term.

### `semester_spec`
Per programme and semester. `kind` is `COURSEWORK`, `INTERNSHIP` or `THESIS`. Elective
capacity is zero outside coursework, which is what makes B.Cyber's semesters 5–8
structurally unable to host a Minor without any special case in the engine.
`printed_total` is the figure printed on the template; when the extracted sum falls
inside it the row is `OBSERVED`, otherwise `TENTATIVE`.

### `programme`
Degree programmes: department templates (`ME-BT`, `CSE-BTH`, …), plus `BCYBER`.
`batch_from`/`batch_to` carry batch windows where the source states one.

Where a department publishes separate templates per batch window the
window is part of the identifier — `PHY-BS-Y22_Y24` and `PHY-BS-Y25` are different
programmes, not one programme read twice.

Two extraction paths feed this table. Most templates are read geometrically, by
recovering the column ruler from the header row. EE and AE emit each table row as a
single text run with no usable column positions; those pages fall back to reading rows in
reading order, on the assumption that cells are left-packed in column order. The fallback
is weaker, so its output is still checked against the printed credit totals and is marked
TENTATIVE where no total can be recovered — every EE semester spec currently is.

### `template_slot`
Individual slots within a semester. `slot_type` is one of `IC`, `ESO`, `DC`, `DE`, `OE`,
`SCHEME`, `UGP`, `MTB`, `INTERNSHIP`, `FIXED`, `ELECTIVE`, `UNKNOWN`. `is_choice` marks
slots offering alternatives (`DE-1/UGP-1`) or unpublished baskets.

`semester_no` is not decoration: the engine treats it as a **floor**, so no course is
scheduled before the semester its own curriculum assigns it. A course may slip later —
that is how a backlog or an extension is expressed — but never earlier. Sequencing that
the prerequisite graph does not capture is carried here, and the published prerequisite
data is sparse enough that without this a third-semester Institute Core was being placed
in semester 1.

Slot credits are reconciled against the total printed on the template. Where grid
extraction produced a slot with no credit figure, the residual between the printed total
and the slots that do carry one is shared among them; where the slots carrying a figure
already account for the whole printed total, the remainder are extraction artefacts and
are dropped. Both outcomes are quarantined
(`template.credits_apportioned`, `template.phantom_slot`).

Two types exist for programmes published without course codes:

* `FIXED` — a taught course named by title with a semester the curriculum dictates. It is
  scheduled exactly where the programme puts it and can never resolve a prerequisite.
* `ELECTIVE` — a basket whose eligible courses the institute has not published. The slot
  reserves its credits so the semester load stays truthful, but no course is named for
  it and none is invented.

### `credit_rule`
Per-programme credit ranges by course type, from the template credit tables.

### `minor_basket`
One row per minor **stream**, not per department: CSE publishes four, CHM three, HSS
three, EE five, and a student reads one of them. Built from
`data/curated/minor_baskets.json`, a transcription of the institute minor catalogue;
every row carries its own `citation`.

`compulsory_codes` is a comma-joined list of requirement *groups*. A group may hold
alternatives separated by `|` where the source states them — `EE311|EE370` means either
satisfies that requirement — which the engine schedules as a two-course pool rather than
committing to an arm. `choose_n` further courses are then taken from `choice_codes`.

`ugarc_variant` (`OLD_Y21`, `NEW_Y22`, `ALL`) carries the batch window where a department
publishes two versions. `cpi_min` and `seat_cap` record the departmental selection
criteria the catalogue states (Cognitive Science 7.5, Tissue Engineering 7.0 and eight
seats, Functional Materials 6.0); DOAA mandates no global floor.

Course codes are printed suffixed in the catalogue (`ESO207A`) and unsuffixed in the
schedule exports (`ESO207`). `aptg_ingest.codes` maps between them at build time, so
every code stored here joins to `course`. A code that resolves to nothing is listed in
`unresolved_codes` and quarantined; `source_course_count` records how many courses the
source published, so a thinned basket is visible. A basket whose surviving courses can no
longer reach its stated credit floor is demoted to `TENTATIVE`.

### `course_equivalence`
Old-to-new course relationships that no one-to-one alias can express: a legacy course
replaced by a *combination* of new ones, such as ESC101 becoming ESC111 together with one
of ESC112/ESC113.

This exists because the Pingala prerequisite strings flatten such a family into a single
alternation — ESO207 reads `( ESC101A OR ESC111M OR ESC112M OR ESC113M )` — which taken
literally lets one 7-credit module unlock the whole CSE chain. Where an expression names
two or more members of an equivalence, the members are pruned and the remainder is ANDed
with the equivalence's own expression. The original string and the mechanical parse are
left untouched, so the rewrite is auditable rather than baked in.

Distinct from `code_alias`, which records that two spellings (`ESO207` / `ESO207A`) are
the *same* course.

### `policy_rule`
Hand-transcribed rules, each quoting and citing its source. Twenty-one rules covering
semester load (UG Manual 4.3.6), summer term, Institute Core minimum, the Honours CPI
criterion, minimum and maximum programme duration by batch (3.2), what a Minor entails
and which slots it may occupy (7.4), and the DOAA minor regulations — course count,
credit accounting against OE, the application window, and the rule that a Minor may be
taken in any department except one's own.

### `programme_eligibility`
Batch-gated programme options, e.g. the Department of Intelligent Systems Minor being
open only to students admitted from 2026-27.

### `ingest_quarantine`
Rows the parsers could not handle, with the reason and source locator. Nothing is dropped
silently.

## Reproducing

```bash
cd aptg
PYTHONPATH=src python3 -m aptg_ingest.cli --data data --out out --strict
PYTHONPATH=src python3 -m aptg_ingest.explorer
PYTHONPATH=src python3 -m pytest tests -q
```
