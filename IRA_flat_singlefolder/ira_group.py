"""
ira_group.py  -  GROUP (portfolio) roll-up rows for each product
================================================================
Appends, to every product output frame, a block of GROUP rows (Country =
"GROUP"): one per label plus a GROUP "Calculated Inherent Credit Risk
Assessment:" row.

Each product's labels are split into two calculation kinds (per the GROUP spec):

  TABLE OPERATIONS  (ratio-of-totals over ALL countries in the raw tables):
    * 1bi / 1bii / 1c deterioration  - Secured 90+$, others 30+$; every Wealth
      product (incl. PvB) uses the 30+$ 'Wealth Banking' line.
        DPD%(m) = SUM(DPD$ of the product, all countries, m) / SUM(category ENR,
                  all countries, m)
        1bi  = DPD%(current)      - DPD%(current - 1 quarter)
        1bii = DPD%(prior month)  - DPD%(prior month - 1 quarter)
        1c   = DPD%(current)      - DPD%(current - 1 year)
    * policy rate  - SUM(L2+L3 over 12m, all) / SUM(new approved over 12m, all)
    * LTV>80 (Secured 1g)      - the table's own 'Total' row / 100
    * volatile (Unsecured 1g)  - the CCPL table's portfolio 'Total'/'Global' value
    * EA / AWC (SME 1e/1f, Wealth Lending 1d/1e) - SUM($ current month, all
      countries) / SUM(category ENR, current month)
  Each is then rated with the SAME ladder the per-country label uses.

  ENR COUNTRY-% WEIGHT  (every other label):
    weight(country) = category ENR of that country (latest month) / TOTAL category
    ENR over ALL countries (latest month) - NOT renormalised.  The GROUP number is
    SUM over the product's config countries of (risk number x weight), rounded to
    the nearest 1..5.

Categories: Secured, Unsecured, SME Banking, Wealth Lending.  The two extra Wealth
products (Retail, PvB) share the Wealth Lending ENR (Wealth Banking + PvB).

GROUP inherent reuses the theme groups (AGG_GROUPS): the worst GROUP risk number
per theme x the theme weight, summed; 1bii is excluded (1bi drives the pair).
"""
from __future__ import annotations
import math, re
from typing import Dict, List, Any, Optional

import pandas as pd

try:
    from . import ira_config as C
    from . import ira_engine as E
except ImportError:
    import ira_config as C
    import ira_engine as E


GROUP_COUNTRY = "GROUP"
INHERENT_EXCLUDE_CANON = {"1bii"}
_EXCLUDE_ROWS = {"total", "group", "grand total", "others", "other"}
_NUM_TO_RATING = {1: "Very Low", 2: "Low", 3: "Medium", 4: "High", 5: "Very High"}
EA_TITLE = "ME EA (PP & NPP) in $mn"
AWC_TITLE = "ME AWC in $mn"

