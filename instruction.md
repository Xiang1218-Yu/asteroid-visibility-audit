# 近地小天体多台站风险与可见性审计

`/app/visibility.py` 的接口为：

```text
python3 /app/visibility.py --input <scenario.json> --output <audit.json> --ledger <ledger.jsonl>
```

修复程序，使其能够传播一颗近地小天体的椭圆两体轨道，审计其在规划窗口中的地球近距离风险，并给出多个地面台站的可跟踪窗口。输入是合成的短期规划数据；实现不得使用第三方 Python 包或依赖外部星历数据。

## 常数、单位与数值规则

距离单位为 km，时间单位为相对参考历元的 s，公开角度单位为 deg。计算时可转换为 rad。使用：

```text
MU = 398600.4418 km^3/s^2
EARTH_RADIUS_KM = 6378.137 km
EARTH_ROTATION_RAD_S = 7.2921150e-5 rad/s
OBLIQUITY_DEG = 23.439291111 deg
```

所有 JSON 数值必须有限；布尔值不是数值。公开输出中的数值须四舍五入到 6 位小数。

## 输入契约（R1）

顶层对象键必须恰为 `orbit`、`epoch`、`stations`、`sun`、`window`、`limits`。

- `epoch` 必须为数值 `0`。
- `orbit` 的键必须恰为 `a_km`、`e`、`i_deg`、`raan_deg`、`arg_periapsis_deg`、`mean_anomaly_deg`。要求 `a_km > EARTH_RADIUS_KM`、`0 <= e < 1`、`i_deg` 位于 `[0,180]`，三个定向角位于 `[0,360)`，且近地点 `a_km*(1-e)` 必须严格大于 `EARTH_RADIUS_KM`。
- `stations` 是长度为 1 至 8 的数组。每个对象键必须恰为 `id`、`latitude_deg`、`longitude_deg`、`height_km`、`sidereal_zero_deg`；ID 为非空字符串且全局唯一；纬度位于 `[-90,90]`、经度位于 `[-180,180]`、高度位于 `[-0.5,10]`、恒星时零点位于 `[0,360)`。
- `sun` 的键必须恰为 `ra_deg`、`dec_deg`，赤经位于 `[0,360)`、赤纬位于 `[-90,90]`。
- `window` 的键必须恰为 `start_s`、`end_s`、`cadence_s`，并满足 `start_s >= 0`、`end_s > start_s`、`cadence_s > 0`。
- `limits` 的键必须恰为 `min_apparent_altitude_deg`、`sun_avoidance_deg`、`risk_distance_km`、`min_track_s`，并分别位于 `[-5,90]`、`[0,180]`、`(EARTH_RADIUS_KM,+∞)`、`[0,+∞)`。

无效 JSON、I/O、结构、类型、范围、轨道穿地、开普勒方程未收敛、损坏账本或输出目标不可写时，必须非零退出，stderr 首行以 `REJECT ` 开头。已有报告和账本字节不得变化，目录中不得留下程序临时文件。

## 轨道传播（R2）

对任意时刻 `t`，令：

```text
n = sqrt(MU / a^3)
M = (mean_anomaly_deg + n*t*180/pi) mod 360
```

把 `M` 转为 rad，并以 `E=M` 为初值用 Newton-Raphson 求椭圆开普勒方程：

```text
E - e*sin(E) = M
Delta = (E - e*sin(E) - M) / (1 - e*cos(E))
E = E - Delta
```

最多迭代 20 次；`abs(Delta) <= 1e-12` 即收敛。若分母绝对值小于 `1e-14` 或未收敛，拒收 `REJECT KEPLER`。

在轨道平面计算：

```text
x_p = a*(cos(E)-e)
y_p = a*sqrt(1-e^2)*sin(E)
```

再以 `R3(raan) × R1(i) × R3(arg_periapsis)` 旋转到地心黄道惯性坐标，最后绕 x 轴旋转 `OBLIQUITY_DEG` 到地心赤道惯性坐标 `r_eci(t)`。

