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

## Minor eligibility, in full

UG Manual 7.4 governs more than the credit count:

- **7.4.1(a)–(d)** — a Minor may be taken in any department *except the student's own*.
  A Double Major student is additionally excluded from their second major's department,
  and a Dual Degree student from their PG department.
- **7.4.1(e)** — there is **no CPI criterion** for a Minor. Allotment is by seat
  availability alone, so the engine never gates a Minor on CPI.
- **7.4.1(g), 7.4.2(c)** — more than one Minor is permitted; up to three may be applied
  for per cycle, at the end of the 4th, 5th and 6th semesters.
- **7.4.6** — a department admits at most 20 percent of its batch strength, so completing
  the courses does not by itself secure the Minor. Raised as a risk flag on every plan
  that includes one.

## Minors consume elective capacity

UG Manual 7.4: *"A student may take Minor courses in OE, DE, ESO, or SCHEME slot."* A
Minor therefore **retargets slots the template already provides** rather than adding
27–44 credits on top. Modelling it the other way overstated every minor plan's workload
and turned feasible pathways into false refusals.


## Why an extension happens, and which kind it is

An overrun has two quite different causes, and the plan states which applies.

A **capacity shortfall** means the work genuinely does not fit: more credits remain than
the ceiling times the remaining semesters can hold.

A **packing extension** means the total would fit but the courses cannot be arranged to
fit it, because courses are indivisible. This is the common case at IIT Kanpur. Of the
open electives available to a mechanical engineering student in an odd semester, the
credit sizes on offer are `{5: 1, 9: 32}` — almost every one is a nine-credit course. A
fifty-credit ceiling therefore admits five electives in a semester and never six, so two
semesters hold ninety credits of electives however they are arranged.

For the worked profile 147 credits remain against a capacity of 150, and yet nine credits
cannot be placed. Reporting only "completes in semester 9" would leave the student to
guess why. Instead the engine says the total is not the problem, and then names the
lowest ceiling that removes the overrun — 51 credits for this profile, one above what was
asked for. The figure is verified by re-solving at that ceiling before it is offered.
