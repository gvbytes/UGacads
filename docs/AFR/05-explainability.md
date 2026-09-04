# 5. Explainability and risk flags

The problem statement requires the system to explain why a course sits where it does,
why an alternative was rejected, and which constraint is responsible when a request
cannot be met. All three are produced by the scheduler itself, not narrated afterwards.

## Per-placement reasons

Every placed course carries the reason it was placed there:

> `ME302` — prerequisites satisfied by semester 6; offered in even semesters, and 6 is
> even; unlocks a chain 2 deep, so it is placed early

## Rejections

A course refused for a particular semester records why, deduplicated so one cause is
listed once rather than once per pass:

> S6 `AE201M` — offered in odd semesters only

## Failure causes

When a requirement cannot be scheduled at all, the responsible constraint is named. An
unfillable basket reports each candidate and its blocker rather than a bare failure:

> no course in the Aerospace Engineering minor elective 2 basket could be placed:
> AE211 — prerequisite not met: needs ESO204; AE321 — prerequisite not met: needs AE209

## Decision log

The log records the constraints applied and cites them:

> Per-semester ceiling 50 credits (student asked for 50; UG Manual 4.3.6.1 allows at most 65).
> Planning horizon capped at semester 12 by UG Manual 3.2.
> The minor needs 4 course slots; 13 elective slots remain in the template. UG Manual 7.4
> allows minor courses to occupy OE, DE, ESO or SCHEME slots.

## Risk flags

Uncertainty is surfaced, never hidden:

| Flag | Meaning |
|---|---|
| `extension` | the plan depends on registering beyond the standard eight semesters |
| `seats` | scheduling a course does not guarantee a seat; caps are unpublished |
| `not_in_current_schedule` | a scheduled course appears only in an older export |
| `single_parity` | observed in one parity only across the loaded terms |
| `ambiguous_prerequisite` | mixed AND/OR with no grouping; read by the variant rule |
| `unpublished_basket` | credits reserved but no course nameable |
| `title_only_curriculum` | published by title with no course code |
| `assumed_history` | past elective slots assumed complete, not verified |
| `cpi_unknown` | a CPI-dependent rule could not be evaluated |
| `minor_slot_shortfall` | the minor needs more elective slots than remain |