# category ENR line(s): the exposure that drives weights AND the deterioration /
# EA / AWC denominators.  All three Wealth products share Wealth Banking + PvB.
CATEGORY_ENR_LINES = {
    "Secured": ["Consumer Secured"],
    "Unsecured": ["Consumer Unsecured"],
    "SME Banking": ["SME Banking"],
    "Wealth Lending": ["Wealth Banking", "PvB"],
    "Wealth Lending - Retail Banking": ["Wealth Banking", "PvB"],
    "Wealth Lending - PvB": ["Wealth Banking", "PvB"],
}
# ENR denominator for EA/AWC ratios (differs from the weighting/category ENR):
# SME uses the ENR 'ME' product; Wealth uses the ENR 'PvB' product.
EA_AWC_ENR_LINES = {
    "SME Banking": ["ME"],
    "Wealth Lending": ["PvB"],
    "Wealth Lending - PvB": ["PvB"],
}
# per-product deterioration ladder family (for GROUP 1bi/1bii/1c rating)
DET_FAMILY = {
    "Secured": "Secured", "Unsecured": "Unsecured", "SME Banking": "SME Banking",
    "Wealth Lending": "Wealth", "Wealth Lending - Retail Banking": "Wealth",
    "Wealth Lending - PvB": "Wealth",
}
# DPD$ numerator (table, product line) for 1bi/1bii/1c
DPD_LINES = {
    "Secured": ("90+$", "Consumer Secured"),
    "Unsecured": ("30+$", "Consumer Unsecured"),
    "SME Banking": ("30+$", "SME Banking"),
    "Wealth Lending": ("30+$", "Wealth Banking"),
    "Wealth Lending - Retail Banking": ("30+$", "Wealth Banking"),
    "Wealth Lending - PvB": ("30+$", "Wealth Banking"),
}
# which int_keys are TABLE OPERATIONS for each product (everything else -> weighted)
_DPD = {"dpd_qoq_cur", "dpd_qoq_prior", "dpd_yoy"}
# some products (PvB) carry a blank int_key on 1bi/1bii/1c (Not Applicable per
# country); at GROUP they still use the 30+$ table op, so map by canonical id.
CANON_TO_DPD = {"1bi": "dpd_qoq_cur", "1bii": "dpd_qoq_prior", "1c": "dpd_yoy"}
TABLE_OP_KEYS = {
    "Secured": _DPD | {"policy_exc_rate", "ltv"},
    "Unsecured": _DPD | {"policy_exc_rate", "volatile"},
    "SME Banking": _DPD | {"ea_prop", "awc_prop", "policy_exc_rate"},
    "Wealth Lending": _DPD | {"ea_prop", "awc_prop", "policy_exc_rate", "shortfall"},
    "Wealth Lending - Retail Banking": set(_DPD),
    "Wealth Lending - PvB": _DPD | {"shortfall"},
}
PRODUCTS = set(CATEGORY_ENR_LINES)

# per-(product, canon) ENR-line override for the weighted method.  SME 1a weights
# on SME Banking + ME (the same combined ENR its per-country YoY value uses).
WEIGHT_OVERRIDE = {("SME Banking", "1a"): ["SME Banking", "ME"]}


# --------------------------------------------------------------------------- #
#  helpers
# --------------------------------------------------------------------------- #
def _canon(label: Any) -> str:
    m = re.match(r"^([0-9]+[a-z]*)", str(label).strip())
    return m.group(1) if m else str(label).strip()


def _months(mt):
    return list(getattr(mt, "months", []) or [])


def _at(months, back):
    return months[-1 - back] if len(months) > back else None


def _is_country(name) -> bool:
    return str(name).strip().lower() not in _EXCLUDE_ROWS


def _num(v):
    """Coerce a possibly-text amount ('28,755', '$4,740') to a float, else None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    st = str(v).strip().replace(",", "").replace("$", "").replace("%", "")
    if st in ("", "-", "n/a", "N/A", "na", "NA"):
        return None
    try:
        return float(st)
    except Exception:
        return None


def _round_half_up(x: float) -> int:
    return int(math.floor(float(x) + 0.5))


def _score_to_rating(score: Optional[float]) -> str:
    if score is None or score == "":
        return ""
    if score >= 4.5: return "Very High"
    if score >= 3.5: return "High"
    if score >= 2.5: return "Medium"
    if score >= 1.5: return "Low"
    return "Very Low"


def _sum_product_at(mt, product, month) -> Optional[float]:
    if mt is None or month is None:
        return None
    total, got = 0.0, False
    for (c, p), series in getattr(mt, "product_data", {}).items():
        if p == product and _is_country(c) and series.get(month) is not None:
            try:
                total += float(series[month]); got = True
            except Exception:
                pass
    return total if got else None


def _sum_lines_at(enr, lines, month) -> Optional[float]:
    """SUM over all countries of the category ENR line(s) at a month."""
    if enr is None or month is None:
        return None
    total, got = 0.0, False
    for line in lines:
        s = _sum_product_at(enr, line, month)
        if s is not None:
            total += s; got = True
    return total if got else None


def _country_lines_at(enr, lines, country, month) -> Optional[float]:
    """One country's category ENR (sum of the line(s)) at a month."""
    if enr is None or month is None:
        return None
    total, got = 0.0, False
    for line in lines:
        s = None
        try:
            s = enr.series_pp(country, line)
        except Exception:
            s = None
        if s and s.get(month) is not None:
            try:
                total += float(s[month]); got = True
            except Exception:
                pass
    return total if got else None


