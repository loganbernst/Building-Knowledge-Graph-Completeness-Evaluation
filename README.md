# Brick Knowledge-Graph Completeness Evaluator

Scores Brick building models (`.ttl`) with the completeness equation from the research notes,
adapted to what the public Brick/Mortar TTL files actually contain.

```
C_KG = 0.2·C_E + 0.3·C_R + 0.4·C_S + 0.1·C_TS
```

**Why these weights differ from the notes (0.4 / 0.2 / 0.3 / 0.1):** the TTL files contain
no ground-truth `N_actual`, so C_E is an inferred estimate and is down-weighted to 0.2.
Its weight moved to components measured directly against the Brick ontology: C_R (0.3)
and C_S (0.4). If real `N_actual` counts become available, restore C_E to 0.4 in `config.py`.

## Quick start

```powershell
pip install -r requirements.txt
python evaluate_kg.py                     # all 45 models in "TTL Files/Mortar Models"
python evaluate_kg.py "TTL Files/Mortar Models/bldg2.ttl" --details   # full breakdown
python evaluate_kg.py --building-type laboratory                      # area weighting profile
python evaluate_kg.py --timeseries-dir path\to\csvs --ts-interval 900 # measure C_TSC/C_TSS/C_TSV
```

Outputs: `results/kg_scores.csv` (one row per building, every sub-metric) and
`results/kg_details.json` (plus example problems: invalid classes, missing points, bad links).
All weights and rules are in [kg_eval/config.py](kg_eval/config.py).

## The equation as implemented (and how it differs from the notes)

### C_E: entity completeness
Notes: `C_E = N_represented / N_actual` per entity type, optionally weighted.
**Change:** the TTL files contain no ground-truth `N_actual`, so `N_expected` is inferred from
the model itself:

| Entity type | Weight | N_expected |
|---|---|---|
| Building | 0.10 | 1 |
| Floor | 0.10 | ≥ 1 |
| HVAC_Zone | 0.10 | one per terminal unit (VAV) |
| Room/Space | 0.10 | one per HVAC_Zone |
| Points | 0.60 | essential points per equipment type (see below) |

Point completeness follows "some sensors are more useful than others": each equipment type has a
template of essential points (e.g. a VAV needs zone temperature, a zone setpoint, airflow and damper control;
an AHU needs supply/return/mixed/outside air temperatures, static pressure, fan and valve control).
Coverage is averaged per equipment type, then weighted by type, so 2,000 VAVs don't drown out one AHU.

### C_R: relationships
Notes: `C_R = N_correct / N_total`.
**Change:** `C_R = (N_correct / N_asserted) × mean over requirements(1 − N_missing / N_applicable)`

* *Correctness*: each asserted link is checked: the target exists, the predicate is real Brick,
  and the categories make sense (e.g. `hasPoint` must go Equipment/Location → Point; inverses are merged).
* *Completeness*: links that **should** exist: points attached to something, equipment/locations
  not isolated, controllable equipment exposes a Command/Setpoint (the **Control** sub-item),
  VAVs fed by upstream equipment and feeding a zone, AHUs feeding something.

Each requirement type counts equally. A plain `N_correct / (N_asserted + N_missing)` let
thousands of `hasPoint` links hide missing air-path topology, the "poor relationships"
problem the paper describes.

### C_S: semantics
Notes: `C_S = N_named_correctly / N_entities`, plus a way to reward accurate properties.
Implemented as `C_S = min(1, mean naming credit + 0.1 × fraction of entities with an accurate property)`.

Naming credit per entity, checked against the Brick 1.4.4 ontology:
valid = 1, generic (bare `Command`, `Sensor`…) = 0.5, deprecated = 0.75, not a Brick class = 0
(with a "did you mean…" suggestion when there's a close match). Location classes that Brick 1.4 maps to RealEstateCore
(`Building`, `Floor`, `Room`, `HVAC_Zone`) are accepted; set `ACCEPT_REC_REPLACED_CLASSES = False` to penalize them.
A property counts as accurate only if it uses a real Brick property with a well-formed value.

**Label/class consistency** ([kg_eval/labels.py](kg_eval/labels.py)): a class can exist in Brick
and still be the *wrong* class. Each entity's label (or IRI name if it has no label) is tokenized,
BMS abbreviations are expanded (`SAT STPT`, `CHWV`, `CCV`, `kBtu`…), and both the label and the class
are mapped onto six dimensions: quantity, medium, device, function, heating/cooling, occupied/unoccupied.
If label and class **contradict** each other on any dimension, that entity's naming credit is
multiplied by `LABEL_CONFLICT_FACTOR` (0.5). A label that is only more specific than its class is
not a contradiction (the "generic" credit already covers that), and ambiguous words ("mode",
"output", bare "water") are not mapped. In the Mortar set this flags 656 of 12,781 checkable
entities, all genuine:

| Count | Label example | Typed as | Contradiction |
|---|---|---|---|
| 639 (18 bldgs) | Zone Air Damper Command | `Damper_Position_Setpoint` | function: command vs setpoint |
| 16 (16 bldgs) | Asmundson_ChilledWater_Demand_kBtu | `Electric_Meter` | medium: chilled water vs electricity |
| 1 (bldg26) | Building Chilled Water Flow | `Outside_Air_Temperature_Sensor` | quantity and medium |

Remove a dimension from `LABEL_CHECK_DIMENSIONS` in `config.py` to stop checking it
(e.g. drop `"function"` if you consider command/setpoint interchangeable for dampers).

### C_TS: time series
Unchanged: `C_TS = C_TSA · C_TSC · C_TSS · C_TSV`.
`C_TSA` (points with a `ref:hasTimeseriesId` / points needing one, excluding Parameters) is
measured from the TTL. The other three need actual data. Pass `--timeseries-dir` with one
`<timeseries-id>.csv` per series (`timestamp,value`). Then:
* **C_TSC** = covered time / expected window (gaps longer than 2× the interval don't count as covered)
* **C_TSS** = observations received / observations expected
* **C_TSV** = valid fraction; missing/non-numeric, impossible values (per-class ranges), and
  stuck sensors (same value ≥ 24 h) are invalid

Without data those three factors are reported as "not measured" and `C_TS = C_TSA`.

### Area scores (thermal / energy / lighting)
As suggested in the notes, the same four components are also computed for each functional area,
and combined with a building-type profile (`--building-type office|laboratory|data_center|residential|retail`)
into `C_KG_area_weighted`. An area with no entities scores 0, so this number shows how
complete the model is *for that kind of building*. Example: most Mortar models have no energy meters.

## Notes on the data
* `TTL Files/Mortar Models`: 45 anonymized buildings from brickschema.org/resources
  (bldg1–44 + smc). `smc.ttl` and `bldg30.ttl` are different files but contain identical
  entity/relationship counts and scores, and are likely the same building.
* The 5 "original reference models" listed on the site currently return 404 and are not included.
* `TTL Files/Brick Ontology/Brick-1.4.4.ttl` is the reference schema for all class/property checks.
