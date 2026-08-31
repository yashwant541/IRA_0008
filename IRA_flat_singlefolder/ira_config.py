"""
ira_config.py
=============
The *what* of the assessment, kept apart from the *how* in ira_engine.py.

For every product (Secured / Unsecured / SME Banking / Wealth Lending) this file
lists, in output order, each metric:

    id      - short code used by the aggregation groups
    label   - exact text shown in column B of the output
    value   - fn(tables, country, product_input_label) -> float | str | None
              (column C: computed straight from the input tables per the
               "What to do in Value Column" instructions)
    rating  - fn(value, ctx) -> "Very Low".."Very High" | "Not Available"
              (column D: the IF-threshold ladder, transcribed per product)

``ctx`` is the dict of already-computed values for the same country/product, so
compound metrics (e.g. the QoQ current+prior pair) can see each other.

GROUPS + WEIGHTS drive the final "Calculated Inherent Credit Risk Assessment":
    score = SUM over groups of  agg(risk_numbers in group) * weight
    rating = >=4.5 Very High / >=3.5 High / >=2.5 Medium / >=1.5 Low / else Very Low

>>> The WEIGHTS below are PLACEHOLDERS (they sum to 100% per product).  Replace
>>> them with your BB:BD metric/weight table when you have it - just edit the
>>> numbers here, nothing else changes.  The grouping mirrors the MAX(...) blocks
>>> in the template's final-row formulas; confirm it against your real formula.
"""

from __future__ import annotations
from typing import Dict, List, Callable, Any

try:                        # inside the IRA package (Dataiku library)
    from . import ira_engine as E
except ImportError:         # flat import (local / standalone)
    import ira_engine as E


# --------------------------------------------------------------------------- #
#  Value extractors  (column C)  -- now read from the intermediate layer
# --------------------------------------------------------------------------- #
# Every metric value comes from a pre-computed intermediate table (built once in
# ira_intermediate.build and stored at tables["intermediates"]).  Extractors are
# thin lookups keyed by (country, OUTPUT product) - e.g. "Secured".  This keeps
# the finals and the intermediate outputs perfectly consistent and lets us
# surface a REASON for every "Not Available".

def from_int(int_key: str):
    """Return an extractor that pulls value + reason from an intermediate table."""
    def f(t, country, prod_out):
        recs = t.get("intermediates", {}).get(int_key, {})
        rec = recs.get((country, prod_out)) or recs.get((country, None))
        return rec if rec else {"value": None, "reason": "intermediate not built"}
    f.int_key = int_key
    return f


# each extractor returns the intermediate record dict {value, reason, ...}
v_asset_growth_yoy     = from_int("enr_yoy")
v_dpd_qoq_current      = from_int("dpd_qoq_cur")
v_dpd_qoq_prior        = from_int("dpd_qoq_prior")
v_dpd_yoy              = from_int("dpd_yoy")
v_dpd_pct_of_total     = from_int("dpd_pct_total")
v_policy_exception_rate = from_int("policy_exc_rate")
v_ea_proportion        = from_int("ea_prop")
v_awc_proportion       = from_int("awc_prop")
v_ltv_concentration    = from_int("ltv")
v_volatile_concentration = from_int("volatile")
v_ppi_yoy              = from_int("ppi_yoy")
v_interest_rate_increase = from_int("interest_inc")
v_country_outlook      = from_int("sovereign_outlook")
v_country_grading      = from_int("sovereign_grade")
v_active_dispensations = from_int("dispensations")
v_cra_breaches         = from_int("breaches")
v_shortfall            = from_int("shortfall")


def v_blank(tables, country, prod_out):
    """A cell that is intentionally Not Applicable for this product/segment."""
    return {"value": None, "reason": "Not applicable for this product"}
v_blank.int_key = ""


def r_blank(value, ctx=None):
    return "Not Applicable"


# --------------------------------------------------------------------------- #
#  Rating ladders  (column D)   -- transcribed verbatim from the IF-formulas
# --------------------------------------------------------------------------- #

def _pair_rating(ctx, cur_key, prior_key, ladder):
    """QoQ metrics rate on BOTH current & prior exceeding a bound (AND logic)."""
    cur, prior = ctx.get(cur_key), ctx.get(prior_key)
    if cur is None or prior is None:
        return "Not Available"
    for bound, rating in ladder:
        if bound is None:
            return rating
        if cur > bound and prior > bound:
            return rating
    return ladder[-1][1]


