"""
verify_sovereign.py
===================
INDEPENDENT check that Country Outlook and Country Grading in the FINAL output
match the raw "Country Sovereign Rating & Outlook" input table.

It does NOT reuse the engine's sovereign parser - it reads the input table with
its own minimal reader - so it is a genuine cross-check of input -> final output.

Usage:
    python verify_sovereign.py [MI.xlsx] [Other_Tables.xlsx]
(defaults: Dummy.xlsx and Other_Tables.xlsx next to this script)
"""
import os
import re
import sys
import glob
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, "IRA")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from IRA import ira_loaders as L, ira_build as B, ira_countries as CC, ira_engine as E
except ImportError:
    import ira_loaders as L, ira_build as B, ira_countries as CC, ira_engine as E


# ----- independent, minimal sovereign reader (no engine PARSING code) -------- #
def _norm(s):
    return re.sub(r"[^a-z0-9 ]", " ", str(s).lower()).strip()


def _ckey(name):
    # use the engine's canonical country key (alias table) for MATCHING only;
    # the table reading below stays fully independent.
    return E.country_key(name)


def read_sovereign_raw(files):
    """Return {country_key: {'outlook':.., 'grading':.., 'name':..}} straight
    from the input workbook(s), using only pandas."""
    out = {}
    for f in files:
        if not f or not os.path.exists(f):
            continue
        for _sn, df in pd.read_excel(f, sheet_name=None, header=None, engine="openpyxl").items():
            rows = df.values.tolist()
            i = 0
            while i < len(rows):
                joined = " ".join(str(c) for c in rows[i] if pd.notna(c)).lower()
                is_title = "sovereign" in joined or ("outlook" in joined and ("crg" in joined or "rating" in joined))
                if is_title:
                    # find header row within the next few rows
                    hdr = None
                    for j in range(i, min(i + 4, len(rows))):
                        jj = " ".join(_norm(c) for c in rows[j] if pd.notna(c))
                        if "country" in jj and ("crg" in jj or "outlook" in jj):
                            hdr = j
                            break
                    if hdr is None:
                        i += 1
                        continue
                    H = [_norm(c) if pd.notna(c) else "" for c in rows[hdr]]
                    def find(pred):
                        for k, h in enumerate(H):
                            if pred(h):
                                return k
                        return None
                    ci = find(lambda h: h == "country" or h.startswith("country")) or 0
                    fcy = find(lambda h: "fcy" in h)
                    oc = find(lambda h: "outlook" in h)
                    for r in rows[hdr + 1:]:
                        if ci >= len(r) or pd.isna(r[ci]) or not isinstance(r[ci], str):
                            if all(pd.isna(x) for x in r):
                                break
                            continue
                        name = r[ci].strip()
                        if name.lower() in ("country", "total"):
                            continue
                        out[_ckey(name)] = {
                            "name": name,
                            "outlook": (str(r[oc]).strip() if oc is not None and oc < len(r) and pd.notna(r[oc]) else None),
                            "grading": (str(r[fcy]).strip() if fcy is not None and fcy < len(r) and pd.notna(r[fcy]) else None),
                        }
                    i = hdr + 1
                else:
                    i += 1
    return out


def main():
    mi = sys.argv[1] if len(sys.argv) > 1 else "Dummy.xlsx"
    other = sys.argv[2] if len(sys.argv) > 2 else "Other_Tables.xlsx"

    expected = read_sovereign_raw([mi, other] + glob.glob(os.path.join(_HERE, "Other_Tables.xlsx")))
    print(f"Input sovereign table: {len(expected)} countries")

    # build the final output the same way run_local does
    sheets = {n: d.values.tolist() for n, d in pd.read_excel(mi, sheet_name=None, header=None, engine="openpyxl").items()}
    if os.path.exists(other):
        for n, d in pd.read_excel(other, sheet_name=None, header=None, engine="openpyxl").items():
            sheets[f"Other::{n}"] = d.values.tolist()
    cfg = CC.load("countries_config.csv") if os.path.exists("countries_config.csv") else None
    tables = L.load_tables(sheets)
    frames = B.build_all(tables, countries_per_category=cfg)

    OUT_LABEL = "Outlook"
    GRD_LABEL = "Grading"
    checked = mism = 0
    print("-" * 78)
    for cat, df in frames.items():
        for _, r in df.iterrows():
            label = str(r["Label"])
            if "Outlook" not in label and "Grading" not in label:
                continue
            country = r["Country"]
            if str(country) == "GROUP":     # GROUP roll-up rows have no sovereign text
                continue
            got = r["Value"]
            exp_rec = expected.get(_ckey(country))
            field = "outlook" if "Outlook" in label else "grading"
            exp = exp_rec.get(field) if exp_rec else None
            # normalise "None"/blank to None
            g = None if (got is None or str(got).strip() in ("", "None", "nan")) else str(got).strip()
            e = None if (exp is None or str(exp).strip() in ("", "None", "nan")) else str(exp).strip()
            checked += 1
            if g != e:
                mism += 1
                print(f"  MISMATCH {cat:20} {country:12} {field:8} final={g!s:10} input={e!s}")
    print("-" * 78)
    print(f"Checked {checked} sovereign cells across all categories. Mismatches: {mism}")
    print("PASS - final Outlook/Grading match the input table." if mism == 0
          else "FAIL - see mismatches above.")


if __name__ == "__main__":
    main()
