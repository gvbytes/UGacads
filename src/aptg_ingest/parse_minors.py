"""Load the curated minor catalogue into batch-specific course baskets.

`data/curated/minor_baskets.json` is a hand transcription of the institute minor
catalogue, one row per *stream* — CSE publishes four, CHM three, HSS three, EE five —
because a stream is what a student actually reads. It replaced a text scrape of
`minors-at-iitk-updated.pdf` that merged a department's streams into one blob, lost most
of the "choose N" figures and mangled several titles.

Two things happen here that the JSON cannot state for itself:

* **Code resolution.** The catalogue prints suffixed codes (``ESO207A``); the schedule
  exports print unsuffixed ones (``ESO207``). :mod:`aptg_ingest.codes` maps between them
  against the loaded course table. A code that resolves to nothing is quarantined as
  ``minor.unresolved_code``, so a basket is never silently short of a course.
* **Alternative groups.** A compulsory entry may be a list, meaning "any one of these".
  Those are flattened to ``A|B`` in the stored string, which the engine reads as an OR.
"""
from __future__ import annotations

import json

from .codes import Resolver
from .model import MinorBasket, PolicyRule
from .provenance import OBSERVED, Quarantine, Source

ALT = "|"


def _group(entry, resolver, q, src, minor_id, unresolved) -> str | None:
    """Render one compulsory entry — a code or a list of alternatives — as a string."""
    codes = [entry] if isinstance(entry, str) else list(entry)
    resolved, missing = resolver.resolve_all(codes)
    for m in missing:
        unresolved.append(m)
        q.add(
            "minor.unresolved_code",
            src,
            f"{minor_id}: compulsory course {m} is not offered in any loaded term",
            m,
        )
    if not resolved:
        return None
    return ALT.join(resolved)


def _reachable_credits(groups: list[str], choices: list[str], choose_n: int, credits) -> int:
    """The most credits this basket can yield from courses that actually resolved.

    Each compulsory group contributes its best alternative; the basket contributes its
    ``choose_n`` heaviest courses. Used only to detect a basket thinned so far by
    unresolved codes that it can no longer reach the credit total its source states.
    """
    total = 0
    for g in groups:
        total += max((credits.get(c) or 0) for c in g.split(ALT))
    weights = sorted((credits.get(c) or 0) for c in choices)
    total += sum(weights[len(weights) - choose_n:]) if choose_n else 0
    return total


def parse_minors(
    path: str,
    credits: dict[str, int | None],
    q: Quarantine,
    resolver: Resolver | None = None,
) -> list[MinorBasket]:
    """Load the curated baskets, resolving every code against the course table.

    The shared :class:`~aptg_ingest.codes.Resolver` is used rather than the bare
    structural rule, so that a basket cannot pick up a course the rest of the pipeline
    has refused. EE210A is Microelectronics-I and EE210 is Analog Electronics; before
    this was shared, the EE minor required the second while every other consumer
    correctly declined to equate them.
    """
    with open(path) as fh:
        data = json.load(fh)

    resolver = resolver or Resolver(set(credits))
    out: list[MinorBasket] = []

    for b in data["baskets"]:
        minor_id = b["minor_id"]
        src = Source(path, f"baskets[{minor_id}]")

        unresolved: list[str] = []
        groups = []
        for entry in b["compulsory"]:
            rendered = _group(entry, resolver, q, src, minor_id, unresolved)
            if rendered:
                groups.append(rendered)

        choices, missing_choices = resolver.resolve_all(b["choices"])
        for m in missing_choices:
            unresolved.append(m)
            q.add(
                "minor.unresolved_code",
                src,
                f"{minor_id}: basket course {m} is not offered in any loaded term",
                m,
            )

        # choose_n is stated by the source against the published basket. When some of
        # that basket did not resolve, the requirement cannot exceed what is left, and
        # the shortfall is recorded rather than papered over.
        choose_n = b["choose_n"] or 0
        if choose_n > len(choices):
            q.add(
                "minor.basket_short",
                src,
                f"{minor_id}: source asks for {choose_n} of {len(b['choices'])} courses "
                f"but only {len(choices)} resolve; requirement reduced to {len(choices)}",
                ",".join(missing_choices),
            )
            choose_n = len(choices)

        if not groups and not choose_n:
            q.add(
                "minor.no_courses",
                src,
                f"{minor_id}: no compulsory course and no fillable basket survived "
                "resolution; the basket is dropped rather than offered as an empty minor",
                b.get("notes") or "",
            )
            continue

        # A basket thinned below its own credit floor cannot be earned from the courses
        # this data can see. It stays in the catalogue — the courses may simply be absent
        # from the three loaded terms — but is demoted to TENTATIVE so that neither the
        # engine nor the interface presents it as a settled requirement.
        confidence = b["confidence"]
        reachable = _reachable_credits(groups, choices, choose_n, credits)
        floor = b["credits_min"]
        if floor and reachable < floor:
            confidence = "TENTATIVE"
            q.add(
                "minor.credits_unreachable",
                src,
                f"{minor_id}: the courses that resolve yield at most {reachable} credits "
                f"against a stated requirement of {floor}; {len(unresolved)} published "
                "course(s) are not offered in any loaded term. Basket demoted to TENTATIVE.",
                ",".join(sorted(set(unresolved))),
            )

        out.append(
            MinorBasket(
                minor_id=minor_id,
                department=b["department"],
                title=b["title"],
                stream=b["stream"],
                ugarc_variant=b["ugarc_variant"],
                batch_from=b["batch_from"],
                batch_to=b["batch_to"],
                compulsory_codes=",".join(groups),
                choose_n=choose_n,
                choice_codes=",".join(choices),
                unresolved_codes=",".join(sorted(set(unresolved))),
                source_course_count=len(b["compulsory"]) + (b["choose_n"] or 0),
                credits_min=b["credits_min"],
                credits_max=b["credits_max"],
                cpi_min=b["cpi_min"],
                seat_cap=b["seat_cap"],
                template_ref=b["template_ref"],
                notes=b["notes"],
                citation=b["citation"],
                confidence=confidence,
                **src.as_row(),
            )
        )

    return out