# DPD deterioration thresholds (difference current - back, as a fraction;
# x100 = percentage points).  Transcribed verbatim from the business IF-formulas.
#   Secured 1bi  : AND >0.05% VH / >0.03% H / >0.02% M / >0.01% L / else VL
#   Secured 1c   : >0.1% VH / >0.05% H / >0.03% M / >0.01% L / else VL
#   Unsec/SME 1bi: AND >0.25% VH / >0.06% H / >0.04% M / >0.01% L / else VL
#   Unsec/SME 1c : >0.25% VH / >0.2% H / >0.15% M / >0.1% L / else VL
#   Wealth 1bi   : AND >0.25% VH / AND >0% H / else VL
#   Wealth 1c    : >0.25% VH / >0% H / else VL
DET_PAIR = {
    "Secured": [(.0005, "Very High"), (.0003, "High"), (.0002, "Medium"),
                (.0001, "Low"), (None, "Very Low")],
    "Unsecured": [(.0025, "Very High"), (.0006, "High"), (.0004, "Medium"),
                  (.0001, "Low"), (None, "Very Low")],
    "SME Banking": [(.0025, "Very High"), (.0006, "High"), (.0004, "Medium"),
                    (.0001, "Low"), (None, "Very Low")],
    "Wealth": [(.0025, "Very High"), (0, "High"), (None, "Very Low")],
}
DET_SINGLE = {
    "Secured": [(.001, "Very High"), (.0005, "High"), (.0003, "Medium"),
                (.0001, "Low"), (None, "Very Low")],
    "Unsecured": [(.0025, "Very High"), (.002, "High"), (.0015, "Medium"),
                  (.001, "Low"), (None, "Very Low")],
    "SME Banking": [(.0025, "Very High"), (.002, "High"), (.0015, "Medium"),
                    (.001, "Low"), (None, "Very Low")],
    "Wealth": [(.0025, "Very High"), (0, "High"), (None, "Very Low")],
}
# back-compat default (used only if a caller passes no ladder)
DETERIORATION_LADDER = DET_SINGLE["Secured"]


def r_deterioration(value, ladder=None):
    """Single-value deterioration rating (used for 1c)."""
    return E.bands_desc(value, ladder or DETERIORATION_LADDER)


def r_deterioration_pair(ctx, ladder=None):
    """Paired deterioration rating (used for 1bi & 1bii together)."""
    return _pair_rating(ctx, "1bi", "1bii", ladder or DETERIORATION_LADDER)


def r_grading(value, ctx=None):
    # single source of truth lives in ira_sovereign
    try:
        from . import ira_sovereign as SOV
    except ImportError:
        import ira_sovereign as SOV
    return SOV.rating_for_grading(value)


def r_outlook(value, ctx=None):
    try:
        from . import ira_sovereign as SOV
    except ImportError:
        import ira_sovereign as SOV
    return SOV.rating_for_outlook(value)


# --------------------------------------------------------------------------- #
#  Per-product metric definitions
# --------------------------------------------------------------------------- #

def _m(id, label, value, rating, group, weight_key):
    return dict(id=id, label=label, value=value, rating=rating,
                group=group, weight_key=weight_key)