## 近距离风险（R3）

正式采样时刻包含 `start_s`、全部严格小于 `end_s` 的 `start_s+n*cadence_s`，以及 `end_s`，且严格升序、无重复。除了全部正式采样时刻，风险候选还必须包括每对相邻正式采样时刻的中点。对每个候选计算 `|r_eci(t)|`，选择最小距离；并列时选择更早时刻。

输出 `closest_approach_s` 与 `closest_distance_km`。风险状态为：

- `IMPACT_RISK`：距离不大于 `EARTH_RADIUS_KM`；
- `CLOSE_APPROACH`：距离大于地球半径且不大于 `risk_distance_km`；
- `CLEAR`：其余情况。

## 台站几何、遮挡与可见性（R4）

对每个正式采样时刻和每个台站，令：

```text
theta = sidereal_zero_deg + longitude_deg + EARTH_ROTATION_RAD_S*t*180/pi
R = EARTH_RADIUS_KM + height_km
r_station = (R*cos(latitude)*cos(theta), R*cos(latitude)*sin(theta), R*sin(latitude))
rho = r_eci - r_station
z_hat = r_station / |r_station|
alt_geo = asin((rho/|rho|) dot z_hat)
```

若 `rho dot r_station < 0`，且线段 `r_station + lambda*rho`（`0 < lambda < 1`）到地心的最短距离严格小于 `EARTH_RADIUS_KM`，小天体被地球遮挡，必须不可见。

未被遮挡时，若 `alt_geo > -1 deg`，使用：

```text
refraction_deg = 1.02 / tan((alt_geo + 10.3/(alt_geo + 5.11))*pi/180) / 60
alt_apparent = alt_geo + refraction_deg
```

否则 `alt_apparent = alt_geo`。折射只能用于可见资格与输出窗口峰值，不得改变遮挡判定。

把 `rho` 的单位向量转换为赤经、赤纬，再与固定 `sun` 方向计算球面角距。一个样本可见，当且仅当未被遮挡、`alt_apparent >= min_apparent_altitude_deg` 且太阳角距 `>= sun_avoidance_deg`；相等边界可见。

## 窗口与联合状态（R5）

对每个台站，把连续可见样本合并为窗口。窗口字段必须恰为：

```text
start_s, end_s, duration_s, peak_apparent_altitude_deg, min_station_distance_km
```

窗口时长小于 `min_track_s` 仍须输出，但不算合格跟踪窗口。台站状态：无窗口为 `NOT_VISIBLE`；有窗口但没有合格窗口为 `BRIEF`；否则为 `TRACKABLE`。

联合状态：若至少两个经度绝对差不小于 `30 deg` 的不同台站在同一正式采样时刻同时属于合格窗口，为 `STEREO_TRACKABLE`；否则若有 `TRACKABLE` 台站，为 `SINGLE_STATION_TRACKABLE`；否则若有 `BRIEF` 台站，为 `BRIEF_ONLY`；否则为 `NO_TRACK`。只可依据规定的离散时刻判定同步，不能插值制造重叠。

## 输出、账本与原子性（R6）

成功报告是 UTF-8、紧凑、对象键字典序稳定、LF 结尾的 JSON，顶层字段恰为：

```text
closest_approach_s, closest_distance_km, earth_risk_status, sample_times_s, stations, union_status
```

`stations` 按 `id` 的 Unicode 码点升序排列；每个台站字段恰为 `id`、`status`、`windows`。

账本为 JSON Lines，每行字段恰为：

```text
fingerprint, closest_distance_km, earth_risk_status, union_status
```

`fingerprint` 是原始输入经对象键排序、紧凑分隔符、UTF-8 编码后的 SHA-256 小写十六进制摘要。相同规范输入重复运行只能保留一条事件。报告和账本必须使用各自同目录临时文件后替换；任何失败均不得只更新其中一个目标。
