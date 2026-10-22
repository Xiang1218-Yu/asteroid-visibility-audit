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
        "observer": {"id": "OBS-7", "latitude_deg": 0.0, "longitude_deg": 0.0, "sidereal_zero_deg": 0.0},
        "target": {"ra_deg": 0.0, "dec_deg": 0.0},
        "sun": {"ra_deg": 180.0, "dec_deg": 0.0},
        "window": {"start_s": 0.0, "end_s": 7200.0, "cadence_s": 1800.0},
        "limits": {"min_altitude_deg": 20.0, "sun_avoidance_deg": 30.0, "min_window_s": 1800.0},
    }
    payload.update(changes)
    return payload


def invoke(payload, report=b"old-report\n", ledger=b"old-ledger\n"):
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        input_path = directory / "in.json"
        report_path = directory / "report.json"
        ledger_path = directory / "ledger.jsonl"
        input_path.write_text(json.dumps(payload), encoding="utf-8")
        report_path.write_bytes(report)
        ledger_path.write_bytes(ledger)
        process = subprocess.run(
            [sys.executable, str(PROGRAM), "--input", str(input_path), "--output", str(report_path), "--ledger", str(ledger_path)],
            capture_output=True,
            text=True,
        )
        return process, report_path.read_bytes(), ledger_path.read_bytes(), sorted(path.name for path in directory.iterdir())


def test_sidereal_rotation_changes_visibility_within_window():
    """R2：每一采样时刻必须按恒星时重新计算仰角。"""
    process, raw, _, _ = invoke(
        scene(
            window={"start_s": 0.0, "end_s": 21600.0, "cadence_s": 10800.0},
            limits={"min_altitude_deg": 60.0, "sun_avoidance_deg": 1.0, "min_window_s": 0.0},
        ),
        b"",
        b"",
    )
    assert process.returncode == 0, process.stderr
    report = json.loads(raw)
    assert report["sample_times_s"] == [0.0, 10800.0, 21600.0]
    assert report["status"] == "OBSERVABLE"
    assert [(window["start_s"], window["end_s"]) for window in report["windows"]] == [(0.0, 0.0)]


def test_spherical_altitude_uses_latitude_declination_and_hour_angle():
    """R2：球面三角中的台站纬度、赤纬和小时角不可省略。"""
    process, raw, _, _ = invoke(
        scene(
            observer={"id": "N", "latitude_deg": 60.0, "longitude_deg": 0.0, "sidereal_zero_deg": 0.0},
            target={"ra_deg": 0.0, "dec_deg": 30.0},
            window={"start_s": 0.0, "end_s": 1.0, "cadence_s": 5.0},
            limits={"min_altitude_deg": 59.9, "sun_avoidance_deg": 1.0, "min_window_s": 0.0},
        ),
        b"",
        b"",
    )
    assert process.returncode == 0, process.stderr
    report = json.loads(raw)
    assert report["status"] == "OBSERVABLE"
    assert report["windows"][0]["peak_altitude_deg"] == pytest.approx(60.0)


def test_solar_avoidance_blocks_high_altitude_target():
    """R3：高仰角目标也必须服从太阳避让角。"""
    process, raw, _, _ = invoke(
        scene(sun={"ra_deg": 2.0, "dec_deg": 0.0}, limits={"min_altitude_deg": -90.0, "sun_avoidance_deg": 5.0, "min_window_s": 0.0}),
        b"",
        b"",
    )
    assert process.returncode == 0, process.stderr
    report = json.loads(raw)
    assert report["sun_separation_deg"] == pytest.approx(2.0)
    assert report["status"] == "NOT_VISIBLE"
    assert report["windows"] == []


def test_equality_at_altitude_and_solar_threshold_is_visible():
    """R2/R3：两个资格门槛的相等边界均为可见。"""
    process, raw, _, _ = invoke(
        scene(
            sun={"ra_deg": 30.0, "dec_deg": 0.0},
            limits={"min_altitude_deg": 90.0, "sun_avoidance_deg": 30.0, "min_window_s": 0.0},
            window={"start_s": 0.0, "end_s": 1.0, "cadence_s": 4.0},
        ),
        b"",
        b"",
    )
    assert process.returncode == 0, process.stderr
    report = json.loads(raw)
    assert report["status"] == "OBSERVABLE"
    assert report["windows"][0]["duration_s"] == pytest.approx(0.0)


