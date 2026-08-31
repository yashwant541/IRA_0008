"""
ira_sovereign.py
================
A DEDICATED, self-contained pipeline for the

    "Country Sovereign Rating & Outlook"

table, which drives two metrics in EVERY category:

    Country Risk - Outlook    <- the "Outlook" column      (Positive/Stable/Negative)
    Country Risk - Grading    <- the "FCY CRG" column       (e.g. 1B, 5B, 7B, 9B)

Shape:
    Country Sovereign Rating & Outlook
    Country | LCY CRG | FCY CRG | Outlook | Approved Date
    Bangladesh | 1B | 1B | Negative
    China      | 7B | 7B | Positive
    ...

This module owns detect -> read -> process -> intermediate -> final for these
two values, independently of the generic reference-table code, and reuses the
country-matching rules from ira_engine (same as the dispensations pipeline).

Public API
----------
parse(sheets)                     -> ({country: {outlook, grading, lcy}}, report)
detect(sheets)                    -> report list (what was found)
outlook_for(data, country)        -> (value, reason)     # -> final output
grading_for(data, country)        -> (value, reason)     # -> final output  (FCY CRG)
rating_for_outlook(value)         -> risk rating          (single source of truth)
rating_for_grading(value)         -> risk rating          (single source of truth)
intermediate_frames(data)         -> {sheet_title: DataFrame}
"""

from __future__ import annotations
from typing import Dict, List, Any, Optional, Tuple
import re
import pandas as pd

try:
    from . import ira_engine as E
except ImportError:
    import ira_engine as E


# a row is a sovereign TITLE if its text matches one of these
_TITLE_MARKERS = ("sovereign",)                      # "Country Sovereign Rating & Outlook"
_TITLE_ALT = lambda low: ("outlook" in low and ("crg" in low or "rating" in low))


# ============================ RATING RULES ================================== #
# The mapping from a raw CRG grade / outlook to a risk rating.  This is the ONE
# place these rules live - edit here to change them everywhere (final output,
# intermediate, and the config delegates to these).
#
# Outlook -> rating
OUTLOOK_RATING = {
    "positive": "Very Low",
    "stable":   "Low",
    "negative": "Very High",
}
# FCY CRG grade -> rating.  Grades look like <number><letter> (1A..13).  We rate
# by the leading NUMBER band (A/B/C suffix is a sub-notch within the number).
#   1-2  Very Low | 3-4 Low | 5-7 Medium | 8-9 High | 10+ Very High
GRADE_BAND = [
    (2,  "Very Low"),
    (4,  "Low"),
    (7,  "Medium"),
    (9,  "High"),
    (99, "Very High"),
]


def rating_for_outlook(value) -> str:
    if value is None or str(value).strip() == "":
        return "Not Available"
    return OUTLOOK_RATING.get(str(value).strip().lower(), "Not Available")


def rating_for_grading(value) -> str:
    if value is None or str(value).strip() == "":
        return "Not Available"
    g = str(value).strip().upper().replace(" ", "")
    # exact grade lists supplied by the business (2d / 2b)
    VH = {"11A", "11B", "11C", "12A", "12B", "12C", "13"}
    H = {"8B", "9A", "9B", "10A"}
    M = {"5B", "6A", "6B", "7A", "7B", "8A"}
    VL = {"1A", "1B", "2A"}
    if g in VH:
        return "Very High"
    if g in H:
        return "High"
    if g in M:
        return "Medium"
    if g in VL:
        return "Very Low"
    return "Low"
# =========================================================================== #


def _blank(x) -> bool:
    return E._blank(x)


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", str(s).lower()).strip()


def _trim_left(rows: List[List[Any]]) -> List[List[Any]]:
    if not rows:
        return rows
    width = max((len(r) for r in rows), default=0)
    first = 0
    for c in range(width):
        if any(c < len(r) and not _blank(r[c]) for r in rows):
            first = c
            break
    return [[(r[c] if c < len(r) else None) for c in range(first, width)] for r in rows]


def _title_in_row(row: List[Any]) -> Optional[str]:
    for v in row:
        if isinstance(v, str) and v.strip():
            low = v.lower()
            if any(m in low for m in _TITLE_MARKERS) or _TITLE_ALT(low):
                return v.strip()
    return None


def _is_header_row(row: List[Any]) -> bool:
    joined = " ".join(_norm(c) for c in row if not _blank(c))
    return "country" in joined and ("crg" in joined or "outlook" in joined)


def _locate_columns(header: List[Any]) -> Dict[str, Optional[int]]:
    norm = [_norm(h) if not _blank(h) else "" for h in header]

    def find(pred):
        for i, h in enumerate(norm):
            if pred(h):
                return i
        return None

    country = find(lambda h: h == "country" or h.startswith("country"))
    fcy = find(lambda h: "fcy" in h)                 # FCY CRG (grading)
    lcy = find(lambda h: "lcy" in h)                 # LCY CRG
    outlook = find(lambda h: "outlook" in h)
    if country is None:
        country = 0
    return {"country": country, "fcy": fcy, "lcy": lcy, "outlook": outlook}


