"""
Score Brick building models (TTL files) with the knowledge-graph completeness equation

    C_KG = 0.2*C_E + 0.3*C_R + 0.4*C_S + 0.1*C_TS     (weights in kg_eval/config.py)

Usage (from this folder):
    python evaluate_kg.py                                  # every TTL in "TTL Files/Mortar Models"
    python evaluate_kg.py "TTL Files/Mortar Models/bldg1.ttl" --details
    python evaluate_kg.py --building-type laboratory
    python evaluate_kg.py --timeseries-dir data/csv --ts-interval 900

Results are written to results/kg_scores.csv (one row per building) and
results/kg_details.json (every sub-metric plus example problems for each building).
"""
import argparse
import csv
import glob
import json
import os
import sys
import time

from kg_eval import config
from kg_eval.metrics import evaluate
from kg_eval.model import BuildingModel
from kg_eval.ontology import BrickOntology
from kg_eval.timeseries import TimeseriesData

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUT = os.path.join(HERE, "TTL Files", "Mortar Models")
DEFAULT_ONTOLOGY = os.path.join(HERE, "TTL Files", "Brick Ontology", "Brick-1.4.4.ttl")


def collect_files(inputs):
    files = []
    for item in inputs:
        if os.path.isdir(item):
            files += glob.glob(os.path.join(item, "*.ttl"))
        else:
            files += glob.glob(item)
    def natural(path):
        import re
        return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", os.path.basename(path))]
    return sorted(set(files), key=natural)


def flat_row(res):
    s, c = res["summary"], res["counts"]
    e, r = res["entity_completeness"], res["relationship_completeness"]
    sem, ts = res["semantic_correctness"], res["timeseries"]
    row = {
        "building": res["building"],
        "C_KG": s["C_KG"], "C_E": s["C_E"], "C_R": s["C_R"], "C_S": s["C_S"], "C_TS": s["C_TS"],
        "C_KG_area_weighted": s["C_KG_area_weighted"], "building_type": s["building_type"],
        "entities": c["entities"], "points": c.get("point", 0), "equipment": c.get("equipment", 0),
        "locations": c.get("location", 0), "unknown_category": c.get("unknown", 0),
        "E_building": e["locations"]["building"]["ratio"], "E_floor": e["locations"]["floor"]["ratio"],
        "E_hvac_zone": e["locations"]["hvac_zone"]["ratio"], "E_room": e["locations"]["room"]["ratio"],
        "E_point_completeness": e["point_completeness"],
        "R_correctness": r["correctness"], "R_completeness": r["completeness"],
        "R_asserted": r["asserted"], "R_correct": r["correct"], "R_incorrect": r["incorrect"],
        "R_missing_required": r["missing_required"],
        "S_naming": sem.get("naming_score", 0), "S_accurate_property_fraction": sem.get("accurate_property_fraction", 0),
        "S_invalid_or_near_miss_types": sum(p["count"] for p in sem.get("problem_types", [])
                                           if p["status"] in ("invalid", "near_miss")),
        "S_label_checked": sem.get("label_checked", 0), "S_label_conflicts": sem.get("label_conflicts", 0),
        "S_label_consistency": sem.get("label_consistency"),
        "S_deprecated_types": sum(p["count"] for p in sem.get("problem_types", []) if p["status"] == "deprecated"),
        "TS_points_requiring": ts["points_requiring_ts"], "TS_points_with_ref": ts["points_with_ts"],
        "C_TSA": ts["C_TSA"], "C_TSC": ts["C_TSC"], "C_TSS": ts["C_TSS"], "C_TSV": ts["C_TSV"],
    }
    for name, a in res["areas"].items():
        row[f"area_{name}_C_KG"] = a["C_KG"] if a["present"] else None
    return row


def print_table(rows):
    hdr = f"{'Building':<10}{'C_KG':>7}{'C_E':>7}{'C_R':>7}{'C_S':>7}{'C_TS':>7}{'Area-wt':>9}" \
          f"{'Entities':>10}{'Points':>8}{'Rels':>7}"
    print(hdr)
    print("-" * len(hdr))
    for r in sorted(rows, key=lambda x: -x["C_KG"]):
        print(f"{r['building']:<10}{r['C_KG']:>7.3f}{r['C_E']:>7.3f}{r['C_R']:>7.3f}{r['C_S']:>7.3f}"
              f"{r['C_TS']:>7.3f}{r['C_KG_area_weighted']:>9.3f}{r['entities']:>10}{r['points']:>8}"
              f"{r['R_asserted']:>7}")
    n = len(rows)
    if n > 1:
        mean = {k: sum(r[k] for r in rows) / n for k in ("C_KG", "C_E", "C_R", "C_S", "C_TS", "C_KG_area_weighted")}
        print("-" * len(hdr))
        print(f"{'MEAN':<10}{mean['C_KG']:>7.3f}{mean['C_E']:>7.3f}{mean['C_R']:>7.3f}{mean['C_S']:>7.3f}"
              f"{mean['C_TS']:>7.3f}{mean['C_KG_area_weighted']:>9.3f}")