def _sum_country_at(mt, month) -> Optional[float]:
    if mt is None or month is None:
        return None
    total, got = 0.0, False
    for c, series in getattr(mt, "country_data", {}).items():
        if _is_country(c) and series.get(month) is not None:
            try:
                total += float(series[month]); got = True
            except Exception:
                pass
    return total if got else None


def _sum_last12_product(mt, product) -> float:
    if mt is None:
        return 0.0
    months = _months(mt)[-12:]
    total = 0.0
    for (c, p), series in getattr(mt, "product_data", {}).items():
        if p == product and _is_country(c):
            for m in months:
                if series.get(m) is not None:
                    try:
                        total += float(series[m])
                    except Exception:
                        pass
    return total


def _fmt_pct(v: Optional[float]) -> Any:
    return "" if v is None else f"{v * 100:.2f}%"


# --------------------------------------------------------------------------- #
#  TABLE-OPERATION values (ratio of totals over ALL countries)
# --------------------------------------------------------------------------- #
def _dpd_pct(tables, product, month) -> Optional[float]:
    dpd_key, dpd_prod = DPD_LINES[product]
    num = _sum_product_at(tables.get(dpd_key), dpd_prod, month)
    den = _sum_lines_at(tables.get("ENR"), CATEGORY_ENR_LINES[product], month)
    if num is None or not den:
        return None
    return num / den


def _ratio_value(tables, product, int_key) -> Optional[float]:
    if int_key in _DPD:
        dpd_key = DPD_LINES[product][0]
        months = _months(tables.get(dpd_key))
        pos = {"dpd_qoq_cur": (0, 3), "dpd_qoq_prior": (1, 4), "dpd_yoy": (0, 12)}[int_key]
        a = _dpd_pct(tables, product, _at(months, pos[0]))
        b = _dpd_pct(tables, product, _at(months, pos[1]))
        return None if (a is None or b is None) else (a - b)

    if int_key == "policy_exc_rate":
        prod = DPD_LINES[product][1]      # policy is keyed by the same product line
        pol = tables.get("policy_exception") or {}
        num = _sum_last12_product(pol.get("left"), prod) + _sum_last12_product(pol.get("right"), prod)
        den = _sum_last12_product(tables.get("new_approved"), prod)
        return (num / den) if den else None

    if int_key == "ltv":
        mt = tables.get("LTV80"); months = _months(mt)
        tot = getattr(mt, "country_data", {}).get("Total") if mt is not None else None
        if not tot or not months:
            return None
        v = tot.get(months[-1])
        return None if v is None else float(v)     # raw Total (already a fraction)

    if int_key == "volatile":
        vol = tables.get("ccpl_volatile") or {}
        for key in ("Total", "TOTAL", "total", "Global", "GLOBAL", "Grand Total"):
            if key in vol:
                try:
                    return float(vol[key])
                except Exception:
                    return None
        return None

    if int_key == "shortfall":
        wm = tables.get("wm_shortfall") or {}
        sec = _num((wm.get("securities") or {}).get("__total__"))
        re_ = _num((wm.get("real_estate") or {}).get("__total__"))
        if sec is None and re_ is None:
            return None
        total = (sec or 0) + (re_ or 0)
        enr = tables.get("ENR")
        den = _sum_product_at(enr, "Wealth Banking", _at(_months(enr), 0))
        if not den:
            return None
        return (total / 1000.0) / den

    if int_key in ("ea_prop", "awc_prop"):
        mt = _ea_awc_num_table(tables, product, int_key)   # SME -> ME table, Wealth -> PvB table
        if mt is None:
            return None
        lines = EA_AWC_ENR_LINES.get(product, CATEGORY_ENR_LINES[product])
        enr = tables.get("ENR")
        month = _at(_months(mt), 0)              # point-in-time: latest month only
        num = _sum_country_at(mt, month)
        den = _sum_lines_at(enr, lines, _at(_months(enr), 0))
        if num is None or not den:
            return None
        return num / den

    return None


