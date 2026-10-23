import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Traceability and scoring weights are machine-readable in tests/scoring.json.
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


def collect_numbers(value, output):
    if isinstance(value, bool):
        raise AssertionError("boolean must not be treated as a JSON number")
    if isinstance(value, (int, float)):
        output.append(value)
        return
    if isinstance(value, dict):
        for child in value.values():
            collect_numbers(child, output)
        return
    if isinstance(value, list):
        for child in value:
            collect_numbers(child, output)


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


# case_id: ASV-R2-01
# gate: true (unscored prerequisite; machine-readable in tests/scoring.json)
# reason: 硬门禁：轨道传播是最近地心距与风险分级的上游判据；用半长轴代替偏心近点会造成科学结论错误。
def test_kepler_propagation_uses_eccentric_periapsis_distance():
    """case_id=ASV-R2-01; requirement=R2; instruction.md#R2: 「x_p = a*(cos(E)-e)」; behavior: 偏心椭圆近地点地心距必须由两体开普勒传播得到。; gate: true (unscored prerequisite); reason: 轨道传播错误时，最近地心距、风险分级与后续可见性结论均不可信。"""
    process, report_bytes, _, _ = invoke(scene())
    assert process.returncode == 0, process.stderr
    report = json.loads(report_bytes)
    assert report["closest_approach_s"] == pytest.approx(0.0)
    assert report["closest_distance_km"] == pytest.approx(9600.0)
    assert report["earth_risk_status"] == "CLOSE_APPROACH"


# case_id: ASV-R3-01
# weight: 0.190000
# reason: R3 要求风险候选含相邻正式采样中点；漏掉中点会把采样间真实近地事件判成安全。
def test_midpoint_risk_candidate_detects_between_sample_close_approach():
    """case_id=ASV-R3-01; requirement=R3; instruction.md#R3: 「风险候选还必须包括每对相邻正式采样时刻的中点」; behavior: 相邻正式采样之间的真实最近接近必须被中点候选命中。; weight: 0.190000; reason: R3 要求风险候选含相邻正式采样中点；漏掉中点会把采样间真实近地事件判成安全。"""
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


# case_id: ASV-R2-R4-01
# weight: 0.190000
# reason: 轨道姿态与黄赤转换共同决定站心视线；任一旋转错误都会扭曲高纬台站高度角和可见性。
def test_inclination_rotation_and_obliquity_control_station_altitude():
    """case_id=ASV-R2-R4-01; requirement=R2+R4; instruction.md#R2+R4: 「再以 `R3(raan) × R1(i) × R3(arg_periapsis)` 旋转到地心黄道惯性坐标」; behavior: 三维轨道姿态与黄赤转换必须共同决定高纬台站高度角。; weight: 0.190000; reason: 轨道姿态与黄赤转换共同决定站心视线；任一旋转错误都会扭曲高纬台站高度角和可见性。"""
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


# case_id: ASV-R4-01
# weight: 0.072500
# reason: 地球遮挡是 R4 的硬几何边界；被遮挡目标误判可见会制造不存在的观测窗口。
def test_earth_occultation_overrides_permissive_horizon_limit():
    """case_id=ASV-R4-01; requirement=R4; instruction.md#R4: 「否则 `alt_apparent = alt_geo`。折射只能用于可见资格与输出窗口峰值，不得改变遮挡判定。」; behavior: 地球遮挡必须优先于宽松的高度角门槛与折射修正。; weight: 0.072500; reason: 地球遮挡是 R4 的硬几何边界；被遮挡目标误判可见会制造不存在的观测窗口。"""
    payload = scene(
        orbit={"a_km": 1000000.0, "e": 0.0, "i_deg": 0.0, "raan_deg": 0.0, "arg_periapsis_deg": 0.0, "mean_anomaly_deg": 90.3},
        stations=[{"id": "HORIZON", "latitude_deg": 0.0, "longitude_deg": 0.0, "height_km": 0.0, "sidereal_zero_deg": 0.0}],
    )
    process, report_bytes, _, _ = invoke(payload)
    assert process.returncode == 0, process.stderr
    report = json.loads(report_bytes)
    assert report["stations"][0]["status"] == "NOT_VISIBLE"
    assert report["stations"][0]["windows"] == []
    assert report["earth_risk_status"] in {"IMPACT_RISK", "CLOSE_APPROACH", "CLEAR"}
    assert "closest_approach_s" in report and "closest_distance_km" in report


