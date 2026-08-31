"""
ira_reftables.py
================
Parses the reference tables that arrive with a DIFFERENT schema (and possibly a
different sheet/file name) than the main product-block sheets:

  * Dispensations         "<Category> Portfolio with Active or Expired ..."
                          Country | #                       -> {category: {country: count}}
  * CRA breaches          "Credit Risk Appetite Breaches - <Category>"
                          Country | <months Y/blank> | L12M -> {category: {country: count_last_12m}}
  * Sovereign rating      "Country Sovereign Rating & Outlook"
                          Country | LCY CRG | FCY CRG | Outlook | Approved Date
                                                          -> {country: {outlook, fcy_crg, lcy_crg}}
  * Property Price Index  "Property Price Index"   (dates in ROWS, countries in COLUMNS)
                                                          -> MonthTable keyed by country
  * Interest Rates        "Interest Rates"          (dates in ROWS, countries in COLUMNS)
                                                          -> MonthTable keyed by country

These tables are located by their TITLE text, so they work whether they sit in
their own sheet, are stacked several-to-a-sheet, or come in a separate file.
A leading blank column (data starting in column B) is handled.
"""

from __future__ import annotations
from typing import Dict, List, Any, Tuple, Optional
import pandas as pd
import re

try:
    from . import ira_engine as E
except ImportError:
    import ira_engine as E


CATEGORY_WORDS = {
    "secured": "Secured", "unsecured": "Unsecured",
    "sme": "SME Banking", "business": "SME Banking",
    "wealth": "Wealth Lending",
}
FOOTER_WORDS = ("yoy change", "last 3 yr", "last 3 yrs", "rate increase",
                "average", "3 yr", "l12m total")


def _blank(x):
    return E._blank(x)


def _trim_left(rows: List[List[Any]]) -> List[List[Any]]:
    """Drop leading columns that are blank in every row (handles the col-A gap)."""
    if not rows:
        return rows
    width = max(len(r) for r in rows)
    first = 0
    for c in range(width):
        if any(c < len(r) and not _blank(r[c]) for r in rows):
            first = c
            break
    return [[(r[c] if c < len(r) else None) for c in range(first, width)] for r in rows]


def _title_of(row: List[Any]) -> Optional[str]:
    """A title row has exactly one non-blank cell that is a non-date string."""
    nb = [(i, v) for i, v in enumerate(row) if not _blank(v)]
    if len(nb) == 1 and isinstance(nb[0][1], str) and not E._is_date_like(nb[0][1]):
        return nb[0][1].strip()
    return None

def _is_reference_title(value: Any) -> bool:
    """
    Return True only when the value is a recognized reference-table title.

    A country row can contain only one populated text cell, so a generic
    one-cell-text test is not sufficient to identify a table title.
    """
    if value is None:
        return False

    low = re.sub(
        r"\s+",
        " ",
        str(value).replace("\xa0", " ").strip().lower()
    )

    if not low:
        return False

    # Dispensations
    if (
        "active or expired" in low
        or "active/expired" in low
        or "dispensation" in low
    ):
        return True

    # CRA breaches
    if (
        "credit risk appetite breaches" in low
        or ("appetite" in low and "breach" in low)
    ):
        return True

    # Sovereign
    if (
        "sovereign" in low
        or (
            "outlook" in low
            and ("crg" in low or "rating" in low)
        )
    ):
        return True

    # Property Price Index
    if "property price index" in low or low == "ppi":
        return True

    # Interest rates
    if low == "interest rates" or "interest rate" in low:
        return True

    return False


def _category_from(title: str) -> Optional[str]:
    """Map a table title to a category by EXACT, case-insensitive word match.
    The title is lowercased and de-punctuated, then split into words; a category
    wins if any of its alias words is present.  'unsecured' is checked before
    'secured' so the shared substring never collides.  No fuzzy matching."""
    import re
    words = set(re.sub(r"[^a-z0-9 ]", " ", str(title).lower()).split())

    if words & {"unsecured", "unsec"}:
        return "Unsecured"
    if words & {"secured", "sec"}:
        return "Secured"
    if words & {"sme", "smb", "business"}:
        return "SME Banking"
    if words & {"wl", "wm", "wealth"}:
        return "Wealth Lending"
    if words & {"pvb", "pb", "private"}:      # PvB block is the Wealth Lending category
        return "Wealth Lending"
    return None