def institute_minor_rules(path: str) -> list[PolicyRule]:
    """The DOAA-level rules the catalogue states above the department sections.

    Emitted as policy rules so the engine can cite them the same way it cites the UG
    Manual, rather than carrying the wording inline.
    """
    with open(path) as fh:
        r = json.load(fh)["institute_rules"]

    return [
        PolicyRule(
            rule_id="minor.own_department",
            scope="UG",
            name="Department a Minor may be taken in",
            value_min=None,
            value_max=None,
            unit=None,
            text=(
                "B.Tech, BS and Dual Degree (Category A) students may apply for a Minor "
                "in any department except their own. Dual Degree (Category B/C) and "
                "Double Major students may apply in any third department."
            ),
            citation=r["citation"],
            confidence=OBSERVED,
        ),
        PolicyRule(
            rule_id="minor.course_count",
            scope="UG",
            name="Courses a Minor consists of",
            value_min=r["courses_min"],
            value_max=r["courses_max"],
            unit="courses",
            text=(
                f"A Minor consists of {r['courses_min']} to {r['courses_max']} courses "
                f"worth {r['credits_min']} to {r['credits_max']} credits, counted "
                f"against the {r['counts_against']} requirements of the primary degree "
                "template."
            ),
            citation=r["citation"],
            confidence=OBSERVED,
        ),
        PolicyRule(
            rule_id="minor.application_window",
            scope="UG",
            name="When a Minor may be applied for",
            value_min=min(r["application_semesters"]),
            value_max=max(r["application_semesters"]),
            unit="semester",
            text=(
                "DOAA invites formal Minor applications during a batch's "
                f"{', '.join(str(s) for s in r['application_semesters'])}th semesters. "
                "A student who completes the requirements without being formally "
                f"enrolled may apply retrospectively in semester "
                f"{r['retrospective_semester']}."
            ),
            citation=r["citation"],
            confidence=OBSERVED,
        ),
        PolicyRule(
            rule_id="minor.selection",
            scope="UG",
            name="Departmental selection for a Minor",
            value_min=None,
            value_max=None,
            unit=None,
            text=(
                "No global CPI floor is mandated by DOAA, but departmental selection "
                "committees frequently enforce CPI cutoffs and seat caps. Those are "
                "recorded per minor in minor_basket.cpi_min and minor_basket.seat_cap."
            ),
            citation=r["citation"],
            confidence=OBSERVED,
        ),
    ]
