# 4. Constraint application

Every constraint below is enforced in `Engine.solve` before any preference is consulted,
and each cites the source it comes from.

## Prerequisite completion

The parsed expression is evaluated against the set of courses completed or already
scheduled in an earlier semester. An `OR` is satisfied by any arm, an `AND` by all. When
a course is refused, the explanation names the cheapest route to satisfying it rather
than every alternative:

> `ME359` — prerequisite not met: needs ESO204

## Semester availability

Derived from which terms actually offer a course: `ODD`, `EVEN`, `BOTH`, or `NEITHER`
for summer-only courses. A course seen only in an *older* export is marked `TENTATIVE`
and ranked below currently-offered alternatives, because it has most likely been
withdrawn — 147 courses fall into that category.

## Credit limits

UG Manual 4.3.6.1 sets the normal load at 50 credits and the registrable range at 35–65.
The student's own ceiling applies on top, and the lower of the two governs. Summer is
capped separately at 27 credits, or 29 for a student graduating at its end (4.3.6).

## Semester type

`COURSEWORK`, `INTERNSHIP` or `THESIS`. Elective capacity is zero outside coursework.
This is what makes a Minor structurally impossible for a B.Cyber student — semesters 5
to 8 are full-time internship — without any special case in the scheduler.

## Batch eligibility

- Minor baskets carry their UGARC window; a Y21 student is refused a `NEW_Y22` basket.
- The Intelligent Systems Minor, Double Major and Dual Degree are open only to students
  admitted from 2026-27 onward.
- Honours programmes require a CPI of 8.0.

## Maximum duration

UG Manual 3.2, and it is batch-specific:

| Programme | Normal | Minimum | Max, Y22+ | Max, Y21− |
|---|---|---|---|---|
| BT/BS/BTM/BSM/BTH/BSH | 8 | 7 | 12 | 12 |
| Dual Degree | 10 | 9 | 15 | 15 |
| Double Major | 10 | 9 | **15** | **12** |

The Double Major row differs by batch, so the same request yields a different horizon for
a Y21 and a Y22 student.

## Minors consume elective capacity

UG Manual 7.4: *"A student may take Minor courses in OE, DE, ESO, or SCHEME slot."* A
Minor therefore **retargets slots the template already provides** rather than adding
27–44 credits on top. Modelling it the other way overstated every minor plan's workload
and turned feasible pathways into false refusals.
