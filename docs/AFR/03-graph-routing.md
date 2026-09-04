# 3. Graph traversal and optimisation

## The prerequisite graph

Courses are nodes; a prerequisite is a directed edge. The graph is built from the
Pingala prerequisite field, parsed into a boolean expression tree rather than a flat
list, because the institute's requirements are genuinely boolean:

```
( ESO201 OR ESO201A AND ESO204A OR ESO204 )
```

Read with ordinary precedence that is `ESO201 OR (ESO201A AND ESO204A) OR ESO204`. The
intended reading is `(ESO201 OR ESO201A) AND (ESO204A OR ESO204)`, because each pair is
one course under two numbering schemes. Both readings are stored; the 18 expressions
where they disagree are flagged `ambiguous` rather than silently resolved.

Acyclicity is checked on every build and is a hard invariant: a cycle makes scheduling
impossible, so the build fails rather than emitting a graph that cannot be satisfied.

## Why depth alone understates the problem

The deepest chain in the data is three edges. That sounds trivial for an eight-semester
degree, and it is not, because **semester parity stretches chains**. A course offered
only in even semesters cannot follow its prerequisite in the very next semester; it must
wait a full year. A three-edge chain of same-parity courses spans up to six semesters,
not four.

```
MSO201 (even) ──► MTH309 (even) ──► MTH614 (even)
     S2                S4                S6
```

For a student declaring a Minor in semester 5, 29 of the chains in the data end after
semester 8. The constraint that binds is parity, not credit shortage — which is exactly
the reasoning the problem statement's first worked example describes.

## Traversal

Semesters are visited in order from the student's current one. Within each semester the
engine repeatedly selects the single best eligible requirement, so that a placement can
unlock further placements inside the same pass, and stops when no candidate fits the
remaining credit budget.

A requirement is eligible when its prerequisite expression evaluates true against
everything completed or already scheduled earlier, the course is offered in that
semester's parity, and its credits fit the remaining budget.

## Summer term

The summer term follows an even semester. It is modelled separately from the regular
semesters because it behaves differently: a 27-credit ceiling instead of 65, a course
set of 157 against roughly 750, and no effect on the graduation semester or the maximum
programme duration. It is used only to clear a backlog — a requirement the curriculum
places *later* is never pulled into scarce summer credits.
