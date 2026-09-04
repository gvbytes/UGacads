"""Minimal xlsx reader built on the standard library.

An xlsx file is a zip of XML parts; openpyxl is not needed to read a flat sheet and
avoiding it keeps the pipeline dependency-light and fully deterministic.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
COL_RE = re.compile(r"[A-Z]+")

Row = tuple[int, dict[str, str]]


def _shared_strings(z: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in si.iter(NS + "t")) for si in root]


def read_sheets(path: str) -> dict[str, list[Row]]:
    """Return {sheet_name: [(row_number, {column_letter: value}), ...]}."""
    with zipfile.ZipFile(path) as z:
        shared = _shared_strings(z)
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        rel_map = {r.get("Id"): r.get("Target") for r in rels}

        out: dict[str, list[Row]] = {}
        for sheet in wb.iter(NS + "sheet"):
            name = sheet.get("name") or "Sheet"
            target = rel_map.get(sheet.get(REL_NS + "id"), "")
            if not target.startswith("xl/"):
                target = "xl/" + target.lstrip("/")
            rows: list[Row] = []
            root = ET.fromstring(z.read(target))
            for row in root.iter(NS + "row"):
                cells: dict[str, str] = {}
                for c in row.iter(NS + "c"):
                    ref = c.get("r") or ""
                    col_match = COL_RE.match(ref)
                    if not col_match:
                        continue
                    ctype = c.get("t")
                    v = c.find(NS + "v")
                    inline = c.find(NS + "is")
                    if ctype == "s" and v is not None and v.text is not None:
                        value = shared[int(v.text)]
                    elif inline is not None:
                        value = "".join(t.text or "" for t in inline.iter(NS + "t"))
                    elif v is not None:
                        value = v.text or ""
                    else:
                        value = ""
                    value = value.strip()
                    if value:
                        cells[col_match.group()] = value
                if cells:
                    rows.append((int(row.get("r") or 0), cells))
            out[name] = rows
        return out
