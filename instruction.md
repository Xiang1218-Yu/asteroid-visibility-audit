# 近地小天体可见窗口审计

`/app/visibility.py` 的接口为：

```text
python3 /app/visibility.py --input <scenario.json> --output <report.json> --ledger <ledger.jsonl>
```

修复程序，使其针对固定天球坐标的小天体生成台站可见性报告。输入的小天体、太阳方向和台站参数均为合成的短时规划数据。不可联网，不得使用第三方 Python 包。

## 输入契约

输入是 UTF-8 JSON 对象，顶层键必须恰为 `observer`、`target`、`sun`、`window`、`limits`。`observer` 的键必须恰为 `id`、`latitude_deg`、`longitude_deg`、`sidereal_zero_deg`：ID 为非空字符串，纬度在 `[-90,90]`，经度在 `[-180,180]`，恒星时零点在 `[0,360)`。`target`、`sun` 的键都必须恰为 `ra_deg`、`dec_deg`，赤经在 `[0,360)`、赤纬在 `[-90,90]`。

`window` 的键必须恰为 `start_s`、`end_s`、`cadence_s`，且 `start_s >= 0`、`end_s > start_s`、`cadence_s > 0`。`limits` 的键必须恰为 `min_altitude_deg`、`sun_avoidance_deg`、`min_window_s`，分别位于 `[-90,90]`、`[0,180]`、`[0,+∞)`。所有数值为有限 JSON 数值，布尔值不是数值。无效 JSON、I/O、结构、类型或范围错误均要拒收：非零退出，stderr 首行以 `REJECT ` 开头，已有报告和账本字节不变，目录中不留程序临时文件。

## 几何与窗口

恒星日为 `86164.0905 s`。时刻 `t` 的本地恒星时为 `LST=(sidereal_zero_deg+longitude_deg+360*t/86164.0905) mod 360`；小时角为归一化到 `[-180,180)` 的 `LST-target.ra_deg`。目标仰角满足：

```text
sin(alt)=sin(latitude)sin(target.dec)+cos(latitude)cos(target.dec)cos(H)
```

目标—太阳角距使用两点赤道坐标的球面余弦公式，赤经差按周期角处理。每个样本必须独立计算仰角。

采样时刻包含 `start_s`、全部严格小于 `end_s` 的 `start_s+n*cadence_s` 与 `end_s`，按升序输出。仰角不小于 `min_altitude_deg` 且日角距不小于 `sun_avoidance_deg` 的样本可见；相等时可见。连续可见样本构成窗口，不可见样本中断窗口。窗口包含 `start_s`、`end_s`、`duration_s` 和 `peak_altitude_deg`。无窗口为 `NOT_VISIBLE`；最长窗口小于 `min_window_s` 为 `BRIEF`；否则为 `OBSERVABLE`。

## 输出、账本与原子性

成功报告为 UTF-8、紧凑、键字典序稳定、LF 结尾的 JSON，字段恰为 `observer_id`、`sample_times_s`、`status`、`sun_separation_deg`、`windows`；所有数值四舍五入至 6 位小数。账本每行是紧凑 JSON 事件，字段恰为 `fingerprint`、`observer_id`、`status`。fingerprint 是原始输入按对象键排序、紧凑分隔符和 UTF-8 规范化后的 SHA-256 小写十六进制摘要。同一规范输入重复运行只保留一个事件。

报告与账本必须采用同目录临时写入后替换。账本读取失败或输出写入失败时，两个已有目标均不改写，且不留临时文件。相同合法输入重复执行时报告和账本字节稳定。
