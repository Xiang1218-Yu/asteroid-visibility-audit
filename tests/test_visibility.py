import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

PROGRAM = Path(os.environ.get("PROGRAM_DIR", "/app")) / "visibility.py"


def scene(**changes):
    payload = {
        "orbit": {"a_km": 12000.0, "e": 0.2, "i_deg": 0.0, "raan_deg": 0.0, "arg_periapsis_deg": 0.0, "mean_anomaly_deg": 0.0},
        "epoch": 0,
        "stations": [{"id": "ALPHA", "latitude_deg": 0.0, "longitude_deg": 0.0, "height_km": 0.0, "sidereal_zero_deg": 0.0}],
        "sun": {"ra_deg": 180.0, "dec_deg": 0.0},
        "window": {"start_s": 0.0, "end_s": 1.0, "cadence_s": 5.0},
        "limits": {"min_apparent_altitude_deg": -5.0, "sun_avoidance_deg": 1.0, "risk_distance_km": 10000.0, "min_track_s": 0.0},
    }
    payload.update(changes)
    return payload


def invoke(payload, report_bytes=None, ledger_bytes=None, ledger_name="ledger.jsonl"):
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        input_path = directory / "in.json"
        report_path = directory / "report.json"
        ledger_path = directory / ledger_name
        input_path.write_text(json.dumps(payload), encoding="utf-8")
        if report_bytes is not None:
            report_path.write_bytes(report_bytes)
        if ledger_bytes is not None:
            ledger_path.write_bytes(ledger_bytes)
        process = subprocess.run(
            [sys.executable, str(PROGRAM), "--input", str(input_path), "--output", str(report_path), "--ledger", str(ledger_path)],
            capture_output=True,
            text=True,
        )
        report = report_path.read_bytes() if report_path.exists() and report_path.is_file() else None
        ledger = ledger_path.read_bytes() if ledger_path.exists() and ledger_path.is_file() else None
        return process, report, ledger, sorted(path.name for path in directory.iterdir())


def test_kepler_propagation_uses_eccentric_periapsis_distance():
    """R2：偏心椭圆在近地点的距离必须由开普勒传播而非半长轴代替。"""
    process, report_bytes, _, _ = invoke(scene())
    assert process.returncode == 0, process.stderr
    report = json.loads(report_bytes)
    assert report["closest_approach_s"] == 0.0
    assert report["closest_distance_km"] == pytest.approx(9600.0)
    assert report["earth_risk_status"] == "CLOSE_APPROACH"


def test_midpoint_risk_candidate_detects_between_sample_close_approach():
    """R3：风险审计必须纳入相邻正式样本间的中点。"""
    payload = scene(
        orbit={"a_km": 10000.0, "e": 0.3, "i_deg": 0.0, "raan_deg": 0.0, "arg_periapsis_deg": 0.0, "mean_anomaly_deg": 323.825},
        window={"start_s": 0.0, "end_s": 2000.0, "cadence_s": 2000.0},
        limits={"min_apparent_altitude_deg": -5.0, "sun_avoidance_deg": 1.0, "risk_distance_km": 7200.0, "min_track_s": 0.0},
    )
    process, report_bytes, _, _ = invoke(payload)
    assert process.returncode == 0, process.stderr
    report = json.loads(report_bytes)
    assert report["sample_times_s"] == [0.0, 2000.0]
    assert report["closest_approach_s"] == pytest.approx(1000.0)
    assert report["closest_distance_km"] == pytest.approx(7000.0, abs=0.001)
    assert report["earth_risk_status"] == "CLOSE_APPROACH"