def test_final_irregular_sample_and_visibility_gap_split_windows():
    """R4：不整除终点必须采样，且不可见样本必须分割窗口。"""
    process, raw, _, _ = invoke(
        scene(
            window={"start_s": 0.0, "end_s": 86164.0905, "cadence_s": 40000.0},
            limits={"min_altitude_deg": 80.0, "sun_avoidance_deg": 1.0, "min_window_s": 0.0},
        ),
        b"",
        b"",
    )
    assert process.returncode == 0, process.stderr
    report = json.loads(raw)
    assert report["sample_times_s"] == pytest.approx([0.0, 40000.0, 80000.0, 86164.0905])
    assert [(window["start_s"], window["end_s"]) for window in report["windows"]] == pytest.approx([(0.0, 0.0), (86164.0905, 86164.0905)])


def test_minimum_window_yields_brief_status():
    """R4：最长窗口不足时状态应为 BRIEF 而非可观测。"""
    process, raw, _, _ = invoke(
        scene(
            window={"start_s": 0.0, "end_s": 7200.0, "cadence_s": 3600.0},
            limits={"min_altitude_deg": 80.0, "sun_avoidance_deg": 1.0, "min_window_s": 3600.0},
        ),
        b"",
        b"",
    )
    assert process.returncode == 0, process.stderr
    report = json.loads(raw)
    assert report["status"] == "BRIEF"
    assert report["windows"][0]["duration_s"] == pytest.approx(0.0)


def test_boolean_and_extra_key_rejection_preserve_two_outputs():
    """R1/R7：布尔伪数值和额外键拒收时不能污染报告或账本。"""
    boolean_payload = scene()
    boolean_payload["window"]["cadence_s"] = True
    process, report, ledger, files = invoke(boolean_payload)
    assert process.returncode != 0
    assert process.stderr.splitlines()[0].startswith("REJECT ")
    assert report == b"old-report\n"
    assert ledger == b"old-ledger\n"
    assert files == ["in.json", "ledger.jsonl", "report.json"]
    extra_key_payload = scene()
    extra_key_payload["target"]["unexpected"] = 1
    process, report, ledger, files = invoke(extra_key_payload)
    assert process.returncode != 0
    assert process.stderr.splitlines()[0].startswith("REJECT ")
    assert report == b"old-report\n"
    assert ledger == b"old-ledger\n"
    assert files == ["in.json", "ledger.jsonl", "report.json"]


def test_nonfinite_and_range_rejection_preserve_two_outputs():
    """R1/R7：非有限数及越界角度必须无副作用拒收。"""
    nonfinite_payload = scene()
    nonfinite_payload["observer"]["latitude_deg"] = float("nan")
    process, report, ledger, files = invoke(nonfinite_payload)
    assert process.returncode != 0
    assert process.stderr.splitlines()[0].startswith("REJECT ")
    assert report == b"old-report\n"
    assert ledger == b"old-ledger\n"
    assert files == ["in.json", "ledger.jsonl", "report.json"]
    range_payload = scene()
    range_payload["sun"]["ra_deg"] = 360.0
    process, report, ledger, files = invoke(range_payload)
    assert process.returncode != 0
    assert process.stderr.splitlines()[0].startswith("REJECT ")
    assert report == b"old-report\n"
    assert ledger == b"old-ledger\n"
    assert files == ["in.json", "ledger.jsonl", "report.json"]


def test_report_is_compact_sorted_lf_and_rounded():
    """R5：成功报告的字段、紧凑排序、换行和精度均是公共契约。"""
    process, raw, _, _ = invoke(scene(target={"ra_deg": 0.0, "dec_deg": 10.0}, sun={"ra_deg": 123.456789, "dec_deg": -12.345678}), b"", b"")
    assert process.returncode == 0, process.stderr
    report = json.loads(raw)
    assert set(report) == {"observer_id", "sample_times_s", "status", "sun_separation_deg", "windows"}
    assert raw == json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    assert b", " not in raw
    assert raw.endswith(b"\n")


