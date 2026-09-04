# 2. Data ingestion

The scheduling engine reads one SQLite database. Everything upstream of it exists to
build that database from published sources, and to prove the result is trustworthy.

## Sources

| Source | Contributes |
|---|---|
| Three Pingala pre-registration exports (two odd terms, one even) | courses, offerings, prerequisites, semester availability |
| Summer term export | the 157-course summer schedule |
| `Course_Schedule_2026-27-1.pdf` | identifies the current odd term as 2026-27/I |
| Approved Course Master | 2,918 approved courses; which codes *exist* |
| 19 DOAA department templates | programmes, semester grids, credit tables |
| Institute Minor list | 28 baskets across 15 departments, split by UGARC batch window |
| UG Manual | credit limits, duration, minor rules, graduation minima |
| WSAIS / DIS pages | B.Cyber and Intelligent Systems programmes |

## What the build produces

```
terms                     4        programmes               52
course_master         2,918        semester_specs          344
code_aliases            643        ... validated           200
courses               1,482        template_slots        2,281
offerings             2,441        minor_baskets            28
prereq_edges            460        policy_rules             24
parity   ODD 705 / EVEN 518 / BOTH 226 / NEITHER 17
```

## Guarantees

**Deterministic.** No generative model anywhere in the pipeline. Two runs over identical
inputs produce byte-identical database content; a test builds twice and compares hashes.

**Provenance on every row.** Source file, locator (`Sheet1!row=42`, `p.7`) and SHA-256,
plus a confidence of `OBSERVED` (read directly), `DERIVED` (computed by a stated rule) or
`TENTATIVE` (the source says it is provisional, or extraction could not be cross-checked).

**Nothing dropped silently.** Input the parsers cannot handle goes to
`ingest_quarantine` with its reason and locator. This is not incidental: the
lecture-section bug below was found because the dropped rows were visible.

**Extraction is checked, not trusted.** Credits summed from a template's slots are
compared against the total printed on that template. 200 of 344 semester specs validate;
the remainder are marked `TENTATIVE` rather than presented as read.

## Three problems the sources contain

**Codes are spelled differently.** The schedule exports print `ESO207`; templates and the
minor list print `ESO207A`. Nothing maps one to the other, so minor courses failed to
join at all. 643 aliases now bridge them — while refusing to merge `AE201A` and `AE201M`,
which are genuinely different courses and whose conflation would erase the batch
distinction the model exists to preserve.

**Some courses were replaced by combinations.** ESC101 became ESC111 *plus* one of
ESC112/ESC113. Pingala flattens this into `( ESC101A OR ESC111M OR ESC112M OR ESC113M )`,
which read literally lets one 7-credit module unlock the whole CSE chain. Equivalences
rewrite such expressions; the original string and mechanical parse are retained so the
rewrite stays auditable.

**Two template layouts resist geometry.** Most templates are read by recovering the
column ruler from the header row. EE and AE emit each table row as a single text run with
no usable column positions, so those pages fall back to reading rows in order. The
fallback is weaker, so its output still faces the credit check and is marked `TENTATIVE`
where no printed total can be recovered.