# case_id: ASV-R4-02
# weight: 0.072500
# reason: 近地平可见资格按 R4 的表观高度判定；忽略折射会错误拒绝真实可观测目标。
def test_near_horizon_refraction_can_meet_apparent_altitude_limit():
    """case_id=ASV-R4-02; requirement=R4; instruction.md#R4: 「alt_apparent = alt_geo + refraction_deg」; behavior: 未遮挡目标必须按折射后的表观高度判定可见资格。; weight: 0.072500; reason: 近地平可见资格按 R4 的表观高度判定；忽略折射会错误拒绝真实可观测目标。"""
    payload = scene(
        orbit={"a_km": 1000000.0, "e": 0.0, "i_deg": 0.0, "raan_deg": 0.0, "arg_periapsis_deg": 0.0, "mean_anomaly_deg": 89.5},
        limits={"min_apparent_altitude_deg": 0.55, "sun_avoidance_deg": 1.0, "risk_distance_km": 10000.0, "min_track_s": 0.0},
    )
    process, report_bytes, _, _ = invoke(payload)
    assert process.returncode == 0, process.stderr
    report = json.loads(report_bytes)
    assert report["stations"][0]["status"] == "TRACKABLE"
    assert report["stations"][0]["windows"][0]["peak_apparent_altitude_deg"] == pytest.approx(0.601777)


# case_id: ASV-R4-03
# weight: 0.072500
# reason: 太阳规避是 R4 的观测安全边界；高高度不能抵销太阳角距不足。
def test_solar_avoidance_rejects_an_otherwise_high_target():
    """case_id=ASV-R4-03; requirement=R4; instruction.md#R4: 「太阳角距 `>= sun_avoidance_deg`；相等边界可见。」; behavior: 太阳角距不足时高高度目标仍不得进入跟踪窗口。; weight: 0.072500; reason: 太阳规避是 R4 的观测安全边界；高高度不能抵销太阳角距不足。"""
    process, report_bytes, _, _ = invoke(scene(sun={"ra_deg": 0.0, "dec_deg": 0.0}))
    assert process.returncode == 0, process.stderr
    report = json.loads(report_bytes)
    assert report["stations"][0]["status"] == "NOT_VISIBLE"
    assert report["union_status"] == "NO_TRACK"


# case_id: ASV-R3-02
# weight: 0.072500
# reason: R3 明确要求把 end_s 纳入正式样本；遗漏末端样本会漏掉末端风险与可见性判定。
def test_final_nonintegral_sampling_endpoint_is_included():
    """case_id=ASV-R3-02; requirement=R3; instruction.md#R3: 「以及 `end_s`，且严格升序、无重复。」; behavior: 不能整除步长的结束时刻仍必须作为正式采样时刻。; weight: 0.072500; reason: R3 明确要求把 end_s 纳入正式样本；遗漏末端样本会漏掉末端风险与可见性判定。"""
    process, report_bytes, _, _ = invoke(scene(window={"start_s": 0.0, "end_s": 25.0, "cadence_s": 10.0}))
    assert process.returncode == 0, process.stderr
    report = json.loads(report_bytes)
    assert report["sample_times_s"] == [0.0, 10.0, 20.0, 25.0]


