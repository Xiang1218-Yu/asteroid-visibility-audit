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


# case_id: ASV-R2-01
# weight: 0.126667
# reason: R2 轨道传播是最近地心距与风险分级的上游判据；用半长轴代替偏心近点会造成科学结论错误。
def test_kepler_propagation_uses_eccentric_periapsis_distance():
    """ASV-R2-01 / instruction.md「轨道传播（R2）」：偏心椭圆在近地点的地心距必须由两体开普勒传播得到，不能用半长轴代替。"""
    process, report_bytes, _, _ = invoke(scene())
    assert process.returncode == 0, process.stderr
    report = json.loads(report_bytes)
    assert report["closest_approach_s"] == 0.0
    assert report["closest_distance_km"] == pytest.approx(9600.0)
    assert report["earth_risk_status"] == "CLOSE_APPROACH"


# case_id: ASV-R3-01
# weight: 0.126667
# reason: R3 要求风险候选含相邻正式采样中点；漏掉中点会把采样间真实近地事件判成安全。
def test_midpoint_risk_candidate_detects_between_sample_close_approach():
    """ASV-R3-01 / instruction.md「近距离风险（R3）」：风险候选必须包含每对相邻正式采样时刻的中点，漏掉中点会把采样间真实近地事件判成安全。"""
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
# weight: 0.126666
# reason: 轨道姿态与黄赤转换共同决定站心视线；任一旋转错误都会扭曲高纬台站高度角和可见性。
def test_inclination_rotation_and_obliquity_control_station_altitude():
    """ASV-R2-R4-01 / instruction.md「轨道传播（R2）」与「台站几何、遮挡与可见性（R4）」：三维轨道姿态和黄赤转换必须共同决定高纬台站高度角。"""
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
    """ASV-R4-01 / instruction.md「台站几何、遮挡与可见性（R4）」：地球遮挡必须优先于宽松的高度角门槛，被遮挡目标不得产生观测窗口。"""
    payload = scene(
        orbit={"a_km": 1000000.0, "e": 0.0, "i_deg": 0.0, "raan_deg": 0.0, "arg_periapsis_deg": 0.0, "mean_anomaly_deg": 90.3},
        stations=[{"id": "HORIZON", "latitude_deg": 0.0, "longitude_deg": 0.0, "height_km": 0.0, "sidereal_zero_deg": 0.0}],
    )
    process, report_bytes, _, _ = invoke(payload)
    assert process.returncode == 0, process.stderr
    report = json.loads(report_bytes)
    assert report["stations"][0]["status"] == "NOT_VISIBLE"
    assert report["stations"][0]["windows"] == []


# case_id: ASV-R4-02
# weight: 0.072500
# reason: 近地平可见资格按 R4 的表观高度判定；忽略折射会错误拒绝真实可观测目标。
def test_near_horizon_refraction_can_meet_apparent_altitude_limit():
    """ASV-R4-02 / instruction.md「台站几何、遮挡与可见性（R4）」：未遮挡的近地平目标必须按折射后的表观高度判定可见资格。"""
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
    """ASV-R4-03 / instruction.md「台站几何、遮挡与可见性（R4）」：太阳角距不足时，即使目标高度很高也不得进入跟踪窗口。"""
    process, report_bytes, _, _ = invoke(scene(sun={"ra_deg": 0.0, "dec_deg": 0.0}))
    assert process.returncode == 0, process.stderr
    report = json.loads(report_bytes)
    assert report["stations"][0]["status"] == "NOT_VISIBLE"
    assert report["union_status"] == "NO_TRACK"


# case_id: ASV-R3-02
# weight: 0.072500
# reason: R3 明确要求把 end_s 纳入正式样本；遗漏末端样本会漏掉末端风险与可见性判定。
def test_final_nonintegral_sampling_endpoint_is_included():
    """ASV-R3-02 / instruction.md「近距离风险（R3）」：不能整除步长的结束时刻仍必须作为正式采样时刻，遗漏末端会漏掉风险与可见性判定。"""
    process, report_bytes, _, _ = invoke(scene(window={"start_s": 0.0, "end_s": 25.0, "cadence_s": 10.0}))
    assert process.returncode == 0, process.stderr
    report = json.loads(report_bytes)
    assert report["sample_times_s"] == [0.0, 10.0, 20.0, 25.0]


# case_id: ASV-R5-01
# weight: 0.070000
# reason: R5 以 min_track_s 区分短暂可见与合格跟踪；短窗口误报可跟踪会夸大任务可执行性。
def test_short_visible_window_is_brief_not_trackable():
    """ASV-R5-01 / instruction.md「窗口与联合状态（R5）」：窗口存在但时长小于 min_track_s 时，台站必须为 BRIEF。"""
    process, report_bytes, _, _ = invoke(scene(limits={"min_apparent_altitude_deg": -5.0, "sun_avoidance_deg": 1.0, "risk_distance_km": 10000.0, "min_track_s": 2.0}))
    assert process.returncode == 0, process.stderr
    report = json.loads(report_bytes)
    assert report["stations"][0]["status"] == "BRIEF"
    assert report["union_status"] == "BRIEF_ONLY"


# case_id: ASV-R5-02
# weight: 0.070000
# reason: R5 的立体跟踪需要同刻合格且经度差至少 30 deg 的台站，足够基线才能形成有效视差。
def test_widely_separated_qualified_stations_enable_stereo_tracking():
    """ASV-R5-02 / instruction.md「窗口与联合状态（R5）」：同刻合格且经度绝对差不小于 30 deg 的两个台站应形成 STEREO_TRACKABLE。"""
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
    """ASV-R5-03 / instruction.md「窗口与联合状态（R5）」：经度绝对差不足 30 deg 时只能形成 SINGLE_STATION_TRACKABLE，不得形成立体跟踪。"""
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
    """ASV-R1-R6-01 / instruction.md「输入契约（R1）」与「输出、账本与原子性（R6）」：重复台站 ID 必须拒收，且不得改写报告、账本或目录。"""
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
    """ASV-R1-R6-02 / instruction.md「输入契约（R1）」与「输出、账本与原子性（R6）」：近地点不大于地球半径的轨道必须拒收，且不得改写报告、账本或目录。"""
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
    """ASV-R6-01 / instruction.md「输出、账本与原子性（R6）」：成功报告必须紧凑、键序稳定、LF 结尾，且顶层字段恰为公开集合。"""
    process, report_bytes, _, _ = invoke(scene())
    assert process.returncode == 0, process.stderr
    report = json.loads(report_bytes)
    assert set(report) == {"closest_approach_s", "closest_distance_km", "earth_risk_status", "sample_times_s", "stations", "union_status"}
    assert report_bytes == json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    assert report_bytes.endswith(b"\n")


# case_id: ASV-R6-02
# weight: 0.017143
# reason: R6 要求规范输入指纹去重；重复事件会破坏账本语义和重放一致性。
def test_same_canonical_input_deduplicates_ledger_event():
    """ASV-R6-02 / instruction.md「输出、账本与原子性（R6）」：相同规范输入重复运行只能保留一条账本事件。"""
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
    """ASV-R6-03 / instruction.md「输出、账本与原子性（R6）」：无法解析的既有账本必须拒收，并保持报告、账本和目录不变。"""
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
    """ASV-R6-04 / instruction.md「输出、账本与原子性（R6）」：账本目标为目录时必须拒收，不得先替换报告或留下临时文件。"""
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
    """ASV-R6-05 / instruction.md「输出、账本与原子性（R6）」：相同合法输入重复成功后，报告、账本和目录清单必须逐字节稳定。"""
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