def _ea_awc_num_table(tables, product, int_key):
    """EA/AWC numerator MonthTable: SME Banking uses the ME EA/AWC sheet; Wealth
    products use the PvB EA/AWC sheet (mirrors _ratio_pvb's title selection)."""
    kind = "EA" if int_key == "ea_prop" else "AWC"
    if product == "SME Banking":
        me = tables.get("ME_EA_AWC") or {}
        return me.get(EA_TITLE if kind == "EA" else AWC_TITLE) or \
            (me.get("ME EA NPP in $mn") if kind == "EA" else None)
    pv = tables.get("PvB_EA_AWC") or {}
    items = list(pv.items())
    ea_tbl = awc_tbl = None
    for title, mt in items:
        t = str(title).lower()
        if "ea" in t and "awc" not in t:
            ea_tbl = mt
        elif "awc" in t and awc_tbl is None:
            awc_tbl = mt
    if ea_tbl is None and len(items) >= 2:
        awc_tbl, ea_tbl = items[0][1], items[1][1]
    elif len(items) == 1 and awc_tbl is None:
        awc_tbl = items[0][1]
    return ea_tbl if kind == "EA" else awc_tbl


def _rate_ratio(m, int_key, value):
    if value is None:
        return ""
    if int_key in _DPD:
        return C.r_deterioration(value)          # single deterioration ladder
    try:
        return m["rating"](value, None)          # policy/ltv/volatile/ea/awc ignore ctx
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
#  ENR country-% weighted values (all other labels)
# --------------------------------------------------------------------------- #
def _weights(tables, product, countries, lines=None):
    """weight(country) = country ENR (latest) / TOTAL ENR over ALL countries
    (latest).  Not renormalised.  `lines` overrides the ENR line(s) (used for
    SME 1a, which weights on SME Banking + ME)."""
    enr = tables.get("ENR")
    month = _at(_months(enr), 0)
    lines = lines or CATEGORY_ENR_LINES[product]
    total = _sum_lines_at(enr, lines, month)
    out = {}
    for c in countries:
        ec = _country_lines_at(enr, lines, c, month)
        out[c] = (ec / total) if (ec is not None and total) else None
    return out


def _weighted_number(num_by_country, weight_by_country):
    s, any_w = 0.0, False
    for c, rn in num_by_country.items():
        w = weight_by_country.get(c)
        if rn is None or w is None:
            continue
        s += float(rn) * float(w); any_w = True
    if not any_w:
        return None
    return s


