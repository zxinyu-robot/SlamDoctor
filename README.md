# ESIS Diagnostics

ESIS 是一个面向机器人传感器、定位系统和异常行为排查的开源 CLI。它将不同运行时
的数据统一为观测事件，通过滑动窗口指标、可配置规则和带滞回的状态机生成可解释报告。

它适合用于日志回放、现场问题复现、自动化验收和故障根因初筛，不替代 SLAM、标定或
厂商驱动工具。

## 能力

- 数据完整性：时间倒退、序列乱序、丢帧和数据源停止
- 实时性：实际频率、P95 延迟和频率漂移
- 数据质量：数值冻结、IMU 饱和、GNSS 失锁、LiDAR 稀疏、视觉特征不足
- 定位诊断：静止漂移、位姿跳变和高协方差
- 可解释结果：实际值、期望阈值和候选原因
- 输入与输出：JSONL、CSV、标准输入、终端报告和 JSON 报告

## 安装

要求 Python 3.10 或更高版本。

```bash
python -m pip install .
```

开发环境：

```bash
python -m pip install ".[dev]"
pytest
```

## 快速开始

生成一段包含静止定位漂移的演示数据并诊断：

```bash
esis simulate --sensor localization --fault drift --output drift.jsonl
esis inspect drift.jsonl
```

其他命令：

```bash
esis validate data.jsonl
esis inspect data.jsonl --config configs/default.yaml
esis inspect data.jsonl --format json --output report.json
esis replay data.jsonl --speed 20
robot-adapter | esis watch
```

生成故障样例：

```bash
esis simulate --sensor imu --fault frame-drop --output imu.jsonl
esis simulate --sensor imu --fault latency --output latency.jsonl
esis simulate --sensor gnss --fault gnss-loss --output gnss.jsonl
esis simulate --sensor lidar --fault sparse --output lidar.jsonl
esis simulate --sensor camera --fault low-features --output camera.jsonl
esis simulate --sensor localization --fault pose-jump --output pose.jsonl
```

## 标准观测格式

每行是一个 JSON 对象：

```json
{
  "sensor_id": "imu_front",
  "sensor_type": "imu",
  "timestamp": 1720000000.125,
  "receive_timestamp": 1720000000.132,
  "sequence": 1024,
  "payload_size": 96,
  "values": {
    "accel_x": 0.12,
    "accel_y": 0.01,
    "accel_z": 9.81
  },
  "metadata": {
    "frame_id": "imu_link"
  }
}
```

`timestamp` 表示数据产生时间，`receive_timestamp` 表示诊断侧收到数据的时间。二者必须
使用同一时钟基准。`values` 中可以放传感器原始统计值或算法输出指标。

定位观测使用 `sensor_type` 为 `pose`、`localization` 或 `slam`，并在 `values` 中提供
`x`、`y`。机器人确定静止时，可设置 `"metadata": {"stationary": true}`；也可以同时输入
`wheel` 或 `wheel_odometry` 类型的速度观测，由引擎判断静止状态。

CSV 必须包含 `sensor_id`、`sensor_type`、`timestamp` 等标准列。其他列会自动放入
`values`；`values` 和 `metadata` 列本身可以保存 JSON 对象。

## 配置

规则配置见 [`configs/default.yaml`](configs/default.yaml)。`defaults` 应用于所有数据源，
`sensors` 可以按 `sensor_id` 覆盖阈值：

```yaml
defaults:
  latency_warning_ms: 50
  latency_critical_ms: 200
  drop_warning_rate: 0.01

sensors:
  imu_front:
    type: imu
    expected_frequency_hz: 200
    latency_warning_ms: 10
```

## 诊断流程

```text
JSONL / CSV / stdin
        |
        v
结构校验与标准化
        |
        v
按 sensor_id 维护有界滑动窗口
        |
        v
通用规则 + 传感器规则 + 定位规则
        |
        v
状态机：UNKNOWN / HEALTHY / DEGRADED / FAULT / RECOVERING / OFFLINE
        |
        v
终端或 JSON 证据报告
```

诊断是流式执行的，每个传感器只保留有限窗口，不会将完整日志加载进内存。

## 定位漂移的边界

ESIS 可以检测静止漂移、定位跳变以及多项上游退化征兆，但仅凭单一路径位姿无法证明
漂移的唯一根因。实际项目应同时采集轮速、IMU、GNSS 状态、点云匹配分数、视觉跟踪率、
TF/外参版本、定位协方差和计算负载，以形成更可靠的证据链。

## 扩展适配器

ROS 2、rosbag2、MCAP 或厂商 SDK 适配器只需将来源数据转换为标准观测 JSONL，再通过
管道输入 `esis watch`。核心诊断层不依赖 ROS，便于部署到机器人本体、运维电脑或 CI。

## 开源协议

MIT License。欢迎提交 Issue 和 Pull Request。