def test_inclination_rotation_and_obliquity_control_station_altitude():
    """R2/R4：三维轨道姿态与黄赤转换必须共同决定高纬台站高度角。"""
    payload = scene(
        orbit={"a_km": 1000000.0, "e": 0.0, "i_deg": 40.0, "raan_deg": 0.0, "arg_periapsis_deg": 90.0, "mean_anomaly_deg": 0.0},
        stations=[{"id": "POLAR", "latitude_deg": 63.439291111, "longitude_deg": 0.0, "height_km": 0.0, "sidereal_zero_deg": 90.0}],
        sun={"ra_deg": 270.0, "dec_deg": 0.0},
        limits={"min_apparent_altitude_deg": 88.0, "sun_avoidance_deg": 1.0, "risk_distance_km": 10000.0, "min_track_s": 0.0},
    )
    process, report_bytes, _, _ = invoke(payload)
    assert process.returncode == 0, process.stderr
    report = json.loads(report_bytes)
    assert report["stations"][0]["status"] == "TRACKABLE"
    assert report["stations"][0]["windows"][0]["peak_apparent_altitude_deg"] == pytest.approx(89.999968)


def test_earth_occultation_overrides_permissive_horizon_limit():
    """R4：几何高度仍在宽松门槛内时，地球遮挡必须优先拒绝该视线。"""
    payload = scene(
        orbit={"a_km": 1000000.0, "e": 0.0, "i_deg": 0.0, "raan_deg": 0.0, "arg_periapsis_deg": 0.0, "mean_anomaly_deg": 90.3},
        stations=[{"id": "HORIZON", "latitude_deg": 0.0, "longitude_deg": 0.0, "height_km": 0.0, "sidereal_zero_deg": 0.0}],
    )
    process, report_bytes, _, _ = invoke(payload)
    assert process.returncode == 0, process.stderr
    report = json.loads(report_bytes)
    assert report["stations"][0]["status"] == "NOT_VISIBLE"
    assert report["stations"][0]["windows"] == []


def test_near_horizon_refraction_can_meet_apparent_altitude_limit():
    """R4：未遮挡的近地平目标应以表观高度而非几何高度判定。"""
    payload = scene(
        orbit={"a_km": 1000000.0, "e": 0.0, "i_deg": 0.0, "raan_deg": 0.0, "arg_periapsis_deg": 0.0, "mean_anomaly_deg": 89.5},
        limits={"min_apparent_altitude_deg": 0.55, "sun_avoidance_deg": 1.0, "risk_distance_km": 10000.0, "min_track_s": 0.0},
    )
    process, report_bytes, _, _ = invoke(payload)
    assert process.returncode == 0, process.stderr
    report = json.loads(report_bytes)
    assert report["stations"][0]["status"] == "TRACKABLE"
    assert report["stations"][0]["windows"][0]["peak_apparent_altitude_deg"] == pytest.approx(0.601777)


def test_solar_avoidance_rejects_an_otherwise_high_target():
    """R4：太阳角距不足时，高高度目标也不得进入跟踪窗口。"""
    process, report_bytes, _, _ = invoke(scene(sun={"ra_deg": 0.0, "dec_deg": 0.0}))
    assert process.returncode == 0, process.stderr
    report = json.loads(report_bytes)
    assert report["stations"][0]["status"] == "NOT_VISIBLE"
    assert report["union_status"] == "NO_TRACK"


def test_final_nonintegral_sampling_endpoint_is_included():
    """R3：不能整除步长的结束时刻仍必须作为正式观测样本。"""
    process, report_bytes, _, _ = invoke(scene(window={"start_s": 0.0, "end_s": 25.0, "cadence_s": 10.0}))
    assert process.returncode == 0, process.stderr
    report = json.loads(report_bytes)
    assert report["sample_times_s"] == [0.0, 10.0, 20.0, 25.0]


def test_short_visible_window_is_brief_not_trackable():
    """R5：窗口存在但时长不足时必须形成 BRIEF 状态。"""
    process, report_bytes, _, _ = invoke(scene(limits={"min_apparent_altitude_deg": -5.0, "sun_avoidance_deg": 1.0, "risk_distance_km": 10000.0, "min_track_s": 2.0}))
    assert process.returncode == 0, process.stderr
    report = json.loads(report_bytes)
    assert report["stations"][0]["status"] == "BRIEF"
    assert report["union_status"] == "BRIEF_ONLY"


