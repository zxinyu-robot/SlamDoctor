# SlamDoctor

[![CI](https://github.com/zxinyu-robot/SlamDoctor/actions/workflows/ci.yml/badge.svg)](https://github.com/zxinyu-robot/SlamDoctor/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

基于接口契约、运行证据和可执行故障图的 SLAM 导航诊断 CLI。

SlamDoctor 将传感器、定位、地图、规划、控制和系统资源观测统一为事件流，检查时间、坐标、频率、序列、数据年龄和质量契约，再依据“症状 → 假设 → Probe → 修复”故障图对根因进行排序。

它适合日志回放、现场问题初筛、自动化验收和回归测试，不替代标定、SLAM、控制器或厂商驱动工具。

## 核心能力

- 通用契约：时间倒退、乱序、丢帧、频率、P95 延迟、冻结、离线和 frame 变化。
- 传感器：IMU 饱和、GNSS 失锁、LiDAR 稀疏、视觉特征不足。
- SLAM：静止漂移、位姿跳变、高协方差、低 inlier、几何退化和 TF 不可用。
- 地图与规划：地图/路径过期、规划超时和规划失败。
- 控制：跟踪误差、周期超限和控制进程崩溃。
- 系统：消息队列、CPU、内存和温度。
- 故障图：八类内置症状、根因排序、下一条低成本 Probe 和候选修复。
- 输出：终端、JSON、Markdown、Mermaid 和 Graphviz DOT。

## 安装

要求 Python 3.10 或更高版本。

```bash
python -m pip install .
```

开发环境：

```bash
python -m pip install ".[dev]"
ruff check .
pytest
```

安装后提供主命令 `slam-doctor`；旧命令 `esis` 继续兼容。

## 快速开始

直接诊断仓库内的导航振荡样例：

```bash
slam-doctor inspect examples/nav_oscillation.jsonl
slam-doctor diagnose examples/nav_oscillation.jsonl \
  --symptom NAV_OSCILLATION
```

生成 Markdown 报告：

```bash
slam-doctor report examples/nav_oscillation.jsonl \
  --symptom LOCAL_PLANNER_OSCILLATION \
  --output report.md
```

导出故障图：

```bash
slam-doctor graph --symptom POSE_JUMP --format mermaid
slam-doctor graph --symptom CONTROLLER_CRASH --format dot \
  --output controller-crash.dot
```

列出内置症状：

```bash
slam-doctor symptoms
```

## 故障图

```text
System dependency DAG
          ↓
Interface contract findings
          ↓
Symptom
  ├── Hypothesis A
  │     ├── matched evidence
  │     ├── next Probe
  │     └── candidate remediation
  └── Hypothesis B
```

内置症状：

- `LOCALIZATION_DRIFT`
- `POSE_JUMP`
- `MAP_GHOSTING`
- `GLOBAL_PLAN_FAILED`
- `LOCAL_PLANNER_OSCILLATION`
- `CONTROLLER_CRASH`
- `END_TO_END_LATENCY`
- `NETWORK_RECOVERY_FAILED`

分数用于排障优先级，不表示经过统计校准的因果概率。缺少某类遥测时，相应假设会以低先验保留，而不是被错误排除。

## 标准观测格式

每行一个 JSON 对象：

```json
{
  "sensor_id": "localization",
  "sensor_type": "slam",
  "timestamp": 1720000000.125,
  "receive_timestamp": 1720000000.145,
  "sequence": 1024,
  "values": {
    "x": 2.1,
    "y": 0.4,
    "inlier_ratio": 0.58,
    "degeneracy_score": 0.2,
    "position_covariance": 0.04
  },
  "metadata": {
    "frame_id": "world",
    "tf_available": true
  }
}
```

支持 JSONL、CSV 和标准输入。ROS 2、rosbag2、MCAP、LTTng 或厂商 SDK 适配器只需转换到该格式，诊断核心不依赖 ROS。

## 其他命令

```bash
slam-doctor validate data.jsonl
slam-doctor inspect data.jsonl --config configs/default.yaml
slam-doctor inspect data.jsonl --format json --output health.json
slam-doctor replay data.jsonl --speed 20
robot-adapter | slam-doctor watch

slam-doctor simulate --sensor localization --fault drift --output drift.jsonl
slam-doctor simulate --sensor imu --fault frame-drop --output imu.jsonl
slam-doctor simulate --sensor lidar --fault sparse --output lidar.jsonl
```

## 设计边界

- 核心只执行只读诊断，不修改参数、不重启节点、不发送控制指令。
- Pose/路径注入类 Probe 必须在仿真、离线回放或受控测试条件下执行。
- 单个异常通常不能证明唯一根因；工具通过跨层证据提高排序质量。
- 生产环境应同时采集 source/publish/receive/consume 时间和统一 trace ID。

详细设计见：

- [系统架构](docs/architecture.md)
- [故障图指南](docs/fault-graph.md)

## License

[MIT](LICENSE)
