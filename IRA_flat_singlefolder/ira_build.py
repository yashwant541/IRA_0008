"""
ira_build.py
============
Turns a dict of parsed input tables into the four output tables that mirror the
"Formulas to be built" workbook (one per product), plus the final
"Calculated Inherent Credit Risk Assessment" row per country.

Output columns match the template exactly:
    Country | Label | Value | Risk Rating | Risk Number | What to do in Value Column
The 6th column is filled from INSTRUCTIONS below (optional, purely documentary).
"""

from __future__ import annotations
from typing import Dict, List, Any
import math
import pandas as pd

try:                        # inside the IRA package (Dataiku library)
    from . import ira_engine as E
    from . import ira_config as C
    from . import ira_intermediate as I
except ImportError:         # flat import (local / standalone)
    import ira_engine as E
    import ira_config as C
    import ira_intermediate as I


OUT_COLUMNS = ["Country", "Label", "Value", "Risk Rating",
               "Risk Number", "What to do in Value Column"]


def _fmt_value(v: Any) -> Any:
    """Present a computed value nicely (fractions as %, keep text/ints)."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, (int,)) or (isinstance(v, float) and float(v).is_integer()
                                 and abs(v) >= 1):
        return int(v)
    # treat as a rate
    return round(float(v), 6)


def compute_country_product(tables: Dict[str, Any], country: str,
                            product_out: str) -> Dict[str, Any]:
    """
    Compute every metric for one country + one output product, reading values
    from the intermediate layer.  Returns
        {metric_id: dict(label, value, rating, number, group, weight_key, reason)}
    """
    metric_defs = C.METRICS[product_out]()

    # pass 1: pull value + reason from each extractor (keyed by OUTPUT product)
    ctx: Dict[str, Any] = {}
    reasons: Dict[str, str] = {}
    displays: Dict[str, Any] = {}
    for m in metric_defs:
        try:
            rec = m["value"](tables, country, product_out)
            ctx[m["id"]] = rec.get("value")
            reasons[m["id"]] = rec.get("reason", "")
            # display value = explicit 'display' field if present, else the value
            displays[m["id"]] = rec.get("display", rec.get("value")) \
                if "display" in rec else rec.get("value")
        except Exception as ex:
            ctx[m["id"]] = None
            reasons[m["id"]] = f"extractor error: {ex}"
            displays[m["id"]] = None

    # pass 2: ratings + numbers
    result: Dict[str, Any] = {}
    for m in metric_defs:
        val = ctx[m["id"]]
        try:
            rating = m["rating"](val, ctx)
        except Exception:
            rating = "Not Available"
        number = E.RISK_NUMBER.get(rating)  # None for Not Available
        result[m["id"]] = dict(label=m["label"], value=val,
                               display=displays[m["id"]], rating=rating,
                               number=number, group=m["group"],
                               weight_key=m["weight_key"],
                               int_key=getattr(m["value"], "int_key", ""),
                               reason=reasons[m["id"]])
    return result


def final_assessment(product_out: str, per_metric: Dict[str, Any],
                     metric_defs: List[dict]) -> Dict[str, Any]:
    """Weighted aggregation per the business spec:
       score = SUM over groups of  weight * max(risk numbers in the group),
       where groups are defined by LABEL POSITION in AGG_GROUPS and weight is
       1/6 (or 1/#groups if NORMALISE_WEIGHTS)."""
    # risk numbers in label order (index 0 == label 1)
    numbers = [per_metric[m["id"]]["number"] for m in metric_defs]

    groups = C.AGG_GROUPS.get(product_out, [])
    weight = (1.0 / len(groups)) if (C.NORMALISE_WEIGHTS and groups) else C.W6

    score = 0.0
    contributions = []
    for positions in groups:
        vals = [numbers[p - 1] for p in positions
                if 1 <= p <= len(numbers) and numbers[p - 1] is not None]
        if not vals:
            contributions.append(None)
            continue
        agg = max(vals)
        score += weight * agg
        contributions.append(agg)

    if score >= 4.5:
        rating = "Very High"
    elif score >= 3.5:
        rating = "High"
    elif score >= 2.5:
        rating = "Medium"
    elif score >= 1.5:
        rating = "Low"
    else:
        rating = "Very Low"
    return dict(score=round(score, 4), rating=rating, contributions=contributions)


def _fmt_output(int_key: str, value) -> Any:
    """Format the output Value per metric type (points 7,10,11,12):
       rate/proportion -> value*100 with a % sign; count -> integer; text -> as-is."""
    if value is None or value == "":
        return ""
    if int_key in I.PCT_KEYS:
        try:
            return f"{float(value) * 100:.2f}%"
        except Exception:
            return value
    if int_key in I.COUNT_KEYS:
        try:
            return int(round(float(value)))
        except Exception:
            return value
    return value          # text (outlook / grading) or anything else


def build_product_frame(tables: Dict[str, Any], product_out: str,
                        countries: List[str]) -> pd.DataFrame:
    """Build the full output DataFrame for one product across all countries."""
    metric_defs = C.METRICS[product_out]()
    rows: List[Dict[str, Any]] = []

    for country in countries:
        per_metric = compute_country_product(tables, country, product_out)
        for m in metric_defs:
            r = per_metric[m["id"]]
            note = ("Not Available - " + (r["reason"] or
                    "value could not be computed (missing input or dependent metric)")
                    ) if r["rating"] == "Not Available" else ""
            rows.append({
                "Country": country,
                "Label": r["label"],
                "Value": _fmt_output(r["int_key"], r["display"]),
                "Risk Rating": r["rating"],
                "Risk Number": r["number"] if r["number"] is not None else "",
                "What to do in Value Column": note,
            })
        fa = final_assessment(product_out, per_metric, metric_defs)
        rows.append({
            "Country": country,
            "Label": C.FINAL_LABEL,
            "Value": fa["score"],          # the score stays a number, not a %
            "Risk Rating": fa["rating"],
            "Risk Number": "",
            "What to do in Value Column": "",
        })
    return pd.DataFrame(rows, columns=OUT_COLUMNS)


def _ensure_intermediates(tables: Dict[str, Any], per_cat) -> None:
    if "intermediates" not in tables:
        tables["intermediates"] = I.build(tables, per_cat)


def countries_for_category(tables: Dict[str, Any], category: str) -> List[str]:
    """Countries that actually have data for this category in the core
    (ENR / DPD) product-block tables - the ones that drive the metrics."""
    canon = E.CATEGORY_TO_CANON[category]
    found: List[str] = []
    for key in ("ENR", "90+%" if category == "Secured" else "30+%",
                "90+$" if category == "Secured" else "30+$"):
        t = tables.get(key)
        if not t:
            continue
        for (c, p) in t.product_data:
            if p == canon and c not in found and not str(c).lower().startswith("total"):
                found.append(c)
    return found


def resolve_countries(tables, countries=None, countries_per_category=None):
    """Decide the country list for each category.
    If countries_per_category is provided it is AUTHORITATIVE - only those
    countries run, per category (missing category -> empty, nothing runs).
    Otherwise fall back to a single `countries` list, or auto-detect."""
    out = {}
    for cat in E.CATEGORIES:
        if countries_per_category is not None:
            out[cat] = list(countries_per_category.get(cat, []))   # strict
        elif countries:
            out[cat] = list(countries)
        else:
            out[cat] = countries_for_category(tables, cat)
    return out


def build_all(tables: Dict[str, Any], countries: List[str] = None,
              countries_per_category: Dict[str, List[str]] = None
              ) -> Dict[str, pd.DataFrame]:
    """Build all four category frames.  Returns {output_sheet_name: DataFrame}.
    Countries can be set globally (`countries`) or per category
    (`countries_per_category={'Secured':[...], ...}`); otherwise auto-detected."""
    per_cat = resolve_countries(tables, countries, countries_per_category)
    wl = per_cat.get("Wealth Lending", [])
    cpc = countries_per_category or {}
    # PvB and Retail run over their OWN configured country sets when the config
    # provides them; otherwise fall back (PvB -> Wealth Lending countries that
    # actually have PvB data; Retail -> all Wealth Lending countries).
    pb_cfg = cpc.get("Wealth Lending - Private Banking")
    pvb = list(pb_cfg) if pb_cfg else [c for c in wl if I._pvb_available(tables, c)]
    retail_cfg = cpc.get("Wealth Lending - Retail Banking")
    retail = list(retail_cfg) if retail_cfg else wl
    # Build intermediates over the UNION of the Wealth family so PvB/Retail-only
    # countries (e.g. UK) get their Wealth-family AND sovereign intermediates
    # built - each frame below still uses its own scope.
    per_cat_inter = dict(per_cat)
    per_cat_inter["Wealth Lending"] = list(dict.fromkeys(list(wl) + pvb + retail))
    _ensure_intermediates(tables, per_cat_inter)
    frames = {
        "IRA - Secured":        build_product_frame(tables, "Secured", per_cat["Secured"]),
        "IRA - Unsecured":      build_product_frame(tables, "Unsecured", per_cat["Unsecured"]),
        "IRA - SME Banking":    build_product_frame(tables, "SME Banking", per_cat["SME Banking"]),
        "IRA - Wealth Lending": build_product_frame(tables, "Wealth Lending", wl),
        "IRA - Wealth Lending - Retail Banking":
            build_product_frame(tables, "Wealth Lending - Retail Banking", retail),
        "IRA - Wealth Lending - PvB":
            build_product_frame(tables, "Wealth Lending - PvB", pvb),
    }
    try:
        from . import ira_group as G
    except ImportError:
        import ira_group as G
    frames = G.append_all(frames, tables)
    return _stamp_period(frames, tables)


def _stamp_period(frames: Dict[str, pd.DataFrame], tables) -> Dict[str, pd.DataFrame]:
    """Insert a 'Period' column (latest MI month header, e.g. 'Mar-26') as the
    first column of every frame."""
    period = E.period_label(tables)
    for name, df in frames.items():
        if df is not None and not df.empty and "Period" not in df.columns:
            df.insert(0, "Period", period)
    return frames


def build_intermediate_frames(tables: Dict[str, Any],
                              per_cat) -> Dict[str, pd.DataFrame]:
    """The calculated intermediate tables, as tidy DataFrames for output."""
    _ensure_intermediates(tables, per_cat)
    frames = I.to_frames(tables["intermediates"])
    # dedicated 1f dispensation source tables (detected/read/processed), shown
    # explicitly so you can see the raw values that feed the final output.
    try:
        from . import ira_dispensations as DSP
    except ImportError:
        import ira_dispensations as DSP
    frames.update(DSP.intermediate_frames(tables.get("dispensations") or {}))
    try:
        from . import ira_sovereign as SOV
    except ImportError:
        import ira_sovereign as SOV
    frames.update(SOV.intermediate_frames(tables.get("sovereign") or {}))
    return _stamp_period(frames, tables)


def build_mapping() -> pd.DataFrame:
    """Category -> label -> source table(s) -> calculation -> intermediate table,
    plus the aggregation group (by label position) used in the final score."""
    rows = []
    for category, factory in C.METRICS.items():
        defs = factory()
        # position (1-based) -> group index label
        pos_group = {}
        for gi, positions in enumerate(C.AGG_GROUPS.get(category, []), start=1):
            for p in positions:
                pos_group[p] = (gi, positions)
        for idx, m in enumerate(defs, start=1):
            int_key = getattr(m["value"], "int_key", "")
            src, calc = I.INT_SOURCE.get(int_key, ("", ""))
            gi, positions = pos_group.get(idx, ("", []))
            agg = ("Label %d" % idx if len(positions) == 1
                   else "Max(Labels %s)" % ",".join(map(str, positions))) if positions else ""
            rows.append({
                "Category": category,
                "Label #": idx,
                "Metric": m["id"],
                "Label": m["label"],
                "Source table(s)": src,
                "Calculation": calc,
                "Intermediate table": I.INT_TITLES.get(int_key, int_key),
                "Final-score group": f"G{gi}: {agg}" if gi else "",
            })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
#  Risk Rating Formula documentation (download only - never shown in the app)
# --------------------------------------------------------------------------- #
import re as _re

_GRADING = ('=IF(OR(B="11A",B="11B",B="11C",B="12A",B="12B",B="12C",B="13"),"Very High",'
            'IF(OR(B="8B",B="9A",B="9B",B="10A"),"High",'
            'IF(OR(B="5B",B="6A",B="6B",B="7A",B="7B",B="8A"),"Medium",'
            'IF(OR(B="1A",B="1B",B="2A"),"Very Low","Low"))))')
_OUTLOOK = '=IF(B="Positive","Very Low",IF(B="Stable","Low","Very High"))'
_DISP = '=IF(B>3,"Very High",IF(B=3,"High",IF(B=2,"Medium",IF(B=1,"Low","Very Low"))))'
_BREACH = '=IF(B>1,"Very High",IF(B=1,"High","Very Low"))'
_CALC = ('=IF(Score>=4.5,"Very High",IF(Score>=3.5,"High",'
         'IF(Score>=2.5,"Medium",IF(Score>=1.5,"Low","Very Low"))))')
_BII = "No separate formula: the Prior-Month value is evaluated inside the 1bi formula."

# per (product, canonical label id) -> the exact Risk Rating formula
_RATING_FORMULAS = {
    "Secured": {
        "1a": '=IF(B>10%,"Very High",IF(B>5%,"High",IF(B>3%,"Medium",IF(B>1%,"Low","Very Low"))))',
        "1bi": '=IF(AND(cur>0.05%,prior>0.05%),"Very High",IF(AND(cur>0.03%,prior>0.03%),"High",IF(AND(cur>0.02%,prior>0.02%),"Medium",IF(AND(cur>0.01%,prior>0.01%),"Low","Very Low"))))',
        "1bii": _BII,
        "1c": '=IF(B>0.1%,"Very High",IF(B>0.05%,"High",IF(B>0.03%,"Medium",IF(B>0.01%,"Low","Very Low"))))',
        "1d": '=IF(B>25%,"Very High",IF(B>15%,"High",IF(B>5%,"Medium",IF(B>2.5%,"Low","Very Low"))))',
        "1e": '=IF(B="","Not Available",IF(B>15%,"Very High",IF(B>10%,"High",IF(B>7.5%,"Medium",IF(B>5%,"Low","Very Low")))))',
        "1f": _DISP,
        "1g": '=IF(B>10%,"Very High",IF(B>5%,"High",IF(B>2.5%,"Medium",IF(B>1%,"Low","Very Low"))))',
        "1h": _BREACH,
        "2a": '=IF(B>3%,"Very High",IF(B>2%,"High",IF(B>1%,"Medium",IF(B>0%,"Low","Very Low"))))',
        "2b": '=IF(B>5%,"Very Low",IF(B>0,"Low",IF(B>-5%,"Medium",IF(B>-15%,"High","Very High"))))',
        "2c": _OUTLOOK, "2d": _GRADING,
    },
    "Unsecured": {
        "1a": '=IF(B>15%,"Very High",IF(B>5%,"High",IF(B>3%,"Medium",IF(B>1%,"Low","Very Low"))))',
        "1bi": '=IF(AND(cur>0.25%,prior>0.25%),"Very High",IF(AND(cur>0.06%,prior>0.06%),"High",IF(AND(cur>0.04%,prior>0.04%),"Medium",IF(AND(cur>0.01%,prior>0.01%),"Low","Very Low"))))',
        "1bii": _BII,
        "1c": '=IF(B>0.25%,"Very High",IF(B>0.2%,"High",IF(B>0.15%,"Medium",IF(B>0.1%,"Low","Very Low"))))',
        "1d": '=IF(B>10%,"Very High",IF(B>7.5%,"High",IF(B>5%,"Medium",IF(B>2.5%,"Low","Very Low"))))',
        "1e": '=IF(B="","Not Available",IF(B>5%,"Very High",IF(B>1%,"High",IF(B>0.5%,"Medium",IF(B>0.25%,"Low","Very Low")))))',
        "1f": _DISP,
        "1g": '=IF(B>12.5%,"Very High",IF(B>10%,"High",IF(B>7.5%,"Medium",IF(B>5%,"Low","Very Low"))))',
        "1h": _BREACH, "2a": _OUTLOOK, "2b": _GRADING,
    },
    "SME Banking": {
        "1a": '=IF(B>15%,"Very High",IF(B>5%,"High",IF(B>3%,"Medium",IF(B>1%,"Low","Very Low"))))',
        "1bi": '=IF(AND(cur>0.25%,prior>0.25%),"Very High",IF(AND(cur>0.06%,prior>0.06%),"High",IF(AND(cur>0.04%,prior>0.04%),"Medium",IF(AND(cur>0.01%,prior>0.01%),"Low","Very Low"))))',
        "1bii": _BII,
        "1c": '=IF(B>0.25%,"Very High",IF(B>0.2%,"High",IF(B>0.15%,"Medium",IF(B>0.1%,"Low","Very Low"))))',
        "1d": '=IF(B>20%,"Very High",IF(B>15%,"High",IF(B>10%,"Medium",IF(B>5%,"Low","Very Low"))))',
        "1e": '=IF(B>10%,"Very High",IF(B>7.5%,"High",IF(B>5%,"Medium",IF(B>2.5%,"Low","Very Low"))))',
        "1f": '=IF(B>12.5%,"Very High",IF(B>10%,"High",IF(B>7.5%,"Medium",IF(B>5%,"Low","Very Low"))))',
        "1g": '=IF(B="","Not Available",IF(B>7.5%,"Very High",IF(B>5%,"High",IF(B>3%,"Medium",IF(B>1%,"Low","Very Low")))))',
        "1h": _DISP, "1i": _BREACH, "2a": _OUTLOOK, "2b": _GRADING,
    },
    "Wealth Lending": {
        "1a": '=IF(B>15%,"Very High",IF(B>5%,"High",IF(B>3%,"Medium",IF(B>1%,"Low","Very Low"))))',
        "1bi": '=IF(AND(cur>0.25%,prior>0.25%),"Very High",IF(AND(cur>0%,prior>0%),"High","Very Low"))',
        "1bii": _BII,
        "1c": '=IF(B>0.25%,"Very High",IF(B>0%,"High","Very Low"))',
        "1d": '=IF(B>1%,"Very High",IF(B>0.75%,"High",IF(B>0.5%,"Medium",IF(B>0.25%,"Low","Very Low"))))',
        "1e": '=IF(B>3.5%,"Very High",IF(B>2.5%,"High",IF(B>1.5%,"Medium",IF(B>0.5%,"Low","Very Low"))))',
        "1f": '=IF(B>25%,"Very High",IF(B>10%,"High",IF(B>5%,"Medium",IF(B>3%,"Low","Very Low"))))',
        "1g": '=IF(B>3.5%,"Very High",IF(B>2.5%,"High",IF(B>1.5%,"Medium",IF(B>0.5%,"Low","Very Low"))))',
        "1h": _DISP, "1i": _BREACH, "2a": _OUTLOOK, "2b": _GRADING,
    },
}
# Retail & PvB share the Wealth Lending ladders
_RATING_FORMULAS["Wealth Lending - Retail Banking"] = _RATING_FORMULAS["Wealth Lending"]
_RATING_FORMULAS["Wealth Lending - PvB"] = _RATING_FORMULAS["Wealth Lending"]

_FORMULA_COL = "Risk Rating Formula"


def _canon_label(label) -> str:
    m = _re.match(r"^\s*([0-9]+[a-z]*)", str(label))
    return m.group(1) if m else ""


def rating_formula_for(product: str, label) -> str:
    """The Risk Rating formula documented for one (product, label)."""
    if str(label).strip().lower().startswith("calculated"):
        return _CALC
    table = _RATING_FORMULAS.get(product, {})
    return table.get(_canon_label(label), "")


def attach_rating_formula(df: "pd.DataFrame", product: str) -> "pd.DataFrame":
    """Return a copy of an output frame with a 'Risk Rating Formula' column
    appended (documentation of the ladder used for each label). Download only -
    this is never added to the frames the app renders."""
    if df is None or df.empty or _FORMULA_COL in df.columns:
        return df
    out = df.copy()
    lab_col = "Label" if "Label" in out.columns else out.columns[min(1, len(out.columns) - 1)]
    out[_FORMULA_COL] = out[lab_col].map(lambda lb: rating_formula_for(product, lb))
    return out
