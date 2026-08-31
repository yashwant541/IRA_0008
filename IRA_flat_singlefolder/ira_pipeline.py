"""
ira_pipeline.py  -  Layered IRA architecture
============================================
A clean, inspectable 5-layer pipeline.  Each layer WRITES its output to its own
folder so you can open it, check it, and understand exactly what happened before
the next layer runs.  The heavy calculations reuse the proven engine
(ira_loaders / ira_intermediate / ira_build) - this module only orchestrates and
materialises, so the numbers stay identical to the verified single-run output.

Layers
------
L1  read_inputs      raw sheets, exactly as read            -> 1_raw/
L2  parse_inputs     split into country x product x months  -> 2_parsed/
L3  build_formulas   YoY / QoQ / proportions / lookups      -> 3_formulas/
L4  requirements     per-product folders + label->file map  -> 4_requirements/<Product>/
L5  process          final per-product/country assessment   -> 5_output/

Run everything with run_pipeline.py, or call `run(...)` here.
"""
from __future__ import annotations
import os
import csv
import shutil
from typing import Dict, List, Any, Optional

import pandas as pd

try:
    from . import (ira_io as IO, ira_loaders as L, ira_intermediate as I,
                   ira_build as B, ira_config as C, ira_engine as E,
                   ira_countries as CC)
except ImportError:
    import ira_io as IO, ira_loaders as L, ira_intermediate as I
    import ira_build as B, ira_config as C, ira_engine as E, ira_countries as CC


PRODUCTS = ["Secured", "Unsecured", "SME Banking", "Wealth Lending",
            "Wealth Lending - Retail Banking", "Wealth Lending - PvB"]

# ---- Layer-4 knowledge: which INPUT table(s) each metric key needs --------- #
# int_key -> human description + the source input sheet/table names it reads.
INT_KEY_SOURCES: Dict[str, Dict[str, Any]] = {
    "enr_yoy":        {"desc": "Asset growth YoY %", "sources": ["ENR"]},
    "dpd_qoq_cur":    {"desc": "QoQ deterioration (current)", "sources": ["30+% or 90+% (Secured)"]},
    "dpd_qoq_prior":  {"desc": "QoQ deterioration (prior)", "sources": ["30+% or 90+% (Secured)"]},
    "dpd_yoy":        {"desc": "YoY deterioration", "sources": ["30+% or 90+% (Secured)"]},
    "dpd_pct_total":  {"desc": "DPD$ share of group total", "sources": ["30+$ or 90+$ (Secured)"]},
    "policy_exc_rate": {"desc": "Policy exceptions (L2+L3) rate",
                        "sources": ["# policy exception L2 and L3", "#monthly new approved"]},
    "ea_prop":        {"desc": "Early Alert / ENR", "sources": ["ME EA AWC (PvB EA AWC for PvB)", "ENR"]},
    "awc_prop":       {"desc": "AWC / ENR", "sources": ["ME EA AWC (PvB EA AWC for PvB)", "ENR"]},
    "ltv":            {"desc": "LTV > 80 concentration", "sources": ["LTV > 80"]},
    "volatile":       {"desc": "CCPL volatile concentration", "sources": ["CCPL Volatile by Country"]},
    "ppi_yoy":        {"desc": "Property price index YoY", "sources": ["PPI"]},
    "interest_inc":   {"desc": "Interest-rate increase vs avg", "sources": ["Interest Rates"]},
    "sovereign_outlook": {"desc": "Country outlook", "sources": ["Other Tables: Sovereign"]},
    "sovereign_grade":   {"desc": "Country grading (FCY CRG)", "sources": ["Other Tables: Sovereign"]},
    "dispensations":  {"desc": "Active dispensations", "sources": ["Other Tables: Dispensations"]},
    "breaches":       {"desc": "CRA breaches (12m)", "sources": ["Other Tables: CRA Breaches"]},
    "":               {"desc": "Not applicable / blank for this product", "sources": []},
}