# case_id: ASV-R5-01
# weight: 0.070000
# reason: R5 以 min_track_s 区分短暂可见与合格跟踪；短窗口误报可跟踪会夸大任务可执行性。
def test_short_visible_window_is_brief_not_trackable():
    """case_id=ASV-R5-01; requirement=R5; instruction.md#R5: 「对每个台站，把连续可见样本合并为窗口。窗口字段必须恰为：」; behavior: 短于最短跟踪时长的窗口必须保留并输出精确字段，但使台站为 BRIEF。; weight: 0.070000; reason: R5 以 min_track_s 区分短暂可见与合格跟踪；短窗口误报可跟踪会夸大任务可执行性。"""
    process, report_bytes, _, _ = invoke(scene(limits={"min_apparent_altitude_deg": -5.0, "sun_avoidance_deg": 1.0, "risk_distance_km": 10000.0, "min_track_s": 2.0}))
    assert process.returncode == 0, process.stderr
    report = json.loads(report_bytes)
    assert report["stations"][0]["status"] == "BRIEF"
    assert report["union_status"] == "BRIEF_ONLY"
    assert set(report["stations"][0]["windows"][0]) == {"start_s", "end_s", "duration_s", "peak_apparent_altitude_deg", "min_station_distance_km"}


# case_id: ASV-R5-02
# weight: 0.070000
# reason: R5 的立体跟踪需要同刻合格且经度差至少 30 deg 的台站，足够基线才能形成有效视差。
def test_widely_separated_qualified_stations_enable_stereo_tracking():
    """case_id=ASV-R5-02; requirement=R5; instruction.md#R5: 「至少两个经度绝对差不小于 `30 deg` 的不同台站在同一正式采样时刻同时属于合格窗口，为 `STEREO_TRACKABLE`」; behavior: 同刻合格且经度差达门槛的两个台站必须形成立体跟踪。; weight: 0.070000; reason: R5 的立体跟踪需要同刻合格且经度差至少 30 deg 的台站，足够基线才能形成有效视差。"""
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


# case_id: ASV-R5-03
# weight: 0.070000
# reason: R5 要求窄经度基线不得冒充立体跟踪；误报会掩盖观测几何不足。
def test_narrow_station_separation_is_not_stereo_tracking():
    """case_id=ASV-R5-03; requirement=R5; instruction.md#R5: 「否则若有 `TRACKABLE` 台站，为 `SINGLE_STATION_TRACKABLE`」; behavior: 经度基线不足时只能形成单站跟踪。; weight: 0.070000; reason: R5 要求窄经度基线不得冒充立体跟踪；误报会掩盖观测几何不足。"""
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


# case_id: ASV-R1-R6-01
# weight: 0.017143
# reason: R1 要求台站 ID 全局唯一，R6 要求拒收不破坏既有报告、账本和目录。
def test_duplicate_station_identifier_is_rejected_without_side_effects():
    """case_id=ASV-R1-R6-01; requirement=R1+R6; instruction.md#R1+R6: 「ID 为非空字符串且全局唯一」; behavior: 重复台站 ID 必须拒收且无持久化副作用。; weight: 0.017143; reason: R1 要求台站 ID 全局唯一，R6 要求拒收不破坏既有报告、账本和目录。"""
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


# case_id: ASV-R1-R6-02
# weight: 0.017143
# reason: R1 禁止近地点穿地轨道，R6 要求该拒收路径保持既有产物逐字节不变。
def test_earth_intersecting_orbit_is_rejected_without_side_effects():
    """case_id=ASV-R1-R6-02; requirement=R1+R6; instruction.md#R1+R6: 「近地点 `a_km*(1-e)` 必须严格大于 `EARTH_RADIUS_KM`」; behavior: 近地点不大于地球半径的轨道必须拒收且无持久化副作用。; weight: 0.017143; reason: R1 禁止近地点穿地轨道，R6 要求该拒收路径保持既有产物逐字节不变。"""
    payload = scene(orbit={"a_km": 7000.0, "e": 0.1, "i_deg": 0.0, "raan_deg": 0.0, "arg_periapsis_deg": 0.0, "mean_anomaly_deg": 0.0})
    process, report, ledger, files = invoke(payload, b"prior-report\n", b"prior-ledger\n")
    assert process.returncode != 0
    assert process.stderr.splitlines()[0] == "REJECT INVALID_ORBIT"
    assert report == b"prior-report\n"
    assert ledger == b"prior-ledger\n"
    assert files == ["in.json", "ledger.jsonl", "report.json"]