def print_details(res):
    print(f"\n=== {res['building']}  C_KG = {res['summary']['C_KG']:.3f} ===")
    e = res["entity_completeness"]
    print("  C_E  locations:", {k: f"{v['represented']}/{v['expected']}" for k, v in e["locations"].items()},
          f"| essential-point completeness {e['point_completeness']:.2f}")
    for name, d in e["equipment_templates"].items():
        miss = ", ".join(f"{k} ({n})" for k, n in d["missing_slots"].items()) or "none"
        print(f"       {name}: {d['count']} units, coverage {d['mean_coverage']:.2f}; missing: {miss}")
    r = res["relationship_completeness"]
    print(f"  C_R  correctness {r['correct']}/{r['asserted']} = {r['correctness']:.3f} x "
          f"requirement completeness {r['completeness']:.3f}")
    for why, n in r["incorrect_reasons"].items():
        print(f"       incorrect: {why} x{n}  e.g. {r['incorrect_examples'][why][:2]}")
    for why, n in r["missing"].items():
        print(f"       missing:   {why} x{n}  e.g. {r['missing_examples'][why][:2]}")
    s = res["semantic_correctness"]
    print(f"  C_S  naming {s['naming_score']:.3f}, accurate-property fraction {s['accurate_property_fraction']:.3f}")
    for p in s["problem_types"][:10]:
        sug = f" -> did you mean {p['suggested']}?" if p["suggested"] else ""
        print(f"       {p['status']}: {p['class']} x{p['count']}{sug}")
    if s["type_status_counts"].get("valid_rec_alias"):
        print(f"       (accepted {s['type_status_counts']['valid_rec_alias']} Brick location types that 1.4 maps to REC)")
    for why, n in s["property_problems"].items():
        print(f"       property problem: {why} x{n}")
    if s.get("label_checked"):
        print(f"       label/class consistency: {s['label_checked'] - s['label_conflicts']}/{s['label_checked']} "
              f"checkable entities consistent ({s['label_consistency']:.3f})")
        for p in s["label_conflict_patterns"][:8]:
            print(f"       label conflict x{p['count']}: '{p['example_label']}' typed {p['class']} "
                  f"({p['dimension']}: label says {p['label_says']}, class says {p['class_says']})")
    t = res["timeseries"]
    extra = (f" x C_TSC {t['C_TSC']:.3f} x C_TSS {t['C_TSS']:.3f} x C_TSV {t['C_TSV']:.3f} "
             f"= {t['C_TS']:.3f}  ({t['points_with_data']} points had data)") if t["measured"] else \
        "  (no time-series data supplied: C_TSC/C_TSS/C_TSV not measured)"
    print(f"  C_TS C_TSA = {t['points_with_ts']}/{t['points_requiring_ts']} = {t['C_TSA']:.3f}{extra}")
    print("  Areas:", {k: (round(v["C_KG"], 3) if v["present"] else "absent") for k, v in res["areas"].items()})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="*", default=[DEFAULT_INPUT], help="TTL files or folders")
    ap.add_argument("--ontology", default=DEFAULT_ONTOLOGY, help="Brick ontology .ttl")
    ap.add_argument("--out", default=os.path.join(HERE, "results"), help="output folder")
    ap.add_argument("--building-type", default=config.DEFAULT_BUILDING_TYPE,
                    choices=sorted(config.BUILDING_TYPE_PROFILES), help="weights for the area scores")
    ap.add_argument("--details", action="store_true", help="print a per-building breakdown")
    ap.add_argument("--timeseries-dir", help="folder of <timeseries id>.csv files (optional)")
    ap.add_argument("--ts-start", help="expected data window start (ISO or epoch)")
    ap.add_argument("--ts-end", help="expected data window end (ISO or epoch)")
    ap.add_argument("--ts-interval", type=float, help="expected sampling interval in seconds")
    args = ap.parse_args(argv)

    files = collect_files(args.inputs)
    if not files:
        sys.exit(f"No .ttl files found in {args.inputs}")
    t0 = time.time()
    onto = BrickOntology(args.ontology)
    print(f"Loaded Brick ontology ({len(onto.classes)} classes) in {time.time() - t0:.1f}s; "
          f"evaluating {len(files)} model(s)\n")
    ts_data = TimeseriesData(args.timeseries_dir, args.ts_start, args.ts_end, args.ts_interval) \
        if args.timeseries_dir else None

    results, rows = [], []
    for f in files:
        try:
            model = BuildingModel(f, onto)
            res = evaluate(model, args.building_type, ts_data)
        except Exception as exc:   # keep going if one file is broken
            print(f"!! {os.path.basename(f)}: {type(exc).__name__}: {exc}")
            continue
        results.append(res)
        rows.append(flat_row(res))
        if args.details:
            print_details(res)
    if args.details:
        print()
    print_table(rows)

    os.makedirs(args.out, exist_ok=True)
    csv_path = os.path.join(args.out, "kg_scores.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()})
    json_path = os.path.join(args.out, "kg_details.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, default=str)
    print(f"\nWrote {csv_path}\nWrote {json_path}")


if __name__ == "__main__":
    main()
