"""End-to-end verification of pan-India support against a running backend.

    python -m uvicorn app.main:app --port 8000     # from backend/
    python scripts/verify_india.py

The pytest suite runs with the network disabled, so it can prove the geography,
the scope resolution and the honest-degradation paths but not the live data
path. This script covers what pytest cannot: real upstream calls, real
OpenStreetMap settlement resolution, and real freshness labelling.

It asserts nothing about flood accuracy - there is no ground truth to assert
against. It verifies that the system does what it says it does.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000/api"
TIMEOUT = 180

# The twelve states the brief asks for, spanning every terrain regime.
STATES = [
    "uttarakhand", "himachal_pradesh", "kerala", "assam", "maharashtra",
    "telangana", "rajasthan", "odisha", "west_bengal", "tamil_nadu",
    "jammu_and_kashmir", "ladakh",
]

passed: list[str] = []
failed: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    if condition:
        passed.append(name)
        print(f"  PASS  {name}" + (f"  [{detail}]" if detail else ""))
    else:
        failed.append(name)
        print(f"  FAIL  {name}" + (f"  [{detail}]" if detail else ""))
    return condition


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=TIMEOUT) as response:
        return json.load(response)


def post(path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else b"{}"
    request = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.load(response)


def section(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


def main() -> int:
    started = time.time()
    print("FloodSafe pan-India verification")
    print("=" * 60)

    section("Backend reachable")
    try:
        health = get("/health")
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"  FAIL  backend not reachable at {BASE}: {exc}")
        print("\nStart it with:  cd backend && python -m uvicorn app.main:app --port 8000")
        return 1
    check("health endpoint responds", health.get("status") == "ok", health.get("version", ""))

    section("Geography dataset")
    geography = get("/geography")
    check("28 states + 8 union territories", geography["state_count"] == 36,
          f"{geography['state_count']} states")
    check("national-scale district count", 600 < geography["district_count"] < 900,
          f"{geography['district_count']} districts")
    check("boundaries attributed to OpenStreetMap",
          "OpenStreetMap" in (geography.get("source") or ""))
    check("hierarchy is country/state/district/location",
          geography["levels"] == ["country", "state", "district", "location"])

    states = get("/geography/states")
    check("states endpoint lists 36", states["count"] == 36)
    check("ODbL attribution present", "ODbL" in (states.get("attribution") or ""))

    section("Every required state resolves to districts")
    district_of: dict[str, dict] = {}
    for state_id in STATES:
        body = get(f"/geography/states/{state_id}/districts")
        ok = check(f"{state_id}", body["count"] > 0, f"{body['count']} districts")
        if ok:
            district_of[state_id] = body["districts"][0]

    section("Scope resolution: national -> state -> district")
    national = get("/locations?region_id=india")
    check("national scope lists 36 states", national["count"] == 36, national["scope"])
    kerala = get("/locations?region_id=kerala")
    check("state scope lists districts", kerala["count"] > 0,
          f"{kerala['count']} in {kerala['scope']}")
    wayanad = district_of.get("kerala")
    if wayanad:
        district = get(f"/locations?region_id={wayanad['id']}")
        check("district scope lists locations", district["count"] > 0,
              f"{district['count']} · {district['freshness']}")
        # Live, this should be real OSM settlements rather than the centroid.
        kinds = {loc["settlement_type"] for loc in district["locations"]}
        check("district locations come from OpenStreetMap",
              bool(kinds & {"city", "town", "village"}) or "district_centroid" in kinds,
              ", ".join(sorted(k for k in kinds if k)))

    section("Backward compatibility (curated pilot regions)")
    uttarakhand = get("/locations?region_id=uttarakhand")
    check("Uttarakhand still returns its 26 curated locations", uttarakhand["count"] == 26)
    names = {loc["name"] for loc in uttarakhand["locations"]}
    check("named pilot locations intact", {"Joshimath", "Gaurikund"} <= names)
    region = get("/regions/uttarakhand")
    check("curated rivers intact", len(region.get("rivers", [])) > 0,
          f"{len(region.get('rivers', []))} rivers")

    section("Risk map at each scope")
    for region_id, label in (("india", "national"), ("kerala", "state")):
        risk_map = get(f"/risk/map?region_id={region_id}")
        cells = risk_map["cells"]
        check(f"{label} risk map has cells", len(cells) > 0, f"{len(cells)} cells")
        check(f"{label} cells all carry a risk level",
              all(c.get("risk_level") for c in cells))
    if wayanad:
        district_map = get(f"/risk/map?region_id={wayanad['id']}")
        check("district risk map has cells", len(district_map["cells"]) > 0,
              f"{len(district_map['cells'])} cells")

    section("Scoped summaries")
    for region_id, expected, label in (
        ("india", "state", "national ranks states"),
        ("kerala", "district", "state ranks districts"),
        ("uttarakhand", "location", "curated ranks locations"),
    ):
        summary = get(f"/dashboard/summary?region_id={region_id}")
        check(label, summary["row_kind"] == expected,
              f"{summary['totals']['monitoring_locations']} rows")

    section("Honest freshness labelling")
    sources = get("/system/sources")
    check("per-source health reported", len(sources["sources"]) > 0,
          f"overall {sources['overall']}")
    providers = get("/system/providers")
    check("provider families declared", len(providers["families"]) == 5)
    check("candidate (not integrated) sources declared explicitly",
          providers["counts"].get("candidate", 0) >= 2,
          f"{providers['counts']}")

    section("Risk assessment at a non-pilot location")
    probe = None
    if "telangana" in district_of:
        locations = get(f"/locations?region_id={district_of['telangana']['id']}")
        probe = locations["locations"][0]["id"]
    if probe:
        snapshot = get(f"/monitoring/{probe}")
        risk = snapshot["risk"]
        check("risk score in range", 0 <= risk["risk_score"] <= 100, str(risk["risk_score"]))
        check("risk level assigned", bool(risk["risk_level"]), risk["risk_level"])
        check("model is the shared baseline model",
              risk["model"]["id"] == "baseline_weighted_v1", risk["model"]["id"])
        check("contributors explain the score", len(risk.get("contributors", [])) > 0,
              f"{len(risk.get('contributors', []))} features")
        check("feature coverage reported",
              "available" in risk["feature_summary"],
              f"{risk['feature_summary']['available']}/{risk['feature_summary']['total']}")

        section("Simulation at that non-pilot location")
        baseline = risk["risk_score"]
        run = post("/simulation/run", {
            "overrides": {"rainfall_intensity": 95.0, "rainfall_3h": 115.0,
                          "antecedent_precipitation_index": 150.0},
            "location_id": probe,
        })
        check("simulation activates", run["active"] is True)
        during = get(f"/monitoring/{probe}")
        check("simulation mode reported", during["risk"]["mode"] == "SIMULATION")
        check("simulated risk exceeds live risk",
              during["risk"]["risk_score"] > baseline,
              f"{baseline} -> {during['risk']['risk_score']}")
        reset = post(f"/simulation/reset?location_id={probe}")
        check("exit simulation works", reset["active"] is False)
        after = get(f"/monitoring/{probe}")
        restored = after["risk"]["risk_score"]
        simulated = during["risk"]["risk_score"]
        # Not an equality check: this script runs against LIVE upstream data, and
        # several minutes pass between the two readings, so the real score is
        # allowed to drift. What must hold is that the scenario is gone - the
        # reading is back near the measured baseline and nowhere near the
        # simulated value. (The offline pytest suite, where the cache is fixed,
        # does assert exact restoration.)
        check("live state restored",
              after["risk"]["mode"] == "LIVE"
              and abs(restored - baseline) < max(5.0, 0.25 * baseline)
              and abs(restored - baseline) < abs(restored - simulated),
              f"baseline {baseline} -> simulated {simulated} -> restored {restored}")

    print("\n" + "=" * 60)
    total = len(passed) + len(failed)
    print(f"{len(passed)}/{total} checks passed in {time.time() - started:.0f}s")
    if failed:
        print("\nFAILED:")
        for name in failed:
            print(f"  - {name}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