def _block(
    rows: List[List[Any]],
    start: int
) -> Tuple[List[List[Any]], int]:
    """
    Extract rows belonging to the current reference table.

    Stop at:
      1. a fully blank row, or
      2. the next recognized reference-table title.

    Important:
    A row containing only a country name is not a table title. This occurs
    frequently in CRA breach tables where that country has no breaches.
    """
    out: List[List[Any]] = []
    i = start

    while i < len(rows):
        row = rows[i]

        # A genuinely blank row terminates the current table.
        if all(_blank(cell) for cell in row):
            break

        candidate_title = _title_of(row)

        # Only a recognized reference-table title starts the next block.
        # Do not stop on one-cell country rows such as ["Bahrain", None, ...].
        if (
            out
            and candidate_title is not None
            and _is_reference_title(candidate_title)
        ):
            break

        out.append(row)
        i += 1

    return out, i


# --------------------------------------------------------------------------- #
#  per-schema parsers
# --------------------------------------------------------------------------- #
def _parse_dispensation(block: List[List[Any]]) -> Dict[str, float]:
    """Country | #  ->  {country: count}."""
    out = {}
    if not block:
        return out
    # header is first row (Country | #); data follows
    for r in block[1:]:
        if len(r) >= 2 and isinstance(r[0], str) and not _blank(r[0]):
            if r[0].strip().lower() in ("country", "total"):
                continue
            val = E._num(r[1])
            if val is not None:
                out[r[0].strip()] = int(val) if float(val).is_integer() else val
    return out


def _breach_marked(cell) -> Optional[float]:
    """A month cell counts as a breach if it is a 'Y'/'Yes' flag or a positive
    number.  Returns the breach weight (1 for a flag, the number itself if the
    cell is numeric > 0) or None when the cell is blank / not a breach."""
    if _blank(cell):
        return None
    if isinstance(cell, str):
        s = cell.strip().lower()
        if s in ("y", "yes", "true", "1"):
            return 1.0
        n = E._num(cell)
        return n if (n is not None and n > 0) else None
    n = E._num(cell)
    return n if (n is not None and n > 0) else None


def _is_month_col(v) -> bool:
    """A month column header in MMM-YY form (Mar-25, Apr-25, ...).  Falls back to
    the engine's general date detector so real Excel date cells are also caught.
    Never fixates on any particular month - any MMM-YY header qualifies."""
    if isinstance(v, str) and re.match(r"^\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
                                       r"[\s\-/]*\d{2,4}\s*$", v.strip(), re.I):
        return True
    return E._is_date_like(v)


def _parse_breaches(block: List[List[Any]]) -> Dict[str, int]:
    """Parse a 'Credit Risk Appetite Breaches - <Category>' block.

    The block is read generically - it is anchored on the **Country** column and
    the **month columns in MMM-YY format** (Mar-25, Apr-25, ... Mar-26), not on
    any specific month:

        Country | Mar-25 | Apr-25 | ... | Mar-26 | L12M

    A month cell holds 'Y' (a breach that month) or is blank; the final L12M
    column is the pre-computed total.  The count per country is the L12M total
    when that column is present and numeric (authoritative); otherwise it is the
    number of breach markers (Y / Yes / positive number) counted across every
    detected month column.  The 'Country' header row and the 'Total' row are
    skipped.
    """
    out: Dict[str, int] = {}
    if not block:
        return out

    # 1) find the header row: the one that has a 'Country' label AND month columns
    hdr_idx = None
    for i, row in enumerate(block):
        has_country = any(isinstance(c, str) and c.strip().lower() == "country" for c in row)
        n_months = sum(1 for v in row if _is_month_col(v))
        if has_country and n_months >= 2:
            hdr_idx = i
            break
    if hdr_idx is None:                                  # fall back: first row with >=2 months
        for i, row in enumerate(block):
            if sum(1 for v in row if _is_month_col(v)) >= 2:
                hdr_idx = i
                break
    if hdr_idx is None:
        return out
    header = block[hdr_idx]

    # 2) locate the Country column, the month columns, and any L12M total column
    month_cols = [j for j, v in enumerate(header) if _is_month_col(v)]
    country_col = next((j for j, v in enumerate(header)
                        if isinstance(v, str) and v.strip().lower() == "country"), None)
    if country_col is None:                              # first non-month column
        country_col = next((j for j in range(len(header)) if j not in month_cols), 0)

    def _norm(v):
        return re.sub(r"[^a-z0-9]", "", str(v).lower()) if v is not None else ""
    l12_col = next((j for j, v in enumerate(header) if _norm(v).startswith("l12")), None)

    # 3) one row per country
    for r in block[hdr_idx + 1:]:
        if country_col >= len(r) or _blank(r[country_col]):
            continue
        country = str(r[country_col]).strip()
        if country.lower() in ("country", "total", "grand total"):
            continue
        counted = 0.0
        for j in month_cols:
            if j < len(r):
                w = _breach_marked(r[j])
                if w is not None:
                    counted += w
        cnt = counted
        if l12_col is not None and l12_col < len(r):
            lv = E._num(r[l12_col])
            if lv is not None:
                cnt = lv
        out[country] = int(cnt) if float(cnt).is_integer() else cnt
    return out