# --------------------------------------------------------------------------- #
#  build the GROUP block for one product
# --------------------------------------------------------------------------- #
def append_group_rows(frame, product_out, tables):
    if frame is None or frame.empty:
        return frame
    cols = list(frame.columns)
    lab_col = "Label" if "Label" in cols else cols[1]
    ctry_col = "Country" if "Country" in cols else cols[0]

    body = frame[~frame[lab_col].astype(str).str.startswith("Calculated")]
    body = body[body[ctry_col] != GROUP_COUNTRY]
    countries = list(dict.fromkeys(body[ctry_col].tolist()))
    if not countries:
        return frame

    weights = _weights(tables, product_out, countries)
    table_ops = TABLE_OP_KEYS.get(product_out, set(_DPD))
    metric_defs = C.METRICS[product_out]()

    # per-country risk numbers per label (for the weighted method)
    num_maps: Dict[str, Dict[str, Optional[float]]] = {}
    for m in metric_defs:
        rows = body[body[lab_col] == m["label"]]
        cmap = {}
        for _, r in rows.iterrows():
            n = r.get("Risk Number", "")
            try:
                cmap[r[ctry_col]] = float(n) if n not in ("", None) else None
            except Exception:
                cmap[r[ctry_col]] = None
        num_maps[_canon(m["label"])] = cmap

    # deterioration: compute all three GROUP values first, so 1bi/1bii can be
    # pair-rated (AND) exactly like the per-country label.
    fam = DET_FAMILY.get(product_out, "Secured")
    dpd_group = {ck: _ratio_value(tables, product_out, ik)
                 for ck, ik in CANON_TO_DPD.items()}

    def _rate_dpd(canon, value):
        if value is None:
            return ""
        if canon == "1c":
            return C.r_deterioration(value, C.DET_SINGLE[fam])
        ctx = {"1bi": dpd_group.get("1bi"), "1bii": dpd_group.get("1bii")}
        return C.r_deterioration_pair(ctx, C.DET_PAIR[fam])

    group_num: Dict[str, Optional[int]] = {}
    group_rows: List[Dict[str, Any]] = []
    for m in metric_defs:
        canon = _canon(m["label"])
        int_key = getattr(m.get("value"), "int_key", m.get("value"))
        if (not int_key) and canon in CANON_TO_DPD:
            int_key = CANON_TO_DPD[canon]     # PvB deterioration -> Wealth 30+$ op
        if int_key in table_ops:
            if int_key in _DPD:
                val = dpd_group.get(canon)
                rating = _rate_dpd(canon, val)
            else:
                val = _ratio_value(tables, product_out, int_key)
                rating = _rate_ratio(m, int_key, val)
            display = _fmt_pct(val)
            number = E.RISK_NUMBER.get(rating) if rating else None
            note = "GROUP table operation (all countries)"
        else:
            ov = WEIGHT_OVERRIDE.get((product_out, canon))
            w = _weights(tables, product_out, countries, lines=ov) if ov else weights
            wsum = _weighted_number(num_maps.get(canon, {}), w)
            if wsum is None:
                number, rating, display = None, "", ""
            else:
                number = min(5, max(1, _round_half_up(wsum)))
                rating = _NUM_TO_RATING[number]
                display = round(wsum, 3)
            note = "GROUP ENR-weighted (country %)"
        group_num[canon] = number
        row = {c: "" for c in cols}
        row[ctry_col] = GROUP_COUNTRY
        row[lab_col] = m["label"]
        if "Value" in row: row["Value"] = display
        if "Risk Rating" in row: row["Risk Rating"] = rating
        if "Risk Number" in row: row["Risk Number"] = (number if number is not None else "")
        if "What to do in Value Column" in row: row["What to do in Value Column"] = note
        group_rows.append(row)

    # GROUP inherent: worst risk number per theme x weight, 1bii excluded
    groups = C.AGG_GROUPS.get(product_out, [])
    weight = (1.0 / len(groups)) if (C.NORMALISE_WEIGHTS and groups) else C.W6
    ordered = [_canon(m["label"]) for m in metric_defs]
    score = 0.0
    for positions in groups:
        theme = []
        for p in positions:
            if 1 <= p <= len(ordered):
                canon = ordered[p - 1]
                if canon in INHERENT_EXCLUDE_CANON:
                    continue
                n = group_num.get(canon)
                if n is not None:
                    theme.append(n)
        if theme:
            score += weight * max(theme)
    inh = {c: "" for c in cols}
    inh[ctry_col] = GROUP_COUNTRY
    inh[lab_col] = C.FINAL_LABEL
    if "Value" in inh: inh["Value"] = round(score, 4)
    if "Risk Rating" in inh:
        inh["Risk Rating"] = _score_to_rating(score if score else None)
    group_rows.append(inh)

    return pd.concat([frame, pd.DataFrame(group_rows, columns=cols)], ignore_index=True)


def append_all(frames, tables):
    for name, df in list(frames.items()):
        product_out = name.replace("IRA - ", "", 1)
        if product_out in PRODUCTS:
            frames[name] = append_group_rows(df, product_out, tables)
    return frames


# --------------------------------------------------------------------------- #
#  GROUP trace - the full arithmetic behind every GROUP row (for auditing)
# --------------------------------------------------------------------------- #
def _dpd_parts(tables, product, month):
    """(sum DPD$, sum category ENR, DPD%) at one month, over ALL countries."""
    dpd_key, dpd_prod = DPD_LINES[product]
    num = _sum_product_at(tables.get(dpd_key), dpd_prod, month)
    den = _sum_lines_at(tables.get("ENR"), CATEGORY_ENR_LINES[product], month)
    pct = (num / den) if (num is not None and den) else None
    return num, den, pct


def _fmt_m(m):
    try:
        return E.fmt_month(m)
    except Exception:
        return str(m)


