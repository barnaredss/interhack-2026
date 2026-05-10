"""Geocode addresses in dirs.csv via Photon (komoot, OSM-based), write coords.csv (id,x,y).

Photon is biased with a Catalonia bbox and a Vic lat/lon centroid, which
resolves both Spanish (CALLE/AVENIDA) and Catalan (Carrer/Avinguda) phrasings
transparently. Rows are written incrementally so the script is resumable.
"""
import csv
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).parent
INPUT = ROOT / "dirs.csv"
OUTPUT = ROOT / "coords.csv"

PHOTON_URL = "https://photon.komoot.io/api/"
HEADERS = {"User-Agent": "interhack-2026-geocoder/1.0 (danvancea1235813@gmail.com)"}
RATE_LIMIT_SEC = 0.4

# Catalonia bbox (minLon, minLat, maxLon, maxLat) + Vic centroid for ranking bias.
CATALONIA_BBOX = "0.16,40.5,3.33,42.86"
VIC_LAT, VIC_LON = 41.93, 2.25

SN_PATTERN = re.compile(r"\bS/?N\.?\b", re.IGNORECASE)
HOUSE_NUM_PATTERN = re.compile(r"\s+\d+[A-Za-z]?\s*$")
LEADING_NUM_PATTERN = re.compile(r"^\d+[A-Za-z]?\s+")


def clean_street(calle: str) -> str:
    s = SN_PATTERN.sub("", calle.strip())
    return re.sub(r"\s+", " ", s).strip()


def strip_house_number(street: str) -> str:
    return HOUSE_NUM_PATTERN.sub("", street).strip()


def request_photon(query: str) -> tuple[float, float] | None:
    params = {
        "q": query,
        "limit": 1,
        "bbox": CATALONIA_BBOX,
        "lat": VIC_LAT,
        "lon": VIC_LON,
        "zoom": 14,
        "lang": "default",
    }
    backoff = 5.0
    for attempt in range(5):
        r = requests.get(PHOTON_URL, params=params, headers=HEADERS, timeout=30)
        if r.status_code in (429, 503):
            print(f"  {r.status_code} — sleeping {backoff:.0f}s (attempt {attempt + 1})", flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 120)
            continue
        r.raise_for_status()
        time.sleep(RATE_LIMIT_SEC)
        feats = r.json().get("features") or []
        if not feats:
            return None
        lon, lat = feats[0]["geometry"]["coordinates"]
        return float(lon), float(lat)
    raise requests.HTTPError("Photon — exceeded retries")


def geocode(calle_raw: str, cp: str, poblacion: str) -> tuple[float, float, str] | None:
    cp = cp.strip().zfill(5)
    poblacion = poblacion.strip()
    street = clean_street(calle_raw)

    if street:
        coords = request_photon(f"{street}, {cp} {poblacion}, Spain")
        if coords:
            return (*coords, "full")

    # Drop trailing house number — useful when the number isn't in OSM.
    street_no_num = strip_house_number(street) if street else ""
    if street_no_num and street_no_num != street:
        coords = request_photon(f"{street_no_num}, {cp} {poblacion}, Spain")
        if coords:
            return (*coords, "no-num")

    # Last resort: postal code + city centroid.
    coords = request_photon(f"{cp} {poblacion}, Spain")
    if coords:
        return (*coords, "cp-only")

    return None


def load_done() -> set[str]:
    if not OUTPUT.exists():
        return set()
    done = set()
    with OUTPUT.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if len(row) >= 3 and row[1] and row[2]:
                done.add(row[0])
    return done


def rewrite_keeping_hits() -> None:
    if not OUTPUT.exists():
        return
    with OUTPUT.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return
    header, body = rows[0], rows[1:]
    kept = [r for r in body if len(r) >= 3 and r[1] and r[2]]
    with OUTPUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(kept)


def main() -> int:
    rewrite_keeping_hits()
    done = load_done()
    if done:
        print(f"Resuming: {len(done)} ids already geocoded", flush=True)

    with INPUT.open("r", encoding="latin-1", newline="") as f:
        rows = list(csv.DictReader(f))

    addr_cache: dict[tuple[str, str, str], tuple[float, float] | None] = {}
    write_header = not OUTPUT.exists() or OUTPUT.stat().st_size == 0
    with OUTPUT.open("a", encoding="utf-8", newline="") as out:
        writer = csv.writer(out)
        if write_header:
            writer.writerow(["id", "x", "y"])
            out.flush()

        total = len(rows)
        misses = 0
        for i, row in enumerate(rows, 1):
            cid = row["Cliente"].strip()
            if cid in done:
                continue

            calle = row["Calle"].strip()
            cp = row["CP"].strip()
            poblacion = row["Poblaci\xf3n"].strip()
            key = (calle.lower(), cp, poblacion.lower())

            if key in addr_cache:
                cached = addr_cache[key]
                if cached is None:
                    misses += 1
                    writer.writerow([cid, "", ""])
                else:
                    x, y = cached
                    writer.writerow([cid, f"{x:.7f}", f"{y:.7f}"])
                out.flush()
                continue

            try:
                result = geocode(calle, cp, poblacion)
            except requests.RequestException as e:
                print(f"[{i}/{total}] {cid} ERROR: {e}", flush=True)
                time.sleep(10)
                continue

            if result is None:
                addr_cache[key] = None
                misses += 1
                print(f"[{i}/{total}] {cid} MISS: {calle} | {cp} {poblacion}", flush=True)
                writer.writerow([cid, "", ""])
            else:
                x, y, strategy = result
                addr_cache[key] = (x, y)
                writer.writerow([cid, f"{x:.7f}", f"{y:.7f}"])
                if i % 100 == 0 or strategy != "full":
                    print(f"[{i}/{total}] {cid} -> {x:.5f},{y:.5f} ({strategy})", flush=True)
            out.flush()

        print(f"Done. {misses} misses across {total} rows.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