def test_widely_separated_qualified_stations_enable_stereo_tracking():
    """R5：同刻合格且经度差足够的两个台站应形成联合立体跟踪。"""
    payload = scene(
        orbit={"a_km": 1000000.0, "e": 0.0, "i_deg": 0.0, "raan_deg": 0.0, "arg_periapsis_deg": 0.0, "mean_anomaly_deg": 40.0},
        stations=[
            {"id": "A", "latitude_deg": 0.0, "longitude_deg": 0.0, "height_km": 0.0, "sidereal_zero_deg": 0.0},
            {"id": "B", "latitude_deg": 0.0, "longitude_deg": 80.0, "height_km": 0.0, "sidereal_zero_deg": 0.0},
        ],
        limits={"min_apparent_altitude_deg": 10.0, "sun_avoidance_deg": 1.0, "risk_distance_km": 10000.0, "min_track_s": 0.0},
    )
    process, report_bytes, _, _ = invoke(payload)
    assert process.returncode == 0, process.stderr
    report = json.loads(report_bytes)
    assert [station["status"] for station in report["stations"]] == ["TRACKABLE", "TRACKABLE"]
    assert report["union_status"] == "STEREO_TRACKABLE"


def test_narrow_station_separation_is_not_stereo_tracking():
    """R5：台站均可跟踪但经度差不足时只能形成单台站联合状态。"""
    payload = scene(
        orbit={"a_km": 1000000.0, "e": 0.0, "i_deg": 0.0, "raan_deg": 0.0, "arg_periapsis_deg": 0.0, "mean_anomaly_deg": 10.0},
        stations=[
            {"id": "A", "latitude_deg": 0.0, "longitude_deg": 0.0, "height_km": 0.0, "sidereal_zero_deg": 0.0},
            {"id": "B", "latitude_deg": 0.0, "longitude_deg": 20.0, "height_km": 0.0, "sidereal_zero_deg": 0.0},
        ],
        limits={"min_apparent_altitude_deg": 10.0, "sun_avoidance_deg": 1.0, "risk_distance_km": 10000.0, "min_track_s": 0.0},
    )
    process, report_bytes, _, _ = invoke(payload)
    assert process.returncode == 0, process.stderr
    report = json.loads(report_bytes)
    assert report["union_status"] == "SINGLE_STATION_TRACKABLE"


def test_duplicate_station_identifier_is_rejected_without_side_effects():
    """R1/R6：重复台站标识必须拒收且已有两个目标保持不变。"""
    payload = scene(stations=[
        {"id": "DUP", "latitude_deg": 0.0, "longitude_deg": 0.0, "height_km": 0.0, "sidereal_zero_deg": 0.0},
        {"id": "DUP", "latitude_deg": 10.0, "longitude_deg": 20.0, "height_km": 0.0, "sidereal_zero_deg": 0.0},
    ])
    process, report, ledger, files = invoke(payload, b"prior-report\n", b"prior-ledger\n")
    assert process.returncode != 0
    assert process.stderr.splitlines()[0] == "REJECT INVALID_STATION"
    assert report == b"prior-report\n"
    assert ledger == b"prior-ledger\n"
    assert files == ["in.json", "ledger.jsonl", "report.json"]


def test_earth_intersecting_orbit_is_rejected_without_side_effects():
    """R1：近地点进入地球的输入轨道必须拒收且不得改写观测产物。"""
    payload = scene(orbit={"a_km": 7000.0, "e": 0.1, "i_deg": 0.0, "raan_deg": 0.0, "arg_periapsis_deg": 0.0, "mean_anomaly_deg": 0.0})
    process, report, ledger, files = invoke(payload, b"prior-report\n", b"prior-ledger\n")
    assert process.returncode != 0
    assert process.stderr.splitlines()[0] == "REJECT INVALID_ORBIT"
    assert report == b"prior-report\n"
    assert ledger == b"prior-ledger\n"
    assert files == ["in.json", "ledger.jsonl", "report.json"]


