"""Request handlers, independent of how the request arrived.

The same four operations back both transports: the local HTTP server, and the browser
build published to GitHub Pages, which runs this module under Pyodide. Keeping them here
rather than inside the server means the deployed site executes the identical code path,
so a roadmap generated in the browser is the same roadmap the server would produce.
"""
from __future__ import annotations

from pathlib import Path

from .data import load
from .engine import Engine
from .state import Preferences, StudentProfile


class State:
    catalogue = None
    engine = None

    @classmethod
    def init(cls, db_path: Path) -> None:
        cls.catalogue = load(str(db_path))
        cls.engine = Engine(cls.catalogue)


def catalogue_payload() -> dict:
    cat = State.catalogue
    progs = []
    for pid, p in sorted(cat.programmes.items()):
        specs = sorted(
            n for (q, n) in cat.semester_kinds if q == pid
        )
        slots = cat.slots_by_programme.get(pid, [])
        progs.append(
            {
                "programme_id": pid,
                "name": p.get("name"),
                "kind": p.get("kind"),
                "department": p.get("department") or p.get("school"),
                "batch_from": p.get("batch_from"),
                "batch_to": p.get("batch_to"),
                "semesters": len(specs),
                "slots": len(slots),
                "internship_semesters": sorted(
                    n for (q, n) in cat.semester_kinds
                    if q == pid and cat.semester_kinds[(q, n)] == "INTERNSHIP"
                ),
                "confidence": p.get("confidence"),
            }
        )
    minors = [
        {
            "minor_id": m.minor_id,
            "title": m.title,
            "stream": m.stream,
            "label": m.label,
            "department": m.department,
            "variant": m.ugarc_variant,
            "batch_from": m.batch_from,
            "batch_to": m.batch_to,
            "compulsory": ["|".join(g) for g in m.compulsory],
            "choose_n": m.choose_n,
            "choices": list(m.choices),
            "unresolved": list(m.unresolved),
            "source_course_count": m.source_course_count,
            "credits_min": m.credits_min,
            "credits_max": m.credits_max,
            "cpi_min": m.cpi_min,
            "seat_cap": m.seat_cap,
            "notes": m.notes,
            "citation": m.citation,
            "confidence": m.confidence,
        }
        for m in sorted(cat.minors.values(), key=lambda x: (x.department, x.stream, x.ugarc_variant))
    ]
    depts = sorted({c.department for c in cat.courses.values() if c.department})
    return {
        "programmes": progs,
        "minors": minors,
        "departments": depts,
        "policy": [
            {
                "rule_id": r["rule_id"],
                "name": r["name"],
                "min": r["value_min"],
                "max": r["value_max"],
                "unit": r["unit"],
                "citation": r["citation"],
            }
            for r in sorted(cat.policy.values(), key=lambda r: r["rule_id"])
        ],
        "eligibility": [
            {
                "rule_id": r["rule_id"], "option": r["option"], "department": r["department"],
                "programme_id": r["programme_id"], "batch_from": r["batch_from"],
                "eligible": bool(r["eligible"]), "citation": r["citation"], "text": r["text"],
            }
            for r in cat.eligibility
        ],
        "counts": {
            "courses": len(cat.courses),
            "prereq_courses": len(cat.prereqs),
        },
    }


def template_courses(programme_id: str) -> list[dict]:
    """Named courses of a programme's template, for the completed-courses picker."""
    cat = State.catalogue
    out = []
    for s in cat.slots_by_programme.get(programme_id, []):
        if not s.course_code:
            continue
        c = cat.courses.get(s.course_code)
        out.append(
            {
                "code": s.course_code,
                "semester": s.semester_no,
                "title": c.title if c else "",
                "credits": (c.credits if c else s.credits),
                "parity": c.parity if c else None,
                "in_catalogue": c is not None,
            }
        )
    seen, uniq = set(), []
    for r in out:
        if r["code"] in seen:
            continue
        seen.add(r["code"])
        uniq.append(r)
    return sorted(uniq, key=lambda r: (r["semester"], r["code"]))


def search_courses(q: str, limit: int = 40) -> list[dict]:
    cat = State.catalogue
    q = q.strip().lower()
    if not q:
        return []
    out = []
    for code in sorted(cat.courses):
        c = cat.courses[code]
        if q in code.lower() or q in c.title.lower():
            out.append(
                {"code": code, "title": c.title, "credits": c.credits,
                 "parity": c.parity, "department": c.department}
            )
        if len(out) >= limit:
            break
    return out


def plan(body: dict) -> dict:
    profile = StudentProfile(
        batch=str(body.get("batch") or "Y24"),
        department=str(body.get("department") or ""),
        programme_id=str(body.get("programme_id") or ""),
        current_semester=int(body.get("current_semester") or 1),
        cpi=float(body["cpi"]) if body.get("cpi") not in (None, "") else None,
        completed=set(body.get("completed") or []),
    )
    prefs = Preferences(
        target_minor=(body.get("target_minor") or None),
        max_credits=int(body.get("max_credits") or 55),
        target_semesters=int(body.get("target_semesters") or 8),
        allow_extension=bool(body.get("allow_extension", True)),
        max_semesters=int(body.get("max_semesters") or 10),
        allow_summer=bool(body.get("allow_summer", False)),
        career_interests=[s for s in (body.get("career_interests") or []) if s.strip()],
    )
    roadmap = State.engine.solve(profile, prefs)
    result = roadmap.as_dict()

    # When a minor was requested and failed, also compute the degree-only pathway so the
    # interface can offer a concrete alternative rather than only a refusal.
    if prefs.target_minor and roadmap.verdict != "FEASIBLE":
        alt_prefs = Preferences(**{**prefs.__dict__, "target_minor": None})
        alt = State.engine.solve(profile, alt_prefs)
        result["alternative"] = {
            "label": "Degree without the requested minor",
            **alt.as_dict(),
        }
    elif roadmap.verdict == "FEASIBLE_WITH_ADJUSTMENT":
        # Two levers, tried in the order that asks least of the student: the summer
        # term first, since it does not extend the degree, then a higher credit load.
        candidates = []
        if not prefs.allow_summer:
            candidates.append((
                {"allow_summer": True},
                "Same plan using the summer term (at most 27 credits, UG Manual 4.3.6)",
            ))
        if prefs.max_credits < 65:
            raised = min(65, prefs.max_credits + 5)
            candidates.append((
                {"max_credits": raised},
                f"Same plan with the ceiling raised to {raised} credits",
            ))
        for override, label in candidates:
            alt = State.engine.solve(profile, Preferences(**{**prefs.__dict__, **override}))
            if alt.verdict == "FEASIBLE":
                result["alternative"] = {"label": label, **alt.as_dict()}
                break
    return result


