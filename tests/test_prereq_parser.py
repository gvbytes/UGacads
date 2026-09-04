"""Unit tests for the prerequisite expression parser."""
import json

from aptg_ingest import parse_prereq as pp


def norm(text):
    return json.loads(pp.parse(text).normalized_json())


def test_single_course():
    assert norm("( BSE667  )") == {"op": "COURSE", "code": "BSE667"}


def test_plain_or():
    node = norm("( ESO202 OR ESO202A  )")
    assert node["op"] == "OR"
    assert {a["code"] for a in node["args"]} == {"ESO202", "ESO202A"}


def test_explicit_and_of_groups():
    node = norm("( AE201M  ) AND (AE209  )")
    assert node["op"] == "AND"
    assert {a["code"] for a in node["args"]} == {"AE201M", "AE209"}


def test_mixed_precedence_is_flagged_ambiguous():
    parsed = pp.parse("( ESO201 OR ESO201A AND ESO204A OR ESO204  )")
    assert parsed.ambiguous, "mixed AND/OR without grouping must be reported"


def test_variant_grouping_reads_old_new_code_pairs_as_alternatives():
    node = norm("( ESO201 OR ESO201A AND ESO204A OR ESO204  )")
    assert node["op"] == "AND"
    arms = [{a["code"] for a in arg["args"]} for arg in node["args"]]
    assert {"ESO201", "ESO201A"} in arms
    assert {"ESO204", "ESO204A"} in arms


def test_mechanical_ast_is_kept_alongside_the_normalized_reading():
    parsed = pp.parse("( ESO201 OR ESO201A AND ESO204A OR ESO204  )")
    assert json.loads(parsed.ast_json())["op"] == "OR", "raw precedence must be preserved"


def test_dangling_leading_operator_is_repaired_and_recorded():
    parsed = pp.parse("(OR MTH301  MTH408  )")
    assert parsed.error is None
    assert any("dangling" in r for r in parsed.repairs)


def test_juxtaposed_codes_become_a_conjunction():
    parsed = pp.parse("(OR MTH301  MTH408  )")
    assert any("implicit AND" in r for r in parsed.repairs)
    assert json.loads(parsed.normalized_json())["op"] == "AND"


def test_stem_strips_a_single_trailing_letter():
    assert pp.stem("ESO201A") == "ESO201"
    assert pp.stem("AE201M") == "AE201"
    assert pp.stem("ESO201") == "ESO201"


def test_alternative_groups_marks_interchangeable_codes():
    parsed = pp.parse("( ESO202 OR ESO202A  )")
    groups = pp.alternative_groups(parsed.normalized)
    assert groups["ESO202"] == groups["ESO202A"]
