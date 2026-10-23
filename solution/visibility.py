#!/usr/bin/env python3
"""Reference implementation for the near-Earth asteroid audit."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path

MU = 398600.4418
EARTH_RADIUS_KM = 6378.137
EARTH_ROTATION_RAD_S = 7.2921150e-5
OBLIQUITY_DEG = 23.439291111
EPS = 1e-9


class Reject(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def number(value, code):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise Reject(code)
    return float(value)


def object_with_keys(value, keys, code="INVALID_STRUCTURE"):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise Reject(code)
    return value


def degrees(value):
    return math.radians(value)


def round6(value):
    rounded = round(float(value), 6)
    return 0.0 if rounded == 0 else rounded


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def angular_distance(first, second):
    cosine = (
        math.sin(degrees(first[1])) * math.sin(degrees(second[1]))
        + math.cos(degrees(first[1])) * math.cos(degrees(second[1])) * math.cos(degrees((first[0] - second[0] + 180) % 360 - 180))
    )
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def parse_scene(raw):
    raw = object_with_keys(raw, ["orbit", "epoch", "stations", "sun", "window", "limits"])
    if number(raw["epoch"], "INVALID_EPOCH") != 0:
        raise Reject("INVALID_EPOCH")
    orbit = object_with_keys(raw["orbit"], ["a_km", "e", "i_deg", "raan_deg", "arg_periapsis_deg", "mean_anomaly_deg"], "INVALID_ORBIT")
    a = number(orbit["a_km"], "INVALID_ORBIT")
    eccentricity = number(orbit["e"], "INVALID_ORBIT")
    inclination = number(orbit["i_deg"], "INVALID_ORBIT")
    raan = number(orbit["raan_deg"], "INVALID_ORBIT")
    argument = number(orbit["arg_periapsis_deg"], "INVALID_ORBIT")
    anomaly = number(orbit["mean_anomaly_deg"], "INVALID_ORBIT")
    if not (a > EARTH_RADIUS_KM and 0 <= eccentricity < 1 and 0 <= inclination <= 180 and all(0 <= value < 360 for value in (raan, argument, anomaly))):
        raise Reject("INVALID_ORBIT")
    if a * (1 - eccentricity) <= EARTH_RADIUS_KM:
        raise Reject("INVALID_ORBIT")
    if not isinstance(raw["stations"], list) or not 1 <= len(raw["stations"]) <= 8:
        raise Reject("INVALID_STATIONS")
    stations, station_ids = [], set()
    for item in raw["stations"]:
        station = object_with_keys(item, ["id", "latitude_deg", "longitude_deg", "height_km", "sidereal_zero_deg"], "INVALID_STATION")
        station_id = station["id"]
        if not isinstance(station_id, str) or not station_id or station_id in station_ids:
            raise Reject("INVALID_STATION")
        latitude = number(station["latitude_deg"], "INVALID_STATION")
        longitude = number(station["longitude_deg"], "INVALID_STATION")
        height = number(station["height_km"], "INVALID_STATION")
        sidereal_zero = number(station["sidereal_zero_deg"], "INVALID_STATION")
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180 and -0.5 <= height <= 10 and 0 <= sidereal_zero < 360):
            raise Reject("INVALID_STATION")
        station_ids.add(station_id)
        stations.append((station_id, latitude, longitude, height, sidereal_zero))
    sun = object_with_keys(raw["sun"], ["ra_deg", "dec_deg"], "INVALID_SUN")
    sun_ra, sun_dec = number(sun["ra_deg"], "INVALID_SUN"), number(sun["dec_deg"], "INVALID_SUN")
    if not (0 <= sun_ra < 360 and -90 <= sun_dec <= 90):
        raise Reject("INVALID_SUN")
    window = object_with_keys(raw["window"], ["start_s", "end_s", "cadence_s"], "INVALID_WINDOW")
    start, end, cadence = (number(window[key], "INVALID_WINDOW") for key in ("start_s", "end_s", "cadence_s"))
    if not (start >= 0 and end > start and cadence > 0):
        raise Reject("INVALID_WINDOW")
    limits = object_with_keys(raw["limits"], ["min_apparent_altitude_deg", "sun_avoidance_deg", "risk_distance_km", "min_track_s"], "INVALID_LIMITS")
    min_alt, solar_avoid, risk_distance, min_track = (number(limits[key], "INVALID_LIMITS") for key in ("min_apparent_altitude_deg", "sun_avoidance_deg", "risk_distance_km", "min_track_s"))
    if not (-5 <= min_alt <= 90 and 0 <= solar_avoid <= 180 and risk_distance > EARTH_RADIUS_KM and min_track >= 0):
        raise Reject("INVALID_LIMITS")
    return (a, eccentricity, inclination, raan, argument, anomaly), stations, (sun_ra, sun_dec), (start, end, cadence), (min_alt, solar_avoid, risk_distance, min_track)


def sample_times(start, end, cadence):
    result, index = [start], 1
    while start + index * cadence < end - EPS:
        result.append(start + index * cadence)
        index += 1
    if abs(result[-1] - end) > EPS:
        result.append(end)
    return result


def propagate(orbit, time_s):
    a, eccentricity, inclination, raan, argument, anomaly = orbit
    mean = (degrees(anomaly) + math.sqrt(MU / a**3) * time_s) % (2 * math.pi)
    eccentric = mean
    for _ in range(20):
        denominator = 1 - eccentricity * math.cos(eccentric)
        if abs(denominator) < 1e-14:
            raise Reject("KEPLER")
        delta = (eccentric - eccentricity * math.sin(eccentric) - mean) / denominator
        eccentric -= delta
        if abs(delta) <= 1e-12:
            break
    else:
        raise Reject("KEPLER")
    x_plane = a * (math.cos(eccentric) - eccentricity)
    y_plane = a * math.sqrt(1 - eccentricity**2) * math.sin(eccentric)
    omega, inc, node = degrees(argument), degrees(inclination), degrees(raan)
    x_ecliptic = (math.cos(node) * math.cos(omega) - math.sin(node) * math.sin(omega) * math.cos(inc)) * x_plane + (-math.cos(node) * math.sin(omega) - math.sin(node) * math.cos(omega) * math.cos(inc)) * y_plane
    y_ecliptic = (math.sin(node) * math.cos(omega) + math.cos(node) * math.sin(omega) * math.cos(inc)) * x_plane + (-math.sin(node) * math.sin(omega) + math.cos(node) * math.cos(omega) * math.cos(inc)) * y_plane
    z_ecliptic = math.sin(omega) * math.sin(inc) * x_plane + math.cos(omega) * math.sin(inc) * y_plane
    obliquity = degrees(OBLIQUITY_DEG)
    return (x_ecliptic, y_ecliptic * math.cos(obliquity) - z_ecliptic * math.sin(obliquity), y_ecliptic * math.sin(obliquity) + z_ecliptic * math.cos(obliquity))


def norm(vector):
    return math.sqrt(sum(component * component for component in vector))


def station_position(station, time_s):
    _, latitude, longitude, height, sidereal_zero = station
    theta = degrees(sidereal_zero + longitude) + EARTH_ROTATION_RAD_S * time_s
    latitude_radians = degrees(latitude)
    radius = EARTH_RADIUS_KM + height
    return (radius * math.cos(latitude_radians) * math.cos(theta), radius * math.cos(latitude_radians) * math.sin(theta), radius * math.sin(latitude_radians))


def occulted(station, line_of_sight):
    dot = sum(first * second for first, second in zip(station, line_of_sight))
    if dot >= 0:
        return False
    length_sq = sum(component * component for component in line_of_sight)
    parameter = -dot / length_sq
    if not 0 < parameter < 1:
        return False
    closest = tuple(first + parameter * second for first, second in zip(station, line_of_sight))
    return norm(closest) < EARTH_RADIUS_KM



def observation(position, station, time_s, sun):
    observer = station_position(station, time_s)
    line_of_sight = tuple(target - origin for target, origin in zip(position, observer))
    distance = norm(line_of_sight)
    direction = tuple(component / distance for component in line_of_sight)
    zenith = tuple(component / norm(observer) for component in observer)
    geometric_altitude = math.degrees(math.asin(max(-1.0, min(1.0, sum(first * second for first, second in zip(direction, zenith))))))
    hidden = occulted(observer, line_of_sight)
    if geometric_altitude > -1:
        apparent_altitude = geometric_altitude + 1.02 / math.tan(degrees(geometric_altitude + 10.3 / (geometric_altitude + 5.11))) / 60
    else:
        apparent_altitude = geometric_altitude
    right_ascension = math.degrees(math.atan2(direction[1], direction[0])) % 360
    declination = math.degrees(math.asin(max(-1.0, min(1.0, direction[2]))))
    return hidden, apparent_altitude, distance, angular_distance((right_ascension, declination), sun)


def build_windows(samples, min_track):
    windows, active = [], None
    for time_s, altitude, distance, visible in samples:
        if visible:
            if active is None:
                active = {"start_s": time_s, "end_s": time_s, "peak_apparent_altitude_deg": altitude, "min_station_distance_km": distance, "indices": [time_s]}
            else:
                active["end_s"] = time_s
                active["peak_apparent_altitude_deg"] = max(active["peak_apparent_altitude_deg"], altitude)
                active["min_station_distance_km"] = min(active["min_station_distance_km"], distance)
                active["indices"].append(time_s)
        elif active is not None:
            windows.append(active)
            active = None
    if active is not None:
        windows.append(active)
    qualified_times = set()
    public = []
    for window in windows:
        duration = window["end_s"] - window["start_s"]
        if duration + EPS >= min_track:
            qualified_times.update(window["indices"])
        public.append({"start_s": round6(window["start_s"]), "end_s": round6(window["end_s"]), "duration_s": round6(duration), "peak_apparent_altitude_deg": round6(window["peak_apparent_altitude_deg"]), "min_station_distance_km": round6(window["min_station_distance_km"])})
    status = "NOT_VISIBLE" if not public else ("TRACKABLE" if qualified_times else "BRIEF")
    return public, status, qualified_times


def read_ledger(path):
    if not path.exists():
        return []
    try:
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        for event in events:
            if set(event) != {"fingerprint", "closest_distance_km", "earth_risk_status", "union_status"}:
                raise ValueError
            if not isinstance(event["fingerprint"], str) or not isinstance(event["earth_risk_status"], str) or not isinstance(event["union_status"], str):
                raise ValueError
            if isinstance(event["closest_distance_km"], bool) or not isinstance(event["closest_distance_km"], (int, float)):
                raise ValueError
        return events
    except Exception as error:
        raise Reject("LEDGER_READ") from error


def stage(destination, contents):
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".asteroid-audit-", suffix=".tmp", dir=destination.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        return temporary
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def commit(report_path, ledger_path, report, fingerprint):
    if any(path.exists() and not path.is_file() for path in (report_path, ledger_path)):
        raise Reject("IO")
    events = read_ledger(ledger_path)
    if fingerprint not in {event["fingerprint"] for event in events}:
        events.append({"fingerprint": fingerprint, "closest_distance_km": report["closest_distance_km"], "earth_risk_status": report["earth_risk_status"], "union_status": report["union_status"]})
    pending = []
    try:
        pending.append((stage(report_path, (canonical(report) + "\n").encode("utf-8")), report_path))
        pending.append((stage(ledger_path, "".join(canonical(event) + "\n" for event in events).encode("utf-8")), ledger_path))
        for temporary, destination in pending:
            os.replace(temporary, destination)
    except Exception as error:
        for temporary, _ in pending:
            temporary.unlink(missing_ok=True)
        raise Reject("IO") from error


def assess(raw):
    orbit, stations, sun, window, limits = parse_scene(raw)
    start, end, cadence = window
    min_altitude, solar_avoidance, risk_distance, min_track = limits
    times = sample_times(start, end, cadence)
    risk_times = sorted(set(times + [(left + right) / 2 for left, right in zip(times, times[1:])]))
    closest_time, closest_distance = min(((time_s, norm(propagate(orbit, time_s))) for time_s in risk_times), key=lambda item: (item[1], item[0]))
    risk_status = "IMPACT_RISK" if closest_distance <= EARTH_RADIUS_KM + EPS else ("CLOSE_APPROACH" if closest_distance <= risk_distance + EPS else "CLEAR")
    station_results, qualified_by_time = [], {}
    for station in sorted(stations, key=lambda item: item[0]):
        samples = []
        for time_s in times:
            hidden, altitude, distance, separation = observation(propagate(orbit, time_s), station, time_s, sun)
            samples.append((time_s, altitude, distance, not hidden and altitude + EPS >= min_altitude and separation + EPS >= solar_avoidance))
        windows, status, qualified = build_windows(samples, min_track)
        station_results.append({"id": station[0], "status": status, "windows": windows})
        for time_s in qualified:
            qualified_by_time.setdefault(time_s, []).append(station)
    stereo = any(any(abs(first[2] - second[2]) >= 30 for index, first in enumerate(items) for second in items[index + 1:]) for items in qualified_by_time.values())
    if stereo:
        union_status = "STEREO_TRACKABLE"
    elif any(result["status"] == "TRACKABLE" for result in station_results):
        union_status = "SINGLE_STATION_TRACKABLE"
    elif any(result["status"] == "BRIEF" for result in station_results):
        union_status = "BRIEF_ONLY"
    else:
        union_status = "NO_TRACK"
    return {"closest_approach_s": round6(closest_time), "closest_distance_km": round6(closest_distance), "earth_risk_status": risk_status, "sample_times_s": [round6(value) for value in times], "stations": station_results, "union_status": union_status}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--ledger", required=True)
    args = parser.parse_args(argv)
    try:
        raw_bytes = Path(args.input).read_bytes()
        raw = json.loads(raw_bytes.decode("utf-8"))
        report = assess(raw)
        fingerprint = hashlib.sha256(canonical(raw).encode("utf-8")).hexdigest()
        commit(Path(args.output), Path(args.ledger), report, fingerprint)
        return 0
    except Reject as error:
        print(f"REJECT {error.code}", file=sys.stderr)
        return 2
    except Exception:
        print("REJECT IO", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