# ladders as (bound, rating) descending; None bound = floor
def secured_metrics() -> List[dict]:
    return [
        _m("1a", "1a.Asset Growth Year on Year as %", v_asset_growth_yoy,
           lambda v, c: E.bands_desc(v, [(.10, "Very High"), (.05, "High"),
                                         (.03, "Medium"), (.01, "Low"),
                                         (None, "Very Low")]),
           "G1", "1a"),
        _m("1bi", "1bi.QoQ Deterioration in 90+DPD$% in Current Month",
           v_dpd_qoq_current,
           lambda v, c: r_deterioration_pair(c, DET_PAIR["Secured"]),
           "G2", "delinquency"),
        _m("1bii", "1bii.QoQ Deterioration in 90+DPD$% in Prior Month",
           v_dpd_qoq_prior,
           lambda v, c: r_deterioration_pair(c, DET_PAIR["Secured"]),
           "G2", "delinquency"),
        _m("1c", "1c. YoY Deterioration in 90+ DPD$%", v_dpd_yoy,
           lambda v, c: r_deterioration(v, DET_SINGLE["Secured"]), "G2", "delinquency"),
        _m("1d", "1d.Country 90+DPD% Total Group 90+ Delinquency",
           v_dpd_pct_of_total,
           lambda v, c: E.bands_desc(v, [(.25, "Very High"), (.15, "High"),
                                         (.05, "Medium"), (.025, "Low"),
                                         (None, "Very Low")]),
           "G2", "delinquency"),
        _m("1e", "1e. Last 12 Months Policy Exceptions rate",
           v_policy_exception_rate,
           lambda v, c: E.bands_desc(v, [(.15, "Very High"), (.10, "High"),
                                         (.075, "Medium"), (.05, "Low"),
                                         (None, "Very Low")]),
           "G3", "policy_disp"),
        _m("1f", "1f. Active Dispensations on Secured Lending",
           v_active_dispensations,
           lambda v, c: _disp_rating(v), "G3", "policy_disp"),
        _m("1g", "1g.Concentration of Mortgage exposures exceeding LTV 80%",
           v_ltv_concentration,
           lambda v, c: E.bands_desc(v, [(.10, "Very High"), (.05, "High"),
                                         (.025, "Medium"), (.01, "Low"),
                                         (None, "Very Low")]),
           "G4", "ltv"),
        _m("1h", "1h.Credit Risk Appetite breaches in last 12 months",
           v_cra_breaches, lambda v, c: _breach_rating(v), "G5", "breaches"),
        _m("2a", "2a. Interest Rate Increase from Last 3 Years Average",
           v_interest_rate_increase,
           lambda v, c: E.bands_desc(v, [(.03, "Very High"), (.02, "High"),
                                         (.01, "Medium"), (0, "Low"),
                                         (None, "Very Low")]),
           "G6", "macro"),
        _m("2b", "2b. YOY Change in Property Price Index", v_ppi_yoy,
           lambda v, c: _ppi_rating(v), "G6", "macro"),
        _m("2c", "2c.Country Risk - Outlook", v_country_outlook, r_outlook,
           "G6", "macro"),
        _m("2d", "2d.Country Risk - Grading", v_country_grading, r_grading,
           "G6", "macro"),
    ]


def unsecured_metrics() -> List[dict]:
    return [
        _m("1a", "1a.Asset Growth Year on Year as %", v_asset_growth_yoy,
           lambda v, c: E.bands_desc(v, [(.15, "Very High"), (.05, "High"),
                                         (.03, "Medium"), (.01, "Low"),
                                         (None, "Very Low")]), "G1", "1a"),
        _m("1bi", "1bi.QoQ Deterioration in 30+DPD$% in Current Month",
           v_dpd_qoq_current,
           lambda v, c: r_deterioration_pair(c, DET_PAIR["Unsecured"]), "G2", "delinquency"),
        _m("1bii", "1bii.QoQ Deterioration in 30+DPD$% in Prior Month",
           v_dpd_qoq_prior,
           lambda v, c: r_deterioration_pair(c, DET_PAIR["Unsecured"]), "G2", "delinquency"),
        _m("1c", "1c. YoY Deterioration in 30+ DPD$%", v_dpd_yoy,
           lambda v, c: r_deterioration(v, DET_SINGLE["Unsecured"]), "G2", "delinquency"),
        _m("1d", "1d.Country 30+DPD% Total Group 30+ Delinquency",
           v_dpd_pct_of_total,
           lambda v, c: E.bands_desc(v, [(.10, "Very High"), (.075, "High"),
                                         (.05, "Medium"), (.025, "Low"),
                                         (None, "Very Low")]), "G2", "delinquency"),
        _m("1e", "1e. Last 12 Months Policy Exceptions rate",
           v_policy_exception_rate,
           lambda v, c: E.bands_desc(v, [(.05, "Very High"), (.01, "High"),
                                         (.005, "Medium"), (.0025, "Low"),
                                         (None, "Very Low")]), "G3", "policy_disp"),
        _m("1f", "1f. Active Dispensations on Unsecured Lending",
           v_active_dispensations, lambda v, c: _disp_rating(v),
           "G3", "policy_disp"),
        _m("1g", "1g.Portfolio Concentration in Volatile Segment",
           v_volatile_concentration,
           lambda v, c: E.bands_desc(v, [(.125, "Very High"), (.10, "High"),
                                         (.075, "Medium"), (.05, "Low"),
                                         (None, "Very Low")]), "G4", "volatile"),
        _m("1h", "1h.Credit Risk Appetite breaches in last 12 months",
           v_cra_breaches, lambda v, c: _breach_rating(v), "G5", "breaches"),
        _m("2a", "2a.Country Risk - Outlook", v_country_outlook, r_outlook,
           "G6", "macro"),
        _m("2b", "2b.Country Risk - Grading", v_country_grading, r_grading,
           "G6", "macro"),
    ]