def group_trace(frames, tables):
    """Return {product: [entry, ...]} where each entry documents one GROUP row:
        {label, kind, value, rating, number, detail: [(what, value), ...]}.
    The arithmetic mirrors append_group_rows exactly."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for name, frame in frames.items():
        product = name.replace("IRA - ", "", 1)
        if product not in PRODUCTS or frame is None or frame.empty:
            continue
        out[product] = _trace_group_product(frame, product, tables)
    return out


def _trace_group_product(frame, product, tables):
    cols = list(frame.columns)
    lab_col = "Label" if "Label" in cols else cols[1]
    ctry_col = "Country" if "Country" in cols else cols[0]
    body = frame[~frame[lab_col].astype(str).str.startswith("Calculated")]
    body = body[body[ctry_col] != GROUP_COUNTRY]
    countries = list(dict.fromkeys(body[ctry_col].tolist()))
    weights = _weights(tables, product, countries)
    table_ops = TABLE_OP_KEYS.get(product, set(_DPD))
    metric_defs = C.METRICS[product]()
    enr = tables.get("ENR")

    num_maps = {}
    for m in metric_defs:
        rows = body[body[lab_col] == m["label"]]
        cmap = {}
        for _, r in rows.iterrows():
            n = r.get("Risk Number", "")
            try:
                cmap[r[ctry_col]] = float(n) if n not in ("", None) else None
            except Exception:
                cmap[r[ctry_col]] = None
        num_maps[_canon(m["label"])] = cmap

    fam = DET_FAMILY.get(product, "Secured")
    dpd_group = {ck: _ratio_value(tables, product, ik) for ck, ik in CANON_TO_DPD.items()}

    def rate_dpd(canon, value):
        if value is None:
            return ""
        if canon == "1c":
            return C.r_deterioration(value, C.DET_SINGLE[fam])
        ctx = {"1bi": dpd_group.get("1bi"), "1bii": dpd_group.get("1bii")}
        return C.r_deterioration_pair(ctx, C.DET_PAIR[fam])

    group_num, entries = {}, []
    for m in metric_defs:
        canon = _canon(m["label"])
        int_key = getattr(m.get("value"), "int_key", m.get("value"))
        if (not int_key) and canon in CANON_TO_DPD:
            int_key = CANON_TO_DPD[canon]
        detail = []
        if int_key in table_ops:
            kind = "table operation (all countries)"
            if int_key in _DPD:
                val = dpd_group.get(canon)
                rating = rate_dpd(canon, val)
                months = _months(tables.get(DPD_LINES[product][0]))
                p = {"1bi": (0, 3), "1bii": (1, 4), "1c": (0, 12)}[canon]
                mc, mr = _at(months, p[0]), _at(months, p[1])
                nc, dc, pc = _dpd_parts(tables, product, mc)
                nr, dr, pr = _dpd_parts(tables, product, mr)
                detail = [(f"sum DPD$ @ {_fmt_m(mc)}", nc), (f"sum ENR @ {_fmt_m(mc)}", dc),
                          (f"DPD% @ {_fmt_m(mc)}", _fmt_pct(pc)),
                          (f"sum DPD$ @ {_fmt_m(mr)}", nr), (f"sum ENR @ {_fmt_m(mr)}", dr),
                          (f"DPD% @ {_fmt_m(mr)}", _fmt_pct(pr)),
                          ("= current - reference", _fmt_pct(val))]
                if canon == "1bi":
                    detail.append(("pair-rated with 1bii (AND)", _fmt_pct(dpd_group.get("1bii"))))
            else:
                val = _ratio_value(tables, product, int_key)
                rating = _rate_ratio(m, int_key, val)
                detail = _ratio_detail(tables, product, int_key, val)
            display = _fmt_pct(val)
            number = E.RISK_NUMBER.get(rating) if rating else None
        else:
            kind = "ENR-weighted (country %)"
            ov = WEIGHT_OVERRIDE.get((product, canon))
            w = _weights(tables, product, countries, lines=ov) if ov else weights
            lines = ov or CATEGORY_ENR_LINES[product]
            total = _sum_lines_at(enr, lines, _at(_months(enr), 0))
            wsum = _weighted_number(num_maps.get(canon, {}), w)
            detail.append((f"total ENR (all countries) = weight denominator", round(total, 2) if total else total))
            for c in countries:
                rn = num_maps.get(canon, {}).get(c)
                wt = w.get(c)
                if rn is None or wt is None:
                    continue
                detail.append((f"{c}:  risk {int(rn)}  x  weight {wt*100:.2f}%", round(rn * wt, 4)))
            if wsum is None:
                number, rating, display = None, "", ""
                detail.append(("no config country had a risk number", ""))
            else:
                number = min(5, max(1, _round_half_up(wsum)))
                rating = _NUM_TO_RATING[number]
                display = round(wsum, 3)
                detail.append(("weighted sum", round(wsum, 4)))
                detail.append((f"rounded to nearest 1..5", number))
        group_num[canon] = number
        entries.append({"label": m["label"], "kind": kind, "value": display,
                        "rating": rating, "number": number, "detail": detail})

    # inherent
    groups = C.AGG_GROUPS.get(product, [])
    weight = (1.0 / len(groups)) if (C.NORMALISE_WEIGHTS and groups) else C.W6
    ordered = [_canon(mm["label"]) for mm in metric_defs]
    score, idetail = 0.0, [(f"theme weight = {weight:.4f}  (1bii excluded)", "")]
    for positions in groups:
        labs = [ordered[p - 1] for p in positions if 1 <= p <= len(ordered)]
        pairs = [(lb, group_num.get(lb)) for lb in labs if lb not in INHERENT_EXCLUDE_CANON]
        valid = [n for _, n in pairs if n is not None]
        mx = max(valid) if valid else None
        if mx is not None:
            score += weight * mx
        idetail.append((f"theme {labs}: max risk = {mx}", (round(weight * mx, 4) if mx is not None else 0)))
    entries.append({"label": C.FINAL_LABEL, "kind": "inherent (worst per theme x weight)",
                    "value": round(score, 4), "rating": _score_to_rating(score if score else None),
                    "number": "", "detail": idetail + [("score", round(score, 4))]})
    return entries


def _ratio_detail(tables, product, int_key, val):
    enr = tables.get("ENR")
    if int_key == "policy_exc_rate":
        prod = DPD_LINES[product][1]
        pol = tables.get("policy_exception") or {}
        num = _sum_last12_product(pol.get("left"), prod) + _sum_last12_product(pol.get("right"), prod)
        den = _sum_last12_product(tables.get("new_approved"), prod)
        return [("sum L2+L3 (last 12m, all countries)", round(num, 4)),
                ("sum new approved (last 12m)", round(den, 4)), ("= ratio", _fmt_pct(val))]
    if int_key == "ltv":
        return [("LTV>80 'Total' row (raw fraction)", _fmt_pct(val))]
    if int_key == "volatile":
        vol = tables.get("ccpl_volatile") or {}
        g = vol.get("Total", vol.get("Global"))
        return [("CCPL portfolio 'Global'/'Total' value", _fmt_pct(g))]
    if int_key in ("ea_prop", "awc_prop"):
        mt = _ea_awc_num_table(tables, product, int_key)
        lines = EA_AWC_ENR_LINES.get(product, CATEGORY_ENR_LINES[product])
        src = "ME" if product == "SME Banking" else "PvB"
        month = _at(_months(mt), 0) if mt is not None else None
        num = _sum_country_at(mt, month) if mt is not None else None
        den = _sum_lines_at(enr, lines, _at(_months(enr), 0))
        return [(f"sum {'EA' if int_key=='ea_prop' else 'AWC'}$ ({src} table) @ {_fmt_m(month)}",
                 round(num, 4) if num is not None else None),
                (f"sum ENR ({'/'.join(lines)}) @ latest", round(den, 2) if den else den),
                ("= ratio (point-in-time)", _fmt_pct(val))]
    if int_key == "shortfall":
        wm = tables.get("wm_shortfall") or {}
        sec = (wm.get("securities") or {}).get("__total__")
        re_ = (wm.get("real_estate") or {}).get("__total__")
        den = _sum_product_at(enr, "Wealth Banking", _at(_months(enr), 0))
        return [("securities Total-Amount", sec), ("real-estate Total-Amount", re_),
                ("(sum) / 1000", round(((sec or 0) + (re_ or 0)) / 1000.0, 4)),
                ("sum ENR Wealth Banking (all countries)", round(den, 2) if den else den),
                ("= ratio", _fmt_pct(val))]
    return [("value", _fmt_pct(val))]
