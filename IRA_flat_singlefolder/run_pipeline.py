"""
run_pipeline.py  -  run the layered IRA pipeline (Layers 1-5)

Usage:
    python run_pipeline.py MI_Data.xlsx [Other_Tables.xlsx ...] [--out FOLDER]

Reads the MI workbook (+ any Other_Tables workbooks), uses countries_config.csv
in this folder, and writes inspectable outputs for every layer under
./ira_pipeline_output (or --out FOLDER):

    1_raw/  2_parsed/  3_formulas/  4_requirements/<Product>/  5_output/

Each layer has a _README.txt and _index.csv.  Open them in order to see exactly
what the pipeline did and where any value came from.
"""
import os
import sys
import glob

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, "IRA")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from IRA import ira_pipeline as P
except ImportError:
    import ira_pipeline as P

CONFIG = "countries_config.csv"


def main():
    args = [a for a in sys.argv[1:]]
    out_root = "ira_pipeline_output"
    if "--out" in args:
        i = args.index("--out")
        out_root = args[i + 1]
        del args[i:i + 2]

    if not args:
        sys.exit("Usage: python run_pipeline.py MI_Data.xlsx [Other_Tables.xlsx ...] [--out FOLDER]")

    mi = args[0]
    if not os.path.splitext(mi)[1]:
        mi = mi + ".xlsx"
    if not os.path.exists(mi):
        sys.exit(f"ERROR: input workbook not found: {mi}")

    # extra reference workbooks: explicit args, else auto-discover Other_Tables*
    others = [a if os.path.splitext(a)[1] else a + ".xlsx" for a in args[1:]]
    others = [o for o in others if os.path.exists(o)]
    if not others:
        _dir = os.path.dirname(os.path.abspath(mi)) or "."
        for pat in ("Other_Tables.xlsx", "*Other*.xlsx", "*reference*.xlsx"):
            for f in glob.glob(os.path.join(_dir, pat)):
                if os.path.abspath(f) != os.path.abspath(mi) and f not in others:
                    others.append(f)

    if not os.path.exists(CONFIG):
        sys.exit(
            f"ERROR: '{CONFIG}' not found. It is user-defined (the script will not "
            "create it). Columns: Category,Country,Include (Include=Yes/No).")

    print(f"MI workbook : {mi}")
    print(f"Other tables: {others or '(none found)'}")
    print(f"Config      : {CONFIG}")
    root = P.run(mi, others, CONFIG, out_root)
    print("\nDone. Layered outputs written to:")
    for d in ("1_raw", "2_parsed", "3_formulas", "4_requirements", "5_output"):
        print(f"   {os.path.join(root, d)}")
    print(f"\nStart here: {os.path.join(root, '_README.txt')}")


if __name__ == "__main__":
    main()