def sme_metrics() -> List[dict]:
    return [
        _m("1a", "1a.Asset Growth Year on Year as %", v_asset_growth_yoy,
           lambda v, c: E.bands_desc(v, [(.15, "Very High"), (.05, "High"),
                                         (.03, "Medium"), (.01, "Low"),
                                         (None, "Very Low")]), "G1", "1a"),
        _m("1bi", "1bi.QoQ Deterioration in 30+DPD$% in Current Month",
           v_dpd_qoq_current,
           lambda v, c: r_deterioration_pair(c, DET_PAIR["SME Banking"]), "G2", "delinquency"),
        _m("1bii", "1bii.QoQ Deterioration in 30+DPD$% in Prior Month",
           v_dpd_qoq_prior,
           lambda v, c: r_deterioration_pair(c, DET_PAIR["SME Banking"]), "G2", "delinquency"),
        _m("1c", "1c. YoY Deterioration in 30+ DPD$%", v_dpd_yoy,
           lambda v, c: r_deterioration(v, DET_SINGLE["SME Banking"]), "G2", "delinquency"),
        _m("1d", "1d.Country 30+DPD% Total Group 30+ Delinquency",
           v_dpd_pct_of_total,
           lambda v, c: E.bands_desc(v, [(.20, "Very High"), (.15, "High"),
                                         (.10, "Medium"), (.05, "Low"),
                                         (None, "Very Low")]), "G2", "delinquency"),
        _m("1e", "1e. Proportion of Exposure in Early Alert (EA)",
           v_ea_proportion,
           lambda v, c: E.bands_desc(v, [(.10, "Very High"), (.075, "High"),
                                         (.05, "Medium"), (.025, "Low"),
                                         (None, "Very Low")]), "G2", "delinquency"),
        _m("1f", "1f. Proportion of Exposure in Collection (AWC)",
           v_awc_proportion,
           lambda v, c: E.bands_desc(v, [(.125, "Very High"), (.10, "High"),
                                         (.075, "Medium"), (.05, "Low"),
                                         (None, "Very Low")]), "G2", "delinquency"),
        _m("1g", "1g.Last 12 Months Policy Exceptions rate",
           v_policy_exception_rate,
           lambda v, c: E.bands_desc(v, [(.075, "Very High"), (.05, "High"),
                                         (.03, "Medium"), (.01, "Low"),
                                         (None, "Very Low")]), "G3", "policy_disp"),
        _m("1h", "1h.Active Dispensations on SME Lending",
           v_active_dispensations, lambda v, c: _disp_rating(v),
           "G3", "policy_disp"),
        _m("1i", "1i.Credit Risk Appetite breaches in last 12 months",
           v_cra_breaches, lambda v, c: _breach_rating(v), "G4", "breaches"),
        _m("2a", "2a.Country Risk - Outlook", v_country_outlook, r_outlook,
           "G5", "macro"),
        _m("2b", "2b.Country Risk - Grading", v_country_grading, r_grading,
           "G5", "macro"),
    ]