def _parse_sovereign(block: List[List[Any]]) -> Dict[str, Dict[str, str]]:
    """Country | LCY CRG | FCY CRG | Outlook | ...  ->  {country: {...}}."""
    out = {}
    if not block:
        return out
    header = [str(c).strip().lower() if c is not None else "" for c in block[0]]

    def col(*names):
        for nm in names:
            for i, h in enumerate(header):
                if nm in h:
                    return i
        return None

    ci = 0
    fcy = col("fcy crg", "fcy rating", "fcy", "foreign currency")
    lcy = col("lcy crg", "lcy rating", "lcy", "local currency")
    out_c = col("outlook")
    for r in block[1:]:
        if not r or _blank(r[ci]) or not isinstance(r[ci], str):
            continue
        country = r[ci].strip()
        if country.lower() in ("country", "total"):
            continue
        rec = {}
        if fcy is not None and fcy < len(r):
            rec["fcy_crg"] = (str(r[fcy]).strip() if not _blank(r[fcy]) else None)
        if lcy is not None and lcy < len(r):
            rec["lcy_crg"] = (str(r[lcy]).strip() if not _blank(r[lcy]) else None)
        if out_c is not None and out_c < len(r):
            rec["outlook"] = (str(r[out_c]).strip() if not _blank(r[out_c]) else None)
        out[country] = rec
    return out


def _parse_matrix(block: List[List[Any]]) -> "E.MonthTable":
    """Dates in the first column (rows), entities across the header (columns).
    Returns a MonthTable keyed by entity (country)."""
    if not block:
        return E.MonthTable([], {}, {}, {})
    header = block[0]
    # entity columns = every non-blank header cell after the first column
    ent_cols = [(i, str(header[i]).strip()) for i in range(1, len(header))
                if not _blank(header[i]) and not E._is_date_like(header[i])]
    months = []
    data: Dict[str, Dict[str, float]] = {name: {} for _i, name in ent_cols}
    for r in block[1:]:
        if not r or _blank(r[0]):
            continue
        # stop at footer rows (YoY / averages / rate increase)
        if isinstance(r[0], str) and any(w in r[0].lower() for w in FOOTER_WORDS):
            continue
        if not E._is_date_like(r[0]):
            continue
        mlabel = str(r[0])
        months.append(mlabel)
        for i, name in ent_cols:
            data[name][mlabel] = E._num(r[i]) if i < len(r) else None
    return E.MonthTable(months, {}, data, {})


# --------------------------------------------------------------------------- #
#  top-level: scan all sheets, extract every reference table by title
# --------------------------------------------------------------------------- #
def parse_reference_tables(sheets: Dict[str, List[List[Any]]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"dispensations": {}, "cra_breaches": {},
                           "sovereign": {}, "ppi": None, "interest_rates": None}
    for name, raw in sheets.items():
        rows = _trim_left(raw)
        i = 0
        while i < len(rows):
            title = _title_of(rows[i])
            if not title:
                i += 1
                continue
            low = title.lower()
            block, nxt = _block(rows, i + 1)

            if "active or expired" in low or "active/expired" in low or \
               ("dispensation" in low):
                cat = _category_from(low)
                if cat:
                    out["dispensations"][cat] = _parse_dispensation(block)
            elif "credit risk appetite breaches" in low or \
                    ("appetite" in low and "breach" in low):
                cat = _category_from(low)
                if cat:
                    out["cra_breaches"][cat] = _parse_breaches(block)
            elif "sovereign" in low or ("outlook" in low and
                                        ("crg" in low or "rating" in low)):
                out["sovereign"] = _parse_sovereign(block)
            elif "property price index" in low or low.strip() == "ppi":
                out["ppi"] = _parse_matrix(block)
            elif low.strip() == "interest rates" or "interest rate" in low:
                out["interest_rates"] = _parse_matrix(block)
            i = max(nxt, i + 1)
    # drop empties
    return {k: v for k, v in out.items()
            if v not in (None, {}, [])}