def _collect_block(rows, start):
    out = []
    i = start
    while i < len(rows):
        r = rows[i]
        if all(_blank(c) for c in r):
            break
        if i != start and _title_in_row(r):
            break
        out.append(r)
        i += 1
    return out, i


def _parse_block(block: List[List[Any]]) -> Dict[str, Dict[str, Any]]:
    """Parse a sovereign block -> {country: {outlook, grading, lcy}}."""
    out: Dict[str, Dict[str, Any]] = {}
    if not block:
        return out
    # find the header row (Country | LCY CRG | FCY CRG | Outlook ...)
    hdr_idx = 0
    for j, r in enumerate(block):
        if _is_header_row(r):
            hdr_idx = j
            break
    cols = _locate_columns(block[hdr_idx])
    ci, fcy, lcy, oc = cols["country"], cols["fcy"], cols["lcy"], cols["outlook"]
    for r in block[hdr_idx + 1:]:
        if ci >= len(r) or _blank(r[ci]) or not isinstance(r[ci], str):
            continue
        country = r[ci].strip()
        if country.lower() in ("country", "total", "grand total"):
            continue
        def cell(idx):
            return (str(r[idx]).strip() if idx is not None and idx < len(r)
                    and not _blank(r[idx]) else None)
        out[country] = {
            "outlook": cell(oc),
            "grading": cell(fcy),      # grading = FCY CRG
            "lcy": cell(lcy),
        }
    return out


# --------------------------------------------------------------------------- #
#  detect + parse
# --------------------------------------------------------------------------- #
def parse(sheets: Dict[str, List[List[Any]]]) -> Tuple[Dict[str, Dict[str, Any]], List[dict]]:
    data: Dict[str, Dict[str, Any]] = {}
    report: List[dict] = []
    for sheet_name, raw in (sheets or {}).items():
        rows = _trim_left(raw)
        i = 0
        while i < len(rows):
            title = _title_in_row(rows[i])
            if title:
                block, nxt = _collect_block(rows, i + 1)
                parsed = _parse_block(block)
                if parsed:
                    data.update(parsed)       # merge countries
                    report.append({"sheet": sheet_name, "title": title,
                                   "n_countries": len(parsed),
                                   "countries": list(parsed.keys()), "detected": True,
                                   "note": "OK"})
                else:
                    report.append({"sheet": sheet_name, "title": title,
                                   "n_countries": 0, "detected": False,
                                   "note": "no rows parsed"})
                i = max(nxt, i + 1)
            else:
                i += 1
    return data, report


def detect(sheets) -> List[dict]:
    _d, report = parse(sheets)
    return report


# --------------------------------------------------------------------------- #
#  final-output pickup (case/space/alias-insensitive country match)
# --------------------------------------------------------------------------- #
def _lookup(data, country):
    if country in data:
        return data[country]
    ck = E.country_key(country)
    for k, v in data.items():
        if E.country_key(k) == ck:
            return v
    return None


def outlook_for(data, country) -> Tuple[Optional[str], str]:
    rec = _lookup(data or {}, country)
    if rec is None:
        avail = ", ".join(sorted(str(k) for k in (data or {}).keys()))
        return None, f"{country} not in Sovereign table (have: {avail})"
    v = rec.get("outlook")
    return (v, "") if v is not None else (None, f"no Outlook value for {country}")


def grading_for(data, country) -> Tuple[Optional[str], str]:
    rec = _lookup(data or {}, country)
    if rec is None:
        avail = ", ".join(sorted(str(k) for k in (data or {}).keys()))
        return None, f"{country} not in Sovereign table (have: {avail})"
    v = rec.get("grading")
    return (v, "") if v is not None else (None, f"no FCY CRG grade for {country}")


# --------------------------------------------------------------------------- #
#  intermediate output
# --------------------------------------------------------------------------- #
def intermediate_frames(data: Dict[str, Dict[str, Any]]) -> Dict[str, pd.DataFrame]:
    if not data:
        return {"Sovereign Outlook & Grading":
                pd.DataFrame([{"Country": "(no table found)", "Outlook": None,
                               "Grading (FCY CRG)": None}])}
    rows = []
    for country, rec in data.items():
        rows.append({"Country": country,
                     "Outlook": rec.get("outlook"),
                     "Outlook Rating": rating_for_outlook(rec.get("outlook")),
                     "Grading (FCY CRG)": rec.get("grading"),
                     "Grading Rating": rating_for_grading(rec.get("grading")),
                     "LCY CRG": rec.get("lcy")})
    return {"Sovereign Outlook & Grading": pd.DataFrame(rows)}