def wealth_metrics(kind: str = "total") -> List[dict]:
    """Wealth Lending family.  kind in {'total','retail','pvb'}.
    New label set: 1a, 1bi, 1bii, 1c, 1d(EA), 1e(AWC), 1f(Shortfall, blank),
    1g(policy), 1h(dispensations), 2a(outlook), 2b(grading).
      - total : ENR = Retail+PvB summed; DPD/EA/AWC/policy/disp as before.
      - retail: ENR = Wealth Banking; EA/AWC BLANK; DPD as before.
      - pvb   : ENR = PvB; DPD BLANK; EA/AWC from the PvB table.
    Blank cells are rated 'Not Applicable' and ignored by the final score."""
    is_pvb = (kind == "pvb")
    is_retail = (kind == "retail")

    def ea_band(v, c):   # 1d EA:  >1% VH / >0.75% H / >0.5% M / >0.25% L / else VL
        return E.bands_desc(v, [(.01, "Very High"), (.0075, "High"),
                                (.005, "Medium"), (.0025, "Low"), (None, "Very Low")])

    def awc_band(v, c):  # 1e AWC: >3.5% VH / >2.5% H / >1.5% M / >0.5% L / else VL
        return E.bands_desc(v, [(.035, "Very High"), (.025, "High"),
                                (.015, "Medium"), (.005, "Low"), (None, "Very Low")])

    # 1bi/1bii/1c: blank for PvB, else Wealth deterioration ladders
    dpd_cur_v = v_blank if is_pvb else v_dpd_qoq_current
    dpd_pri_v = v_blank if is_pvb else v_dpd_qoq_prior
    dpd_yoy_v = v_blank if is_pvb else v_dpd_yoy
    dpd_pair_r = r_blank if is_pvb else (lambda v, c: r_deterioration_pair(c, DET_PAIR["Wealth"]))
    dpd_yoy_r = r_blank if is_pvb else (lambda v, c: r_deterioration(v, DET_SINGLE["Wealth"]))

    # 1d/1e EA & AWC: blank for Retail, else computed (separate ladders)
    ea_v = v_blank if is_retail else v_ea_proportion
    awc_v = v_blank if is_retail else v_awc_proportion
    ea_r = r_blank if is_retail else ea_band
    awc_r = r_blank if is_retail else awc_band

    # 1f Shortfall: real value for total & PvB (blank for Retail)
    def shortfall_band(v, c):   # >25% VH / >10% H / >5% M / >3% L / else VL
        return E.bands_desc(v, [(.25, "Very High"), (.10, "High"),
                                (.05, "Medium"), (.03, "Low"), (None, "Very Low")])
    sf_v = v_blank if is_retail else v_shortfall
    sf_r = r_blank if is_retail else shortfall_band

    return [
        _m("1a", "1a.Asset Growth Year on Year as %", v_asset_growth_yoy,
           lambda v, c: E.bands_desc(v, [(.15, "Very High"), (.05, "High"),
                                         (.03, "Medium"), (.01, "Low"),
                                         (None, "Very Low")]), "G1", "1a"),
        _m("1bi", "1bi.QoQ Deterioration in 30+DPD$% in Current Month",
           dpd_cur_v, dpd_pair_r, "G2", "delinquency"),
        _m("1bii", "1bii.QoQ Deterioration in 30+DPD$% in Prior Month",
           dpd_pri_v, dpd_pair_r, "G2", "delinquency"),
        _m("1c", "1c. YoY Deterioration in 30+ DPD$%", dpd_yoy_v, dpd_yoy_r,
           "G2", "delinquency"),
        _m("1d", "1d. Proportion of Exposure in Early Alert (EA)",
           ea_v, ea_r, "G3", "exposure"),
        _m("1e", "1e. Proportion of Exposure in Collection (AWC)",
           awc_v, awc_r, "G3", "exposure"),
        _m("1f", "1f. Proportion of Exposure in Shortfall Status",
           sf_v, sf_r, "G3", "exposure"),
        _m("1g", "1g. Last 12 Months Policy Exceptions rate",
           v_policy_exception_rate,
           lambda v, c: E.bands_desc(v, [(.035, "Very High"), (.025, "High"),
                                         (.015, "Medium"), (.005, "Low"),
                                         (None, "Very Low")]), "G4", "policy_disp"),
        _m("1h", "1h.Active Dispensations on Wealth Lending",
           v_active_dispensations, lambda v, c: _disp_rating(v), "G4", "policy_disp"),
    ] + ([
        _m("1i", "1i.Credit Risk Appetite breaches in last 12 months",
           v_cra_breaches, lambda v, c: _breach_rating(v), "G5", "breaches"),
    ] if kind == "total" else []) + [
        _m("2a", "2a.Country Risk - Outlook", v_country_outlook, r_outlook,
           "G6", "macro"),
        _m("2b", "2b.Country Risk - Grading", v_country_grading, r_grading,
           "G6", "macro"),
    ]