def test_same_input_deduplicates_canonical_ledger_event():
    """R6：同一规范输入的账本事件必须幂等。"""
    payload = scene()
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        input_path = directory / "in.json"
        report_path = directory / "out.json"
        ledger_path = directory / "ledger.jsonl"
        input_path.write_text(json.dumps(payload), encoding="utf-8")
        command = [sys.executable, str(PROGRAM), "--input", str(input_path), "--output", str(report_path), "--ledger", str(ledger_path)]
        first_process = subprocess.run(command, capture_output=True, text=True)
        assert first_process.returncode == 0, first_process.stderr
        first_ledger = ledger_path.read_bytes()
        second_process = subprocess.run(command, capture_output=True, text=True)
        assert second_process.returncode == 0, second_process.stderr
        assert ledger_path.read_bytes() == first_ledger
        event = json.loads(first_ledger)
        expected_fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        assert event["fingerprint"] == expected_fingerprint
        assert set(event) == {"fingerprint", "observer_id", "status"}


def test_distinct_input_appends_ledger_event():
    """R6：不同规范输入必须仅追加一个新账本事件。"""
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        input_path = directory / "in.json"
        report_path = directory / "out.json"
        ledger_path = directory / "ledger.jsonl"
        command = [sys.executable, str(PROGRAM), "--input", str(input_path), "--output", str(report_path), "--ledger", str(ledger_path)]
        for payload in (scene(), scene(observer={"id": "OBS-8", "latitude_deg": 0.0, "longitude_deg": 0.0, "sidereal_zero_deg": 0.0})):
            input_path.write_text(json.dumps(payload), encoding="utf-8")
            process = subprocess.run(command, capture_output=True, text=True)
            assert process.returncode == 0, process.stderr
        events = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
        assert len(events) == 2
        assert {event["observer_id"] for event in events} == {"OBS-7", "OBS-8"}


def test_corrupt_ledger_preserves_report_ledger_and_directory():
    """R6/R7：不可解析账本是命名拒收，已有两个目标均应不变。"""
    process, report, ledger, files = invoke(scene(), b"prior report", b"not-json\n")
    assert process.returncode != 0
    assert process.stderr.splitlines()[0] == "REJECT LEDGER_READ"
    assert report == b"prior report"
    assert ledger == b"not-json\n"
    assert files == ["in.json", "ledger.jsonl", "report.json"]


def test_output_target_rejection_preserves_report_ledger_and_directory():
    """R7：任一输出目标不可替换时，报告、账本和目录均不得被部分改写。"""
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        input_path = directory / "in.json"
        report_path = directory / "report.json"
        ledger_path = directory / "ledger-target"
        input_path.write_text(json.dumps(scene()), encoding="utf-8")
        report_path.write_bytes(b"prior report\n")
        ledger_path.mkdir()
        sentinel = ledger_path / "sentinel.txt"
        sentinel.write_bytes(b"prior ledger directory\n")
        command = [sys.executable, str(PROGRAM), "--input", str(input_path), "--output", str(report_path), "--ledger", str(ledger_path)]
        process = subprocess.run(command, capture_output=True, text=True)
        assert process.returncode != 0
        assert process.stderr.splitlines()[0] == "REJECT IO"
        assert report_path.read_bytes() == b"prior report\n"
        assert sentinel.read_bytes() == b"prior ledger directory\n"
        assert sorted(path.name for path in directory.iterdir()) == ["in.json", "ledger-target", "report.json"]
        assert sorted(path.name for path in ledger_path.iterdir()) == ["sentinel.txt"]
        assert not [path for path in directory.iterdir() if path.name.startswith(".visibility-")]


def test_success_is_byte_stable_without_temp_files():
    """R7：重复成功写入必须字节稳定且不残留程序临时文件。"""
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        input_path = directory / "in.json"
        report_path = directory / "out.json"
        ledger_path = directory / "ledger.jsonl"
        input_path.write_text(json.dumps(scene()), encoding="utf-8")
        command = [sys.executable, str(PROGRAM), "--input", str(input_path), "--output", str(report_path), "--ledger", str(ledger_path)]
        first_process = subprocess.run(command, capture_output=True, text=True)
        assert first_process.returncode == 0, first_process.stderr
        before = (report_path.read_bytes(), ledger_path.read_bytes())
        second_process = subprocess.run(command, capture_output=True, text=True)
        assert second_process.returncode == 0, second_process.stderr
        assert before == (report_path.read_bytes(), ledger_path.read_bytes())
        assert not [path for path in directory.iterdir() if path.name.startswith(".visibility-")]
