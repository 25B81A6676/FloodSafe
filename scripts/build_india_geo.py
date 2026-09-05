"""Generate the India administrative dataset from OpenStreetMap.

Run once (or when boundaries change); the output is committed.

    python scripts/build_india_geo.py

Writes:
    data/india/states.json      36 states + union territories
    data/india/districts.json   ~780 districts

WHY OSM: the project already queries Overpass for waterways and infrastructure,
so this adds no new dependency, no API key and no new licence obligation - the
ODbL attribution already shown in the footer covers it. Boundaries come from the
same relations that render on openstreetmap.org.

Nothing here is invented. Every bbox and centroid is what Overpass returned;
this script only reshapes and slugifies. Districts are admin_level=5, which is
what India's OSM community uses for districts (level 6 is tehsil/taluk - 6435 of
them nationally, far too fine). Verified against Rudraprayag, Wayanad,
Hyderabad, Nagpur, Jaipur, Kamrup Metropolitan, Chamoli, Ernakulam,
Thiruvananthapuram, Cuttack, Darjeeling, Leh and Kargil.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "india"
# Same mirrors the running application uses. Rotating between them keeps a long
# generation run from leaning on one shared community endpoint.
# Full-planet Overpass instances only. overpass.osm.ch is deliberately NOT here:
# it answers quickly but serves a regional extract, so the India state query
# comes back with one result instead of 36. A mirror that returns *partial* data
# is far more dangerous than one that returns an error, which is why the counts
# are validated below rather than trusted.
MIRRORS = (
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
)
UA = "FloodSafe-SIH26192-Prototype/1.0 (educational prototype)"
# Written after every state so an interrupted or rate-limited run resumes
# instead of starting over. Deleted once the real output files are written.
CHECKPOINT = OUT / ".districts.partial.json"
CAPITAL_CHECKPOINT = OUT / ".capitals.partial.json"

# Aksai Chin and the Sino-Indian boundary dispute mean a state-area query for
# Ladakh also returns Chinese prefectures. Those are not Indian districts and
# must not appear in a district selector.
FOREIGN_ADMIN = re.compile(r"\b(Prefecture|County|Autonomous)\b", re.I)

# OpenStreetMap does NOT reliably distinguish India's union territories from its
# states: `border_type` and `place` are set on only a handful of relations and
# disagree with each other (Chandigarh, a union territory, is tagged
# place=state, and so is Maharashtra). The eight union territories are instead
# identified by their published ISO 3166-2:IN codes, which is a standard rather
# than an invented classification - the same reasoning that lets the risk model
# use IMD's published rainfall class boundaries. 36 - 8 = 28 states.
UNION_TERRITORY_ISO = frozenset({
    "IN-AN",  # Andaman and Nicobar Islands
    "IN-CH",  # Chandigarh
    "IN-DH",  # Dadra and Nagar Haveli and Daman and Diu
    "IN-DL",  # Delhi
    "IN-JK",  # Jammu and Kashmir
    "IN-LA",  # Ladakh
    "IN-LD",  # Lakshadweep
    "IN-PY",  # Puducherry
})

STATE_QUERY = (
    "[out:json][timeout:300];"
    'relation["boundary"="administrative"]["admin_level"="4"]["ISO3166-2"~"^IN-"];'
    "out tags bb;"
)


def overpass(query: str, *, retries: int = 10, offset: int = 0) -> list[dict]:
    """Query Overpass, rotating mirrors and backing off exponentially.

    Overpass answers a sustained run with HTTP 429 fairly quickly, so patience
    here is not optional - it is the difference between a complete dataset and a
    partial one. Backoff runs 15s, 30s, 60s, 120s, 240s.
    """
    last_error: Exception | None = None
    for attempt in range(retries):
        url = MIRRORS[(attempt + offset) % len(MIRRORS)]
        try:
            req = urllib.request.Request(
                url, data=("data=" + query).encode(), headers={"User-Agent": UA}
            )
            with urllib.request.urlopen(req, timeout=600) as response:
                return json.load(response).get("elements", [])
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last_error = exc
            if attempt == retries - 1:
                break
            wait = min(15 * (2 ** attempt), 240)
            print(f"    retry in {wait}s ({exc})", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"Overpass failed after {retries} attempts: {last_error}")


def slug(name: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_"))


def bbox_of(element: dict) -> dict | None:
    bounds = element.get("bounds")
    if not bounds:
        return None
    return {
        "min_lat": round(float(bounds["minlat"]), 5),
        "min_lon": round(float(bounds["minlon"]), 5),
        "max_lat": round(float(bounds["maxlat"]), 5),
        "max_lon": round(float(bounds["maxlon"]), 5),
    }


def centre_of(bbox: dict) -> dict:
    return {
        "latitude": round((bbox["min_lat"] + bbox["max_lat"]) / 2, 5),
        "longitude": round((bbox["min_lon"] + bbox["max_lon"]) / 2, 5),
    }


def inside(bbox: dict, outer: dict, slack: float = 0.35) -> bool:
    centre = centre_of(bbox)
    return (
        outer["min_lat"] - slack <= centre["latitude"] <= outer["max_lat"] + slack
        and outer["min_lon"] - slack <= centre["longitude"] <= outer["max_lon"] + slack
    )


def name_of(tags: dict) -> str:
    return tags.get("name:en") or tags.get("name") or ""


def fetch_states() -> list[dict]:
    print("Fetching states and union territories...", flush=True)
    states = []
    for element in overpass(STATE_QUERY):
        tags = element.get("tags", {})
        bbox = bbox_of(element)
        name = name_of(tags)
        if not bbox or not name:
            continue
        states.append(
            {
                "id": slug(name),
                "name": name,
                "iso_code": tags.get("ISO3166-2", ""),
                "type": "union_territory"
                if tags.get("ISO3166-2", "") in UNION_TERRITORY_ISO
                else "state",
                "bbox": bbox,
                "center": centre_of(bbox),
                "osm_relation_id": element.get("id"),
            }
        )
    states.sort(key=lambda s: s["name"])
    print(f"  {len(states)} states/UTs", flush=True)
    return states


def fetch_districts(states: list[dict]) -> list[dict]:
    """One query per state, so each district's parent is known by construction.

    Assigning districts to states by bbox overlap would be wrong - Indian state
    bboxes overlap heavily (Puducherry's spans half the peninsula).
    """
    done: dict[str, list[dict]] = {}
    if CHECKPOINT.exists():
        done = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
        print(f"  resuming: {len(done)} states already fetched", flush=True)

    for index, state in enumerate(states, 1):
        if state["id"] in done:
            print(f"  [{index}/{len(states)}] {state['name']}... cached", flush=True)
            continue
        print(f"  [{index}/{len(states)}] {state['name']}...", end="", flush=True)
        state_districts: list[dict] = []
        query = (
            f"[out:json][timeout:300];"
            f"relation({state['osm_relation_id']});map_to_area->.s;"
            f'relation(area.s)["boundary"="administrative"]["admin_level"="5"];'
            f"out tags bb;"
        )
        elements = overpass(query)
        if not elements:
            # An empty answer is usually a mirror serving a partial extract, not
            # a state with no districts. Confirm on a different mirror before
            # recording a zero - a silently empty district list would be worse
            # than a crash, because the selector would just look broken.
            print(" empty, confirming on another mirror...", end="", flush=True)
            elements = overpass(query, offset=1)
        kept, dropped = 0, 0
        seen: set[str] = set()
        for element in elements:
            name = name_of(element.get("tags", {}))
            bbox = bbox_of(element)
            if not name or not bbox:
                continue
            if FOREIGN_ADMIN.search(name) or not inside(bbox, state["bbox"]):
                dropped += 1
                continue
            district_slug = slug(name)
            if district_slug in seen:
                continue
            seen.add(district_slug)
            state_districts.append(
                {
                    "id": f"{state['id']}__{district_slug}",
                    "name": name,
                    "state_id": state["id"],
                    "state_name": state["name"],
                    "bbox": bbox,
                    "center": centre_of(bbox),
                    "osm_relation_id": element.get("id"),
                }
            )
            kept += 1
        note = f" ({dropped} outside state dropped)" if dropped else ""
        print(f" {kept} districts{note}", flush=True)
        done[state["id"]] = state_districts
        CHECKPOINT.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
        time.sleep(3.0)  # be a good citizen on a shared community endpoint

    districts = [d for state_list in done.values() for d in state_list]
    districts.sort(key=lambda d: (d["state_name"], d["name"]))
    return districts


def fetch_capitals(states: list[dict]) -> dict[str, dict]:
    """The administrative centre each state relation itself designates.

    A bounding-box centre is a fine representative point for a compact state and
    a badly wrong one for a fragmented union territory: Puducherry's four
    enclaves are spread across South India, so its bbox centre lands in inland
    Andhra Pradesh. Ranking "Puducherry" from a point outside Puducherry would
    be a plain factual error, so the national view uses the real admin centre.

    Assigning capitals to states by coordinate would reintroduce the same bug
    (Puducherry city sits inside Tamil Nadu's bounding box). Relation membership
    is authoritative and unambiguous, so that is what is used.
    """
    done: dict[str, dict] = {}
    if CAPITAL_CHECKPOINT.exists():
        done = json.loads(CAPITAL_CHECKPOINT.read_text(encoding="utf-8"))
        print(f"  resuming: {len(done)} capitals already fetched", flush=True)

    for index, state in enumerate(states, 1):
        if state["id"] in done:
            continue
        print(f"  [{index}/{len(states)}] {state['name']}...", end="", flush=True)
        elements = overpass(
            f"[out:json][timeout:120];"
            f"relation({state['osm_relation_id']});"
            f'node(r:"admin_centre");'
            f"out tags center;"
        )
        node = next((e for e in elements if e.get("lat") is not None), None)
        if node:
            done[state["id"]] = {
                "name": name_of(node.get("tags", {})),
                "latitude": round(float(node["lat"]), 5),
                "longitude": round(float(node["lon"]), 5),
                "source": "osm_admin_centre",
            }
            print(f" {done[state['id']]['name']}", flush=True)
        else:
            # Recorded as a bbox centroid, and labelled as one downstream.
            done[state["id"]] = {
                "name": None,
                "latitude": state["center"]["latitude"],
                "longitude": state["center"]["longitude"],
                "source": "bbox_centroid",
            }
            print(" no admin_centre; using bbox centroid", flush=True)
        CAPITAL_CHECKPOINT.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
        time.sleep(2.0)
    return done


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    generated = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    attribution = "Administrative boundaries (c) OpenStreetMap contributors, ODbL"
    header = {
        "generated_at": generated,
        "source": "OpenStreetMap via Overpass API",
        "attribution": attribution,
    }

    states = fetch_states()
    if len(states) < 30:
        print(f"ERROR: only {len(states)} states returned; refusing to write a partial dataset.")
        return 1

    # Districts are the expensive half of this script (one query per state, and
    # Overpass is often slow). Reuse a complete existing file so re-running to
    # refresh the cheap parts costs a handful of small queries, not an hour.
    existing = OUT / "districts.json"
    districts: list[dict] = []
    if existing.exists() and "--force" not in sys.argv:
        districts = json.loads(existing.read_text(encoding="utf-8")).get("districts", [])
        print(f"Reusing {len(districts)} districts from {existing.name} (--force to refetch)")
    if len(districts) < 500:
        print("Fetching districts (one query per state)...", flush=True)
        districts = fetch_districts(states)
    if len(districts) < 500:
        print(f"ERROR: only {len(districts)} districts returned; refusing to write a partial dataset.")
        return 1

    print("Fetching administrative centres...", flush=True)
    capitals = fetch_capitals(states)
    for state in states:
        state["representative_point"] = capitals.get(state["id"]) or {
            **state["center"], "name": None, "source": "bbox_centroid",
        }
    real = sum(1 for s in states if s["representative_point"]["source"] == "osm_admin_centre")
    print(f"  {real}/{len(states)} states have a real administrative centre", flush=True)

    (OUT / "states.json").write_text(
        json.dumps({**header, "admin_level": 4, "count": len(states), "states": states},
                   indent=1, ensure_ascii=False),
        encoding="utf-8",
    )
    (OUT / "districts.json").write_text(
        json.dumps({**header, "admin_level": 5, "count": len(districts), "districts": districts},
                   indent=1, ensure_ascii=False),
        encoding="utf-8",
    )
    CHECKPOINT.unlink(missing_ok=True)
    CAPITAL_CHECKPOINT.unlink(missing_ok=True)
    for name in ("states.json", "districts.json"):
        print(f"wrote data/india/{name} ({(OUT / name).stat().st_size / 1024:.0f} KB)")
    print(f"\n{len(states)} states/UTs, {len(districts)} districts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