# small shared rating helpers ------------------------------------------------ #
def _disp_rating(v):
    if v is None:
        return "Not Available"
    if v > 3:
        return "Very High"
    if v == 3:
        return "High"
    if v == 2:
        return "Medium"
    if v == 1:
        return "Low"
    return "Very Low"


def _breach_rating(v):
    if v is None:
        return "Not Available"
    if v > 1:
        return "Very High"
    if v == 1:
        return "High"
    return "Very Low"


def _ppi_rating(v):
    if v is None:
        return "Not Available"
    if v > .05:
        return "Very Low"
    if v > 0:
        return "Low"
    if v > -.05:
        return "Medium"
    if v > -.15:
        return "High"
    return "Very High"


def _wealth_pair(ctx):
    cur, prior = ctx.get("1bi"), ctx.get("1bii")
    if cur is None or prior is None:
        return "Not Available"
    if cur > .0025 and prior > .0025:
        return "Very High"
    if cur > 0 and prior > 0:
        return "High"
    return "Very Low"


# --------------------------------------------------------------------------- #
#  Aggregation groups + weights  (final "Calculated" row)
# --------------------------------------------------------------------------- #
# agg: "single" (one metric) or "max" (worst risk-number in the group).
# weight: PLACEHOLDER fraction - replace from your BB:BD table.

# --------------------------------------------------------------------------- #
#  Final "Calculated Inherent Credit Risk Assessment" aggregation
# --------------------------------------------------------------------------- #
# EXACT spec supplied by the business.  Each group is weighted 1/6 (16.67%).
# Groups are given by LABEL POSITION (1-based) in the metric list for the
# category; a group's value is the MAX risk number across its labels (a single
# label is just that label's risk number).
#
#   score = SUM over groups of  (1/6) * max(risk numbers in the group)
#   rating: >=4.5 Very High / >=3.5 High / >=2.5 Medium / >=1.5 Low / else Very Low
#
# NOTE: Secured & Unsecured have 6 groups (weights sum to 100%); SME & Wealth
# have 5 groups as specified (5 x 16.67% = 83.3%).  Set NORMALISE_WEIGHTS=True
# to instead divide by the actual group count per category (so every category
# sums to 100%).
W6 = 1.0 / 6.0
NORMALISE_WEIGHTS = False

AGG_GROUPS: Dict[str, List[List[int]]] = {
    # Secured (13 labels)
    "Secured": [[1], [2, 3, 4, 5], [6, 7, 8], [9], [10], [11, 12, 13]],
    # Unsecured (11 labels)
    "Unsecured": [[1], [2, 3, 4, 5], [6, 7], [8], [9], [10, 11]],
    # SME Banking (12 labels)
    "SME Banking": [[1], [2, 3, 4, 5, 6, 7], [8, 9], [10], [11, 12]],
    # Wealth Lending total (12 labels): 1a | dpd+EA+AWC+shortfall | policy+disp
    #   | breaches(1i) | outlook+grading.  Retail/PvB keep 11 labels (no 1i).
    "Wealth Lending": [[1], [2, 3, 4, 5, 6, 7], [8, 9], [10], [11, 12]],
    "Wealth Lending - Retail Banking": [[1], [2, 3, 4, 5, 6, 7], [8, 9], [10, 11]],
    "Wealth Lending - PvB": [[1], [2, 3, 4, 5, 6, 7], [8, 9], [10, 11]],
}

METRICS: Dict[str, Callable[[], List[dict]]] = {
    "Secured": secured_metrics,
    "Unsecured": unsecured_metrics,
    "SME Banking": sme_metrics,
    "Wealth Lending": lambda: wealth_metrics("total"),
    "Wealth Lending - Retail Banking": lambda: wealth_metrics("retail"),
    "Wealth Lending - PvB": lambda: wealth_metrics("pvb"),
}

FINAL_LABEL = "Calculated Inherent Credit Risk Assessment:"