# case_id: ASV-R6-01
# weight: 0.017143
# reason: R6 同时约束公开字段集合、紧凑编码、键序和 LF 结尾，保证可稳定消费。
def test_compact_sorted_output_has_required_public_fields():
    """case_id=ASV-R6-01; requirement=R6; instruction.md#R6: 「所有 JSON 数值必须有限；布尔值不是数值。公开输出中的数值须四舍五入到 6 位小数。」「顶层对象键必须恰为 `orbit`、`epoch`、`stations`、`sun`、`window`、`limits`。」「成功报告是 UTF-8、紧凑、对象键字典序稳定、LF 结尾的 JSON」; behavior: 成功报告必须数值有限、至多六位小数、紧凑、键序稳定、LF 结尾且顶层字段集合精确。; weight: 0.017143; reason: R1 与 R6 共同约束公共数值和字段集合，保证输出可被稳定消费。"""
    process, report_bytes, _, _ = invoke(scene())
    assert process.returncode == 0, process.stderr
    report = json.loads(report_bytes)
    assert set(report) == {"closest_approach_s", "closest_distance_km", "earth_risk_status", "sample_times_s", "stations", "union_status"}
    numeric_values = []
    collect_numbers(report, numeric_values)
    assert numeric_values
    assert all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) for value in numeric_values)
    assert all(value == round(value, 6) for value in numeric_values)
    assert report_bytes == json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    assert report_bytes.endswith(b"\n")


# case_id: ASV-R6-02
# weight: 0.017143
# reason: R6 要求规范输入指纹去重；重复事件会破坏账本语义和重放一致性。
def test_same_canonical_input_deduplicates_ledger_event():
    """case_id=ASV-R6-02; requirement=R6; instruction.md#R6: 「相同规范输入重复运行只能保留一条事件。」; behavior: 相同规范输入重复执行只能保留一条账本事件。; weight: 0.017143; reason: R6 要求规范输入指纹去重；重复事件会破坏账本语义和重放一致性。"""
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


# case_id: ASV-R6-03
# weight: 0.017143
# reason: 损坏账本属于 R6 命名拒收；失败必须保留旧报告、旧账本且不遗留临时文件。
def test_corrupt_ledger_is_rejected_without_side_effects():
    """case_id=ASV-R6-03; requirement=R6; instruction.md#R6: 「损坏账本或输出目标不可写时」; behavior: 无法解析的既有账本必须拒收且无持久化副作用。; weight: 0.017143; reason: 损坏账本属于 R6 命名拒收；失败必须保留旧报告、旧账本且不遗留临时文件。"""
    process, report, ledger, files = invoke(scene(), b"prior-report\n", b"not-json\n")
    assert process.returncode != 0
    assert process.stderr.splitlines()[0] == "REJECT LEDGER_READ"
    assert report == b"prior-report\n"
    assert ledger == b"not-json\n"
    assert files == ["in.json", "ledger.jsonl", "report.json"]


# case_id: ASV-R6-04
# weight: 0.017143
# reason: R6 要求两个目标原子替换；账本目标不可写时不得先替换报告或遗留半成品。
def test_directory_ledger_target_is_rejected_without_partial_report_update():
    """case_id=ASV-R6-04; requirement=R6; instruction.md#R6: 「任何失败均不得只更新其中一个目标。」; behavior: 账本目标为目录时必须拒收且不得部分更新报告。; weight: 0.017143; reason: R6 要求两个目标原子替换；账本目标不可写时不得先替换报告或遗留半成品。"""
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


# case_id: ASV-R6-05
# weight: 0.017142
# reason: R6 要求成功路径可重复且原子；重复运行后报告、账本和目录必须保持稳定。
def test_repeated_success_is_byte_stable_and_leaves_no_temp_files():
    """case_id=ASV-R6-05; requirement=R6; instruction.md#R6: 「报告和账本必须使用各自同目录临时文件后替换」; behavior: 相同合法输入重复成功后报告与账本必须逐字节稳定且无临时文件。; weight: 0.017142; reason: R6 要求成功路径可重复且原子；重复运行后报告、账本和目录必须保持稳定。"""
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
