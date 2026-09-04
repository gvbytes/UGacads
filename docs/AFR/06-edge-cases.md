# 6. Worked edge cases

Each case below is reproducible against the running application and is covered by a test.

## A. Late Minor aspirant — conditionally feasible

ME, Y23, semester 6, CPI 8.5, requesting the Aerospace Minor at a 55-credit ceiling.

The engine schedules `AE201M` and `AE333` into existing OE slots — a Minor consumes
elective capacity rather than adding credits — and then reports the request
**infeasible**, naming the cause: the remaining basket courses `AE211` and `AE341`
require `ESO204` and `AE311`, which an ME student never takes. A degree-only alternative
pathway is returned alongside, and it is `FEASIBLE`.

This is the shape the problem statement describes: not a shortage of credits, but a
prerequisite chain that cannot be satisfied in the remaining time.

## B. Workload ceiling forces an extension

The same student without the Minor needs 147 credits from semester 6 — 49 per semester
across three semesters. The ME template itself budgets 53, 57 and 51.

| Ceiling | Outcome |
|---|---|
| 52–65 | `FEASIBLE`, completes semester 8 |
| ≤ 50 | `FEASIBLE_WITH_ADJUSTMENT`, completes semester 9 |

At a 50-credit ceiling the plan spills into semester 9 — which is precisely the problem
statement's first example, a student who imposes a 50-credit maximum and needs a ninth
semester despite preferring eight. The engine also offers the alternative of raising the
ceiling to 55, which restores an eight-semester plan.

## C. Structural infeasibility — B.Cyber

B.Cyber semesters 5 to 8 are full-time internship at 54 credits each. A Minor request
from a B.Cyber student returns:

> Bachelors in Cybersecurity has no coursework semesters remaining from semester 5:
> every remaining semester is internship or thesis, so there is no elective capacity in
> which minor courses could be scheduled. This is a structural property of the
> programme, not a scheduling shortfall.

The engine reproduces the published curriculum exactly — 51, 50–54, 50, 46 credits of
coursework then 54 per internship semester — and reserves its three unpublished elective
baskets with credits but no named course, rather than inventing one.

## D. Cross-batch generalisation

The same request, opposite answers, from published rules:

- A **Y21** student requesting the `AE-NEW_Y22` Minor is refused: that basket applies to
  Y22 and later. A Y23 student is not refused.
- A **Y25** student is ineligible for the Intelligent Systems Minor; a **Y26** student is
  eligible.
- **PHY** publishes different templates for Y22–Y24 and Y25-onward; they are distinct
  programmes with distinct identifiers, not one template read twice.
- **Double Major** duration is 12 semesters for Y21-and-earlier, 15 for Y22-and-later.

## E. Summer term

At a 40-credit ceiling, enabling summer inserts a 27-credit summer term after semester 8
— the UG Manual cap — and drains semester 9 to 9 credits. Summer is used only to clear a
backlog; a requirement the curriculum places later is never pulled into it.