# --------------------------------------------------------------------------- #
#  small io helpers
# --------------------------------------------------------------------------- #
def _fresh(path: str):
    if os.path.isdir(path):
        shutil.rmtree(path)
    os.makedirs(path, exist_ok=True)
    return path


def _safe(name: str) -> str:
    for ch in '/\\:*?[]"<>|':
        name = name.replace(ch, "_")
    return name.strip() or "sheet"


def _write_csv(path: str, rows: List[List[Any]], header: Optional[List[str]] = None):
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        if header:
            w.writerow(header)
        for r in rows:
            w.writerow(["" if c is None else c for c in r])


def _write_df(path: str, df: pd.DataFrame):
    df.to_csv(path, index=False, encoding="utf-8-sig")


def _manifest(folder: str, lines: List[str]):
    with open(os.path.join(folder, "_README.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
#  LAYER 1 - read inputs
# --------------------------------------------------------------------------- #
def layer1_read(mi_path: str, other_paths: List[str], out_root: str) -> Dict[str, List[List[Any]]]:
    folder = _fresh(os.path.join(out_root, "1_raw"))
    sheets: Dict[str, List[List[Any]]] = {}
    index = [["source_file", "sheet", "rows", "cols", "written_csv"]]

    def ingest(path, prefix=""):
        try:
            raw, _warns = IO.read_workbook_checked(path)
        except Exception:
            xls = pd.read_excel(path, sheet_name=None, header=None, engine="openpyxl")
            raw = {n: d.values.tolist() for n, d in xls.items()}
        for sn, rows in raw.items():
            key = f"{prefix}{sn}" if prefix else sn
            sheets[key] = rows
            fn = _safe(f"{os.path.splitext(os.path.basename(path))[0]}__{sn}") + ".csv"
            _write_csv(os.path.join(folder, fn), rows)
            ncol = max((len(r) for r in rows), default=0)
            index.append([os.path.basename(path), sn, len(rows), ncol, fn])

    ingest(mi_path)
    for p in other_paths:
        ingest(p, prefix="OtherTables::")

    _write_csv(os.path.join(folder, "_index.csv"), index[1:], index[0])
    _manifest(folder, [
        "LAYER 1 - RAW INPUT", "=" * 40,
        "One CSV per input sheet, exactly as read (no processing).",
        "_index.csv lists every sheet, its size and the CSV written.",
        f"Total sheets read: {len(sheets)}",
    ])
    return sheets


# --------------------------------------------------------------------------- #
#  LAYER 2 - parse (split country / product / months)
# --------------------------------------------------------------------------- #
def _monthtable_to_rows(mt) -> (List[str], List[List[Any]]):
    months = list(getattr(mt, "months", []) or [])
    header = ["Country", "Product"] + [E.fmt_month(m) for m in months]
    rows = []
    for (country, product), series in getattr(mt, "product_data", {}).items():
        rows.append([country, product] + [series.get(m) for m in months])
    for country, series in getattr(mt, "country_data", {}).items():
        rows.append([country, ""] + [series.get(m) for m in months])
    return header, rows


def layer2_parse(sheets, out_root: str) -> Dict[str, Any]:
    folder = _fresh(os.path.join(out_root, "2_parsed"))
    tables = L.load_tables(sheets)
    index = [["table", "kind", "rows", "written_csv"]]

    for key, val in tables.items():
        if key in ("intermediates",) or key.endswith("_report"):
            continue
        fn = _safe(key) + ".csv"
        path = os.path.join(folder, fn)
        try:
            if hasattr(val, "product_data"):                    # MonthTable
                header, rows = _monthtable_to_rows(val)
                _write_csv(path, rows, header)
                index.append([key, "country x product x months", len(rows), fn])
            elif isinstance(val, dict) and val and all(isinstance(v, dict) for v in val.values()):
                # nested dict: sovereign {country:{...}} or per-category {cat:{country:val}}
                first = next(iter(val.values()))
                if all(not isinstance(x, dict) for x in first.values()):
                    # per-category -> {category: {country: value}}
                    rows = [[cat, country, v] for cat, inner in val.items()
                            for country, v in inner.items()]
                    _write_csv(path, rows, ["Category", "Country", "Value"])
                else:
                    # {country: {field: value}} (sovereign)
                    fields = sorted({k for inner in val.values() for k in inner})
                    rows = [[country] + [inner.get(f) for f in fields]
                            for country, inner in val.items()]
                    _write_csv(path, rows, ["Country"] + fields)
                index.append([key, "reference table", len(val), fn])
            elif isinstance(val, dict):
                # stacked country-only tables {title: MonthTable}
                if val and all(hasattr(v, "product_data") for v in val.values()):
                    allrows = []
                    for title, mt in val.items():
                        h, rws = _monthtable_to_rows(mt)
                        for r in rws:
                            allrows.append([title] + r)
                    _write_csv(path, allrows, ["Sub-table", "Country", "Product", "..."])
                    index.append([key, "stacked country tables", len(allrows), fn])
                else:
                    rows = [[k, v] for k, v in val.items()]
                    _write_csv(path, rows, ["Key", "Value"])
                    index.append([key, "map", len(val), fn])
        except Exception as ex:
            _write_csv(path, [[f"could not serialise: {ex}"]])
            index.append([key, "error", 0, fn])

    _write_csv(os.path.join(folder, "_index.csv"), index[1:], index[0])
    _manifest(folder, [
        "LAYER 2 - PARSED INPUT", "=" * 40,
        "Each input table split into a tidy CSV.",
        "Monthly tables -> Country | Product | <month columns>.",
        "Reference tables (sovereign, dispensations, breaches) -> flat CSVs.",
        "This is where 'country and product' are separated and made explicit.",
    ])
    return tables


# --------------------------------------------------------------------------- #
#  LAYER 3 - build formulas (YoY, QoQ, proportions, lookups)
# --------------------------------------------------------------------------- #
def layer3_formulas(tables, per_cat, out_root: str):
    folder = _fresh(os.path.join(out_root, "3_formulas"))
    frames = B.build_intermediate_frames(tables, per_cat)      # computes + returns
    index = [["formula_table", "rows", "written_csv"]]
    for title, df in frames.items():
        fn = _safe(title) + ".csv"
        _write_df(os.path.join(folder, fn), df)
        index.append([title, len(df), fn])
    _write_csv(os.path.join(folder, "_index.csv"), index[1:], index[0])
    _manifest(folder, [
        "LAYER 3 - FORMULAS", "=" * 40,
        "Every computed metric as its own CSV, with the value AND its components",
        "(current month, reference month, numerator/denominator, etc.).",
        "This is the single source of truth the final output reads from.",
    ])
    return frames


# --------------------------------------------------------------------------- #
#  LAYER 4 - requirements: per-product folders + label->file mapping
# --------------------------------------------------------------------------- #
def layer4_requirements(tables, per_cat, out_root: str):
    root = _fresh(os.path.join(out_root, "4_requirements"))
    # parsed tables (layer2) to copy the relevant ones per product
    l2 = os.path.join(out_root, "2_parsed")

    # which parsed CSVs correspond to which int_key (best-effort by table key)
    key_to_csv = {
        "enr_yoy": ["ENR"], "dpd_qoq_cur": ["90+%", "30+%"], "dpd_qoq_prior": ["90+%", "30+%"],
        "dpd_yoy": ["90+%", "30+%"], "dpd_pct_total": ["90+$", "30+$"],
        "policy_exc_rate": ["policy_exception", "new_approved"],
        "ea_prop": ["ME_EA_AWC", "PvB_EA_AWC", "ENR"], "awc_prop": ["ME_EA_AWC", "PvB_EA_AWC", "ENR"],
        "ltv": ["LTV80"], "volatile": ["ccpl_volatile"], "ppi_yoy": ["PPI"],
        "interest_inc": ["interest_rates"], "sovereign_outlook": ["sovereign"],
        "sovereign_grade": ["sovereign"], "dispensations": ["dispensations"],
        "breaches": ["cra_breaches"],
    }

    summary = [["product", "labels", "applicable_labels", "folder"]]
    for product in PRODUCTS:
        pdir = _fresh(os.path.join(root, _safe(product)))
        metric_defs = C.METRICS[product]()
        countries = per_cat.get(_base_category(product), [])
        # label -> file map
        maprows = []
        needed_tables = set()
        applicable = 0
        for m in metric_defs:
            ik = getattr(m["value"], "int_key", "")
            info = INT_KEY_SOURCES.get(ik, {"desc": "", "sources": []})
            is_app = bool(ik)
            applicable += 1 if is_app else 0
            maprows.append([m["id"], m["label"], ik or "(blank)", info["desc"],
                            "; ".join(info["sources"]) or "n/a",
                            "Yes" if is_app else "No (Not Applicable)"])
            for tk in key_to_csv.get(ik, []):
                if tables.get(tk) is not None:
                    needed_tables.add(tk)
        _write_csv(os.path.join(pdir, "label_map.csv"), maprows,
                   ["Label ID", "Label", "int_key", "What it is", "Source input file(s)", "Applicable"])
        # copy the parsed input tables this product actually needs
        copied = []
        inputs_dir = _fresh(os.path.join(pdir, "inputs"))
        for tk in sorted(needed_tables):
            src = os.path.join(l2, _safe(tk) + ".csv")
            if os.path.exists(src):
                shutil.copy(src, os.path.join(inputs_dir, _safe(tk) + ".csv"))
                copied.append(tk)
        # the config countries for this product
        _write_csv(os.path.join(pdir, "countries.csv"),
                   [[c] for c in countries], ["Country (Include=Yes)"])
        _manifest(pdir, [
            f"PRODUCT: {product}", "=" * 40,
            "label_map.csv : every label, the formula key, and which input file feeds it.",
            "inputs/       : copies of the parsed tables this product needs.",
            "countries.csv : the countries this product runs for (from countries_config).",
            f"Applicable labels: {applicable}/{len(metric_defs)}",
        ])
        summary.append([product, len(metric_defs), applicable, _safe(product)])

    _write_csv(os.path.join(root, "_summary.csv"), summary[1:], summary[0])
    _manifest(root, [
        "LAYER 4 - REQUIREMENTS", "=" * 40,
        "One folder per product (6 total).  Each contains:",
        "  - label_map.csv : label -> formula -> source input file",
        "  - inputs/       : the parsed input tables that product needs",
        "  - countries.csv : the config countries for that product",
        "_summary.csv lists the applicable-label count per product.",
    ])


def _base_category(product: str) -> str:
    if product.startswith("Wealth Lending"):
        return "Wealth Lending"
    return product


# raw MI sheet name behind each parsed table key (Layer 2 -> Layer 1)
RAW_SHEET = {
    "ENR": "ENR", "30+%": "30+%", "30+$": "30+$", "90+%": "90+%", "90+$": "90+$",
    "RWA": "RWA", "gco": "GCO %", "policy_exception": "# policy exception L2 and L3",
    "new_approved": "#monthly new approved", "ME_EA_AWC": "ME EA AWC",
    "PvB_EA_AWC": "PvB EA AWC", "LTV80": "LTV > 80 Excl MIP",
    "ccpl_volatile": "CCPL Volatile by Country", "PPI": "PPI",
    "interest_rates": "Interest Rates", "sovereign": "OtherTables: Sovereign",
    "dispensations": "OtherTables: Dispensations", "cra_breaches": "OtherTables: CRA Breaches",
}
# formula key -> parsed table key(s) it reads
KEY_TO_PARSED = {
    "enr_yoy": ["ENR"], "dpd_qoq_cur": ["90+%", "30+%"], "dpd_qoq_prior": ["90+%", "30+%"],
    "dpd_yoy": ["90+%", "30+%"], "dpd_pct_total": ["90+$", "30+$"],
    "policy_exc_rate": ["policy_exception", "new_approved"],
    "ea_prop": ["ME_EA_AWC", "PvB_EA_AWC", "ENR"], "awc_prop": ["ME_EA_AWC", "PvB_EA_AWC", "ENR"],
    "ltv": ["LTV80"], "volatile": ["ccpl_volatile"], "ppi_yoy": ["PPI"],
    "interest_inc": ["interest_rates"], "sovereign_outlook": ["sovereign"],
    "sovereign_grade": ["sovereign"], "dispensations": ["dispensations"], "breaches": ["cra_breaches"],
}


def layer_flow(tables, out_root: str):
    """Write the table lineage: raw sheet -> parsed table -> formula -> product/label."""
    folder = _fresh(os.path.join(out_root, "0_flow"))

    # --- per product/label lineage table --- #
    rows = [["Layer5 Output (Product)", "Label", "Layer3 Formula (key)",
             "Layer2 Parsed table(s)", "Layer1 Raw sheet(s)", "Applicable"]]
    edges_raw_parsed = set()
    edges_parsed_formula = set()
    edges_formula_product = set()
    for product in PRODUCTS:
        for m in C.METRICS[product]():
            ik = getattr(m["value"], "int_key", "")
            parsed = [k for k in KEY_TO_PARSED.get(ik, []) if tables.get(k) is not None]
            raws = [RAW_SHEET.get(k, k) for k in parsed]
            rows.append([product, m["label"], ik or "(blank)",
                         "; ".join(parsed) or "n/a", "; ".join(raws) or "n/a",
                         "Yes" if ik else "No"])
            for k in parsed:
                edges_raw_parsed.add((RAW_SHEET.get(k, k), k))
                edges_parsed_formula.add((k, ik))
            if ik:
                edges_formula_product.add((ik, product))
    _write_csv(os.path.join(folder, "lineage.csv"), rows[1:], rows[0])

    # --- mermaid flow diagram (raw -> parsed -> formula -> product) --- #
    def nid(s):
        return "n" + str(abs(hash(s)) % (10 ** 9))
    lines = ["flowchart LR",
             "  classDef raw fill:#eaf1ff,stroke:#1769ff;",
             "  classDef parsed fill:#e5f8f6,stroke:#00a99d;",
             "  classDef formula fill:#fff7e6,stroke:#e6a62f;",
             "  classDef product fill:#f1ebff,stroke:#7253cc;"]
    seen = {}
    def node(label, cls):
        if label not in seen:
            seen[label] = nid(label)
            lines.append(f'  {seen[label]}["{label}"]:::{cls}')
        return seen[label]
    for a, b in sorted(edges_raw_parsed):
        lines.append(f"  {node(a,'raw')} --> {node(b,'parsed')}")
    for a, b in sorted(edges_parsed_formula):
        if b:
            lines.append(f"  {node(a,'parsed')} --> {node('['+b+']','formula')}")
    for a, b in sorted(edges_formula_product):
        lines.append(f"  {node('['+a+']','formula')} --> {node(b,'product')}")
    mermaid = "\n".join(lines)
    with open(os.path.join(folder, "flow.md"), "w", encoding="utf-8") as fh:
        fh.write("# Table flow (layer to layer)\n\n```mermaid\n" + mermaid + "\n```\n")
    # standalone HTML that renders the mermaid diagram
    html = (
        "<!doctype html><html><head><meta charset='utf-8'><title>IRA table flow</title>"
        "<script src='https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js'></script>"
        "<style>body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#17233d}"
        "h1{font-size:20px}.legend span{margin-right:16px;font-size:12px}"
        ".k{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:5px;vertical-align:middle}"
        "</style></head><body><h1>IRA - table flow (Layer 1 -> 2 -> 3 -> 5)</h1>"
        "<div class='legend'><span><i class='k' style='background:#eaf1ff'></i>Raw sheet</span>"
        "<span><i class='k' style='background:#e5f8f6'></i>Parsed table</span>"
        "<span><i class='k' style='background:#fff7e6'></i>Formula</span>"
        "<span><i class='k' style='background:#f1ebff'></i>Product output</span></div>"
        "<pre class='mermaid'>" + mermaid + "</pre>"
        "<script>mermaid.initialize({startOnLoad:true,flowchart:{useMaxWidth:false}});</script>"
        "</body></html>")
    with open(os.path.join(folder, "flow.html"), "w", encoding="utf-8") as fh:
        fh.write(html)

    _manifest(folder, [
        "LAYER FLOW (LINEAGE)", "=" * 40,
        "lineage.csv : every product/label -> formula -> parsed table -> raw sheet.",
        "flow.md     : the same as a Mermaid diagram (view on GitHub / mermaid.live).",
        "flow.html   : open in a browser to SEE the flow rendered.",
        "Read direction: Raw sheet -> Parsed table -> Formula -> Product output.",
    ])
    return folder


# --------------------------------------------------------------------------- #
#  LAYER 5 - process to final per-product/country output
# --------------------------------------------------------------------------- #
def layer5_output(tables, per_cat, out_root: str):
    folder = _fresh(os.path.join(out_root, "5_output"))
    frames = B.build_all(tables, countries_per_category=per_cat)
    index = [["product_sheet", "rows", "written_csv"]]
    # per-product CSVs
    for name, df in frames.items():
        fn = _safe(name) + ".csv"
        _write_df(os.path.join(folder, fn), df)
        index.append([name, len(df), fn])
    # combined Excel
    xlsx_path = os.path.join(folder, "IRA_Output.xlsx")
    try:
        with pd.ExcelWriter(xlsx_path, engine="openpyxl") as xw:
            used = set()
            for name, df in frames.items():
                sn = E.sanitize_sheet_name(name.replace("IRA - ", ""), used)
                (df if not df.empty else pd.DataFrame({"(empty)": []})).to_excel(
                    xw, sheet_name=sn, index=False)
    except Exception:
        xlsx_path = None
    _write_csv(os.path.join(folder, "_index.csv"), index[1:], index[0])
    _manifest(folder, [
        "LAYER 5 - FINAL OUTPUT", "=" * 40,
        "Final per-product assessment: Country | Label | Value | Risk Rating | Risk Number.",
        "One CSV per product plus the combined IRA_Output.xlsx.",
        "Values come straight from Layer 3; only the config countries are included.",
    ])
    return frames


# --------------------------------------------------------------------------- #
#  orchestrator
# --------------------------------------------------------------------------- #
def run(mi_path: str, other_paths: Optional[List[str]] = None,
        config_path: str = "countries_config.csv",
        out_root: str = "ira_pipeline_output") -> str:
    other_paths = other_paths or []
    os.makedirs(out_root, exist_ok=True)

    # L1
    sheets = layer1_read(mi_path, other_paths, out_root)
    # L2
    tables = layer2_parse(sheets, out_root)
    # countries config (authoritative)
    per_cat = CC.load(config_path) if os.path.exists(config_path) else None
    per_cat = B.resolve_countries(tables, None, per_cat)
    # L3
    layer3_formulas(tables, per_cat, out_root)
    # L4
    layer4_requirements(tables, per_cat, out_root)
    # L5
    layer5_output(tables, per_cat, out_root)
    # Flow / lineage across layers
    layer_flow(tables, out_root)

    _manifest(out_root, [
        "IRA LAYERED PIPELINE OUTPUT", "=" * 40,
        "Open the folders in order:",
        "  0_flow/         - table lineage: raw -> parsed -> formula -> product (flow.html)",
        "  1_raw/          - inputs exactly as read",
        "  2_parsed/       - country x product x months, split out",
        "  3_formulas/     - YoY / QoQ / proportions / lookups (with components + Period)",
        "  4_requirements/ - per-product folders: label->file map + needed inputs",
        "  5_output/       - final per-product/country assessment + Excel (with Period)",
        "",
        "Period (e.g. 'Mar-26') is the latest month column in the MI data and is",
        "stamped on every Layer 3 and Layer 5 table.  To change a formula, edit the",
        "engine (ira_intermediate) and re-run; to change scope, edit countries_config.csv.",
    ])
    return out_root