def test_compact_sorted_output_has_required_public_fields():
    """R6：成功报告必须使用稳定紧凑 JSON 并且只含公开字段。"""
    process, report_bytes, _, _ = invoke(scene())
    assert process.returncode == 0, process.stderr
    report = json.loads(report_bytes)
    assert set(report) == {"closest_approach_s", "closest_distance_km", "earth_risk_status", "sample_times_s", "stations", "union_status"}
    assert report_bytes == json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    assert report_bytes.endswith(b"\n")


def test_same_canonical_input_deduplicates_ledger_event():
    """R6：相同规范输入重复执行时账本只能保留一个事件。"""
    payload = scene()
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        input_path = directory / "in.json"
        report_path = directory / "report.json"
        ledger_path = directory / "ledger.jsonl"
        command = [sys.executable, str(PROGRAM), "--input", str(input_path), "--output", str(report_path), "--ledger", str(ledger_path)]
        input_path.write_text(json.dumps(payload), encoding="utf-8")
        first = subprocess.run(command, capture_output=True, text=True)
        assert first.returncode == 0, first.stderr
        second = subprocess.run(command, capture_output=True, text=True)
        assert second.returncode == 0, second.stderr
        events = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
        expected = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        assert len(events) == 1
        assert events[0]["fingerprint"] == expected


def test_corrupt_ledger_is_rejected_without_side_effects():
    """R6：无法解析的既有账本必须拒收并保持报告、账本和目录不变。"""
    process, report, ledger, files = invoke(scene(), b"prior-report\n", b"not-json\n")
    assert process.returncode != 0
    assert process.stderr.splitlines()[0] == "REJECT LEDGER_READ"
    assert report == b"prior-report\n"
    assert ledger == b"not-json\n"
    assert files == ["in.json", "ledger.jsonl", "report.json"]


def test_directory_ledger_target_is_rejected_without_partial_report_update():
    """R6：账本目标为目录时不得先替换报告或遗留临时文件。"""
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        input_path = directory / "in.json"
        report_path = directory / "report.json"
        ledger_path = directory / "ledger-target"
        input_path.write_text(json.dumps(scene()), encoding="utf-8")
        report_path.write_bytes(b"prior-report\n")
        ledger_path.mkdir()
        (ledger_path / "sentinel.txt").write_bytes(b"prior-ledger-directory\n")
        process = subprocess.run([sys.executable, str(PROGRAM), "--input", str(input_path), "--output", str(report_path), "--ledger", str(ledger_path)], capture_output=True, text=True)
        assert process.returncode != 0
        assert process.stderr.splitlines()[0] == "REJECT IO"
        assert report_path.read_bytes() == b"prior-report\n"
        assert (ledger_path / "sentinel.txt").read_bytes() == b"prior-ledger-directory\n"
        assert sorted(path.name for path in directory.iterdir()) == ["in.json", "ledger-target", "report.json"]
        assert not [path for path in directory.iterdir() if path.name.startswith(".asteroid-audit-")]


def test_repeated_success_is_byte_stable_and_leaves_no_temp_files():
    """R6：相同合法输入重复成功后报告、账本和目录清单必须稳定。"""
    payload = scene()
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        input_path = directory / "in.json"
        report_path = directory / "report.json"
        ledger_path = directory / "ledger.jsonl"
        input_path.write_text(json.dumps(payload), encoding="utf-8")
        command = [sys.executable, str(PROGRAM), "--input", str(input_path), "--output", str(report_path), "--ledger", str(ledger_path)]
        first = subprocess.run(command, capture_output=True, text=True)
        assert first.returncode == 0, first.stderr
        before = (report_path.read_bytes(), ledger_path.read_bytes())
        second = subprocess.run(command, capture_output=True, text=True)
        assert second.returncode == 0, second.stderr
        assert (report_path.read_bytes(), ledger_path.read_bytes()) == before
        assert not [path for path in directory.iterdir() if path.name.startswith(".asteroid-audit-")]
