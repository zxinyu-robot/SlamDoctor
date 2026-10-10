from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .config import AppConfig, Thresholds
from .models import (
    DiagnosticReport,
    Evidence,
    Finding,
    Observation,
    SensorReport,
    Severity,
)


def _number(values: dict[str, Any], *names: str) -> float | None:
    for name in names:
        value = values.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def _vector_norm(values: dict[str, Any], prefixes: tuple[str, ...]) -> float | None:
    components: list[float] = []
    for axis in ("x", "y", "z"):
        component = _number(values, *(f"{prefix}_{axis}" for prefix in prefixes))
        if component is None:
            return None
        components.append(component)
    return math.sqrt(sum(value * value for value in components))


@dataclass
class SensorRuntime:
    sensor_id: str
    sensor_type: str
    config: Thresholds
    samples: int = 0
    timestamps: deque[float] = field(default_factory=lambda: deque(maxlen=100))
    value_windows: dict[str, deque[float]] = field(default_factory=dict)
    last_timestamp: float | None = None
    last_sequence: int | None = None
    last_frame_id: str | None = None
    last_pose: tuple[float, float] | None = None
    stationary_anchor: tuple[float, float] | None = None
    dropped: int = 0
    expected_frames: int = 0
    latency_values: deque[float] = field(default_factory=lambda: deque(maxlen=500))
    findings: dict[str, Finding] = field(default_factory=dict)
    state: str = "UNKNOWN"
    critical_streak: int = 0
    healthy_streak: int = 0

    def record(self, finding: Finding) -> None:
        previous = self.findings.get(finding.code)
        if previous is None or finding.severity >= previous.severity:
            self.findings[finding.code] = finding

    def transition(self, severity: Severity) -> None:
        if severity == Severity.CRITICAL:
            self.critical_streak += 1
            self.healthy_streak = 0
            self.state = "FAULT" if self.critical_streak >= 2 else "DEGRADED"
        elif severity == Severity.WARNING:
            self.critical_streak = 0
            self.healthy_streak = 0
            self.state = "DEGRADED"
        else:
            self.critical_streak = 0
            self.healthy_streak += 1
            if self.state in {"FAULT", "DEGRADED"} and self.healthy_streak < 3:
                self.state = "RECOVERING"
            else:
                self.state = "HEALTHY"

    def metrics(self) -> dict[str, float | int | None]:
        intervals = [
            current - previous
            for previous, current in zip(self.timestamps, list(self.timestamps)[1:], strict=False)
            if current > previous
        ]
        frequency = 1 / statistics.median(intervals) if intervals else None
        drop_rate = self.dropped / self.expected_frames if self.expected_frames else 0.0
        latency_p95 = None
        if self.latency_values:
            ordered = sorted(self.latency_values)
            latency_p95 = ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)]
        return {
            "frequency_hz": round(frequency, 3) if frequency else None,
            "drop_rate": round(drop_rate, 6),
            "dropped_frames": self.dropped,
            "latency_p95_ms": round(latency_p95, 3) if latency_p95 is not None else None,
        }


class DiagnosticEngine:
    """Streaming diagnostics with bounded per-sensor memory."""

    def __init__(self, config: AppConfig | None = None, source: str = "stream") -> None:
        self.config = config or AppConfig()
        self.source = source
        self.runtimes: dict[str, SensorRuntime] = {}
        self.latest_by_type: dict[str, Observation] = {}
        self.observations = 0
        self.started_at: float | None = None
        self.ended_at: float | None = None

    def process(self, observation: Observation) -> list[Finding]:
        self.observations += 1
        self.started_at = (
            observation.timestamp
            if self.started_at is None
            else min(self.started_at, observation.timestamp)
        )
        self.ended_at = (
            observation.timestamp
            if self.ended_at is None
            else max(self.ended_at, observation.timestamp)
        )
        runtime = self.runtimes.setdefault(
            observation.sensor_id,
            SensorRuntime(
                sensor_id=observation.sensor_id,
                sensor_type=observation.sensor_type,
                config=self.config.for_sensor(observation.sensor_id),
            ),
        )
        findings = self._evaluate(runtime, observation)
        for finding in findings:
            runtime.record(finding)
        runtime.transition(max((finding.severity for finding in findings), default=Severity.INFO))
        runtime.samples += 1
        runtime.last_timestamp = observation.timestamp
        runtime.last_sequence = observation.sequence
        frame_id = observation.metadata.get("frame_id")
        if isinstance(frame_id, str) and frame_id:
            runtime.last_frame_id = frame_id
        runtime.timestamps.append(observation.timestamp)
        self._remember_values(runtime, observation)
        self.latest_by_type[observation.sensor_type] = observation
        return findings

    def _finding(
        self,
        runtime: SensorRuntime,
        observation: Observation,
        code: str,
        severity: Severity,
        summary: str,
        metric: str,
        actual: float | int | str | bool | None,
        expected: str,
        causes: list[str],
    ) -> Finding:
        return Finding(
            code=code,
            severity=severity,
            sensor_id=runtime.sensor_id,
            timestamp=observation.timestamp,
            summary=summary,
            evidence=[Evidence(metric=metric, actual=actual, expected=expected)],
            possible_causes=causes,
        )

    def _evaluate(self, runtime: SensorRuntime, obs: Observation) -> list[Finding]:
        cfg = runtime.config
        result: list[Finding] = []
        frame_id = obs.metadata.get("frame_id")
        if (
            isinstance(frame_id, str)
            and frame_id
            and runtime.last_frame_id is not None
            and frame_id != runtime.last_frame_id
        ):
            result.append(
                self._finding(
                    runtime,
                    obs,
                    "FRAME_CHANGED",
                    Severity.CRITICAL,
                    "同一数据源的 frame_id 发生变化",
                    "frame_id",
                    frame_id,
                    runtime.last_frame_id,
                    ["TF 配置切换", "数据源复用错误", "节点重启后配置不一致"],
                )
            )
        if obs.metadata.get("tf_available") is False:
            result.append(
                self._finding(
                    runtime,
                    obs,
                    "TF_UNAVAILABLE",
                    Severity.CRITICAL,
                    "目标时间的 TF 不可用",
                    "tf_available",
                    False,
                    "true",
                    ["TF 树断裂", "时间外推失败", "静态外参未发布"],
                )
            )
        if runtime.last_timestamp is not None and obs.timestamp <= runtime.last_timestamp:
            result.append(
                self._finding(
                    runtime,
                    obs,
                    "TIME_REGRESSION",
                    Severity.CRITICAL,
                    "时间戳未单调递增",
                    "timestamp",
                    obs.timestamp,
                    f"> {runtime.last_timestamp}",
                    ["设备时钟回拨", "日志乱序", "时间同步配置错误"],
                )
            )
        if obs.receive_timestamp is not None:
            latency_ms = (obs.receive_timestamp - obs.timestamp) * 1000
            runtime.latency_values.append(latency_ms)
            if latency_ms >= cfg.latency_critical_ms:
                severity = Severity.CRITICAL
            elif latency_ms >= cfg.latency_warning_ms:
                severity = Severity.WARNING
            else:
                severity = Severity.INFO
            if severity > Severity.INFO:
                result.append(
                    self._finding(
                        runtime,
                        obs,
                        "HIGH_LATENCY",
                        severity,
                        "数据接收延迟超过阈值",
                        "latency_ms",
                        round(latency_ms, 3),
                        f"< {cfg.latency_warning_ms} ms",
                        ["计算负载过高", "通信拥塞", "时间同步偏差"],
                    )
                )
        if obs.sequence is not None and runtime.last_sequence is not None:
            gap = obs.sequence - runtime.last_sequence
            if gap > 1:
                runtime.dropped += gap - 1
                runtime.expected_frames += gap
            elif gap == 1:
                runtime.expected_frames += 1
            elif gap <= 0:
                result.append(
                    self._finding(
                        runtime,
                        obs,
                        "SEQUENCE_DISORDER",
                        Severity.WARNING,
                        "序列号重复或倒退",
                        "sequence",
                        obs.sequence,
                        f"> {runtime.last_sequence}",
                        ["消息重复", "数据乱序", "设备重启"],
                    )
                )
        drop_rate = runtime.dropped / runtime.expected_frames if runtime.expected_frames else 0
        if drop_rate >= cfg.drop_critical_rate:
            severity = Severity.CRITICAL
        elif drop_rate >= cfg.drop_warning_rate:
            severity = Severity.WARNING
        else:
            severity = Severity.INFO
        if severity > Severity.INFO:
            result.append(
                self._finding(
                    runtime,
                    obs,
                    "FRAME_DROP",
                    severity,
                    "累计丢帧率超过阈值",
                    "drop_rate",
                    round(drop_rate, 6),
                    f"< {cfg.drop_warning_rate}",
                    ["链路丢包", "订阅队列过小", "处理线程阻塞"],
                )
            )
        result.extend(self._frequency_findings(runtime, obs))
        result.extend(self._frozen_findings(runtime, obs))
        result.extend(self._sensor_findings(runtime, obs))
        return result

    def _frequency_findings(self, runtime: SensorRuntime, obs: Observation) -> list[Finding]:
        cfg = runtime.config
        if cfg.expected_frequency_hz is None or len(runtime.timestamps) < 4:
            return []
        timestamps = [*runtime.timestamps, obs.timestamp]
        intervals = [
            current - previous
            for previous, current in zip(timestamps, timestamps[1:], strict=False)
            if current > previous
        ]
        if not intervals:
            return []
        actual = 1 / statistics.median(intervals)
        deviation = abs(actual - cfg.expected_frequency_hz) / cfg.expected_frequency_hz
        if deviation <= cfg.frequency_tolerance:
            return []
        severity = (
            Severity.CRITICAL if deviation > cfg.frequency_tolerance * 2 else Severity.WARNING
        )
        return [
            self._finding(
                runtime,
                obs,
                "FREQUENCY_DRIFT",
                severity,
                "数据频率偏离期望值",
                "frequency_hz",
                round(actual, 3),
                f"{cfg.expected_frequency_hz} Hz ± {cfg.frequency_tolerance * 100:.0f}%",
                ["传感器配置错误", "系统负载过高", "上游降频"],
            )
        ]

    def _frozen_findings(self, runtime: SensorRuntime, obs: Observation) -> list[Finding]:
        cfg = runtime.config
        if obs.metadata.get("stationary"):
            return []
        numeric = {
            key: float(value)
            for key, value in obs.values.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        candidates: list[float] = []
        for key, value in numeric.items():
            window = runtime.value_windows.get(key)
            if window is not None and len(window) >= cfg.frozen_window - 1:
                candidates.append(statistics.pvariance([*window, value]))
        if not candidates or any(variance > cfg.frozen_variance for variance in candidates):
            return []
        return [
            self._finding(
                runtime,
                obs,
                "VALUE_FROZEN",
                Severity.WARNING,
                "多个数值字段持续不变",
                "max_variance",
                max(candidates),
                f"> {cfg.frozen_variance}",
                ["传感器卡死", "驱动重复发布旧数据", "采集链路冻结"],
            )
        ]

    def _sensor_findings(self, runtime: SensorRuntime, obs: Observation) -> list[Finding]:
        cfg = runtime.config
        values = obs.values
        findings: list[Finding] = []
        if obs.sensor_type == "imu":
            checks = (
                (
                    "IMU_ACCEL_SATURATION",
                    _vector_norm(values, ("accel", "linear_acceleration")),
                    cfg.imu_accel_limit,
                ),
                (
                    "IMU_GYRO_SATURATION",
                    _vector_norm(values, ("gyro", "angular_velocity")),
                    cfg.imu_gyro_limit,
                ),
            )
            for code, actual, limit in checks:
                if actual is not None and actual > limit:
                    findings.append(
                        self._finding(
                            runtime,
                            obs,
                            code,
                            Severity.CRITICAL,
                            "IMU 测量值疑似饱和",
                            "vector_norm",
                            round(actual, 4),
                            f"<= {limit}",
                            ["量程配置过小", "强烈冲击或振动", "驱动单位错误"],
                        )
                    )
        elif obs.sensor_type == "gnss":
            satellites = _number(values, "satellites", "satellite_count")
            fix = str(values.get("fix_type", values.get("status", ""))).lower()
            if fix in {"none", "no_fix", "invalid", "0"}:
                findings.append(
                    self._finding(
                        runtime,
                        obs,
                        "GNSS_NO_FIX",
                        Severity.CRITICAL,
                        "GNSS 未获得有效定位",
                        "fix_type",
                        fix,
                        "有效 fix",
                        ["遮挡", "天线或射频链路异常", "初始化未完成"],
                    )
                )
            if satellites is not None and satellites < cfg.gnss_min_satellites:
                findings.append(
                    self._finding(
                        runtime,
                        obs,
                        "GNSS_LOW_SATELLITES",
                        Severity.WARNING,
                        "可用卫星数不足",
                        "satellites",
                        int(satellites),
                        f">= {cfg.gnss_min_satellites}",
                        ["环境遮挡", "多路径效应", "天线安装不当"],
                    )
                )
        elif obs.sensor_type == "lidar":
            points = _number(values, "points_count", "point_count")
            if points is not None and points < cfg.lidar_min_points:
                findings.append(
                    self._finding(
                        runtime,
                        obs,
                        "LIDAR_SPARSE",
                        Severity.WARNING,
                        "LiDAR 点数过少",
                        "points_count",
                        int(points),
                        f">= {cfg.lidar_min_points}",
                        ["遮挡或污染", "网络丢包", "视场内有效目标过少"],
                    )
                )
        elif obs.sensor_type == "camera":
            features = _number(values, "feature_count", "tracked_features")
            if features is not None and features < cfg.camera_min_features:
                findings.append(
                    self._finding(
                        runtime,
                        obs,
                        "CAMERA_LOW_FEATURES",
                        Severity.WARNING,
                        "视觉有效特征不足",
                        "feature_count",
                        int(features),
                        f">= {cfg.camera_min_features}",
                        ["弱纹理", "过曝或欠曝", "运动模糊", "镜头污染"],
                    )
                )
        elif obs.sensor_type in {"pose", "localization", "slam"}:
            findings.extend(self._localization_findings(runtime, obs))
            findings.extend(self._slam_quality_findings(runtime, obs))
        elif obs.sensor_type in {"planner", "path", "global_planner"}:
            findings.extend(self._planner_findings(runtime, obs))
        elif obs.sensor_type in {"controller", "local_planner"}:
            findings.extend(self._controller_findings(runtime, obs))
        elif obs.sensor_type in {"system", "runtime", "network"}:
            findings.extend(self._runtime_findings(runtime, obs))
        elif obs.sensor_type in {"map", "costmap"}:
            age_ms = _number(values, "age_ms", "map_age_ms")
            if age_ms is not None and age_ms > cfg.path_max_age_ms:
                findings.append(
                    self._finding(
                        runtime,
                        obs,
                        "MAP_STALE",
                        Severity.CRITICAL,
                        "地图数据年龄超过阈值",
                        "map_age_ms",
                        round(age_ms, 3),
                        f"<= {cfg.path_max_age_ms}",
                        ["地图更新线程阻塞", "上游位姿或点云停止", "消息队列积压"],
                    )
                )
        return findings

    def _slam_quality_findings(
        self, runtime: SensorRuntime, obs: Observation
    ) -> list[Finding]:
        findings: list[Finding] = []
        inlier_ratio = _number(obs.values, "inlier_ratio", "match_inlier_ratio")
        if inlier_ratio is not None and inlier_ratio < runtime.config.slam_min_inlier_ratio:
            findings.append(
                self._finding(
                    runtime,
                    obs,
                    "SLAM_LOW_INLIERS",
                    Severity.CRITICAL,
                    "SLAM 有效匹配比例过低",
                    "inlier_ratio",
                    round(inlier_ratio, 4),
                    f">= {runtime.config.slam_min_inlier_ratio}",
                    ["场景几何退化", "初值错误", "点云去畸变或外参异常"],
                )
            )
        degeneracy = _number(obs.values, "degeneracy_score")
        if degeneracy is not None and degeneracy >= runtime.config.slam_degeneracy_warning:
            findings.append(
                self._finding(
                    runtime,
                    obs,
                    "SLAM_DEGENERATE",
                    Severity.WARNING,
                    "SLAM 几何约束疑似退化",
                    "degeneracy_score",
                    round(degeneracy, 4),
                    f"< {runtime.config.slam_degeneracy_warning}",
                    ["长走廊或大平面", "有效法向方向单一", "观测范围不足"],
                )
            )
        return findings

    def _planner_findings(
        self, runtime: SensorRuntime, obs: Observation
    ) -> list[Finding]:
        findings: list[Finding] = []
        path_age = _number(obs.values, "path_age_ms", "age_ms")
        if path_age is not None and path_age > runtime.config.path_max_age_ms:
            findings.append(
                self._finding(
                    runtime,
                    obs,
                    "PATH_STALE",
                    Severity.CRITICAL,
                    "规划或控制正在消费过期路径",
                    "path_age_ms",
                    round(path_age, 3),
                    f"<= {runtime.config.path_max_age_ms}",
                    ["规划队列积压", "旧路径重复发布", "时间基准不一致"],
                )
            )
        planning_time = _number(obs.values, "planning_time_ms")
        if (
            planning_time is not None
            and planning_time > runtime.config.planning_max_time_ms
        ):
            findings.append(
                self._finding(
                    runtime,
                    obs,
                    "PLANNING_SLOW",
                    Severity.WARNING,
                    "规划耗时超过阈值",
                    "planning_time_ms",
                    round(planning_time, 3),
                    f"<= {runtime.config.planning_max_time_ms}",
                    ["地图规模过大", "搜索空间或分辨率不合理", "CPU 调度竞争"],
                )
            )
        if obs.values.get("success") is False:
            findings.append(
                self._finding(
                    runtime,
                    obs,
                    "PLANNING_FAILED",
                    Severity.CRITICAL,
                    "规划器报告失败",
                    "success",
                    False,
                    "true",
                    ["起终点不在自由连通域", "地图/TF 不一致", "规划超时"],
                )
            )
        return findings

    def _controller_findings(
        self, runtime: SensorRuntime, obs: Observation
    ) -> list[Finding]:
        findings: list[Finding] = []
        tracking_error = _number(obs.values, "tracking_error_m", "cross_track_error_m")
        if (
            tracking_error is not None
            and tracking_error > runtime.config.tracking_error_max_m
        ):
            findings.append(
                self._finding(
                    runtime,
                    obs,
                    "TRACKING_ERROR",
                    Severity.WARNING,
                    "轨迹跟踪误差超过阈值",
                    "tracking_error_m",
                    round(tracking_error, 3),
                    f"<= {runtime.config.tracking_error_max_m}",
                    ["定位抖动", "路径曲率不连续", "控制参数与本体不匹配"],
                )
            )
        cycle_time = _number(obs.values, "cycle_time_ms")
        if cycle_time is not None and cycle_time > runtime.config.controller_cycle_max_ms:
            findings.append(
                self._finding(
                    runtime,
                    obs,
                    "CONTROLLER_CYCLE_MISS",
                    Severity.WARNING,
                    "控制周期超过 deadline",
                    "cycle_time_ms",
                    round(cycle_time, 3),
                    f"<= {runtime.config.controller_cycle_max_ms}",
                    ["控制线程阻塞", "代价计算过重", "CPU 调度竞争"],
                )
            )
        if obs.values.get("crashed") is True:
            findings.append(
                self._finding(
                    runtime,
                    obs,
                    "CONTROLLER_CRASH",
                    Severity.CRITICAL,
                    "控制进程异常退出",
                    "crashed",
                    True,
                    "false",
                    ["插件异常", "非法输入或参数", "内存错误"],
                )
            )
        return findings

    def _runtime_findings(
        self, runtime: SensorRuntime, obs: Observation
    ) -> list[Finding]:
        findings: list[Finding] = []
        checks = (
            (
                "QUEUE_BACKLOG",
                "消息队列深度超过阈值",
                _number(obs.values, "queue_depth"),
                runtime.config.queue_depth_max,
                ["消费者处理不足", "QoS history 过深", "大消息阻塞实时流"],
            ),
            (
                "CPU_SATURATION",
                "CPU 使用率超过阈值",
                _number(obs.values, "cpu_percent"),
                runtime.config.cpu_warning_percent,
                ["计算负载过高", "线程未隔离", "异常忙等"],
            ),
            (
                "MEMORY_PRESSURE",
                "内存使用率超过阈值",
                _number(obs.values, "memory_percent"),
                runtime.config.memory_warning_percent,
                ["缓存无界增长", "点云/地图未淘汰", "内存泄漏"],
            ),
            (
                "THERMAL_THROTTLE",
                "设备温度超过阈值",
                _number(obs.values, "temperature_c"),
                runtime.config.temperature_warning_c,
                ["散热不足", "持续高负载", "功耗模式不匹配"],
            ),
        )
        for code, summary, actual, limit, causes in checks:
            if actual is not None and actual > limit:
                findings.append(
                    self._finding(
                        runtime,
                        obs,
                        code,
                        Severity.WARNING,
                        summary,
                        code.lower(),
                        round(actual, 3),
                        f"<= {limit}",
                        causes,
                    )
                )
        return findings

    def _localization_findings(self, runtime: SensorRuntime, obs: Observation) -> list[Finding]:
        x = _number(obs.values, "x", "position_x")
        y = _number(obs.values, "y", "position_y")
        if x is None or y is None:
            return []
        cfg = runtime.config
        current = (x, y)
        findings: list[Finding] = []
        if runtime.last_pose is not None:
            delta = math.dist(runtime.last_pose, current)
            if delta > cfg.pose_jump_m:
                findings.append(
                    self._finding(
                        runtime,
                        obs,
                        "POSE_JUMP",
                        Severity.CRITICAL,
                        "相邻定位结果发生跳变",
                        "position_delta_m",
                        round(delta, 4),
                        f"<= {cfg.pose_jump_m}",
                        ["重定位跳变", "回环约束异常", "TF 或时间同步错误"],
                    )
                )
        wheel = self.latest_by_type.get("wheel_odometry") or self.latest_by_type.get("wheel")
        wheel_speed = _number(wheel.values, "speed", "linear_speed") if wheel else None
        stationary = bool(obs.metadata.get("stationary")) or (
            wheel is not None
            and abs(obs.timestamp - wheel.timestamp) < 1
            and wheel_speed is not None
            and abs(wheel_speed) < 0.02
        )
        if stationary:
            runtime.stationary_anchor = runtime.stationary_anchor or current
            drift = math.dist(runtime.stationary_anchor, current)
            if drift > cfg.stationary_drift_m:
                findings.append(
                    self._finding(
                        runtime,
                        obs,
                        "STATIONARY_DRIFT",
                        Severity.CRITICAL,
                        "机器人静止时定位持续移动",
                        "drift_m",
                        round(drift, 4),
                        f"<= {cfg.stationary_drift_m}",
                        ["SLAM 匹配漂移", "IMU 零偏", "外参或时间同步错误"],
                    )
                )
        else:
            runtime.stationary_anchor = None
        covariance = _number(obs.values, "position_covariance", "covariance")
        if covariance is not None and covariance > 1:
            findings.append(
                self._finding(
                    runtime,
                    obs,
                    "LOCALIZATION_UNCERTAIN",
                    Severity.WARNING,
                    "定位协方差较高",
                    "position_covariance",
                    round(covariance, 6),
                    "<= 1",
                    ["环境退化", "有效观测不足", "融合滤波未收敛"],
                )
            )
        runtime.last_pose = current
        return findings

    def _remember_values(self, runtime: SensorRuntime, obs: Observation) -> None:
        for key, value in obs.values.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                window = runtime.value_windows.setdefault(
                    key, deque(maxlen=runtime.config.frozen_window)
                )
                window.append(float(value))

    def report(self) -> DiagnosticReport:
        sensor_reports: list[SensorReport] = []
        for runtime in self.runtimes.values():
            if (
                self.ended_at is not None
                and runtime.last_timestamp is not None
                and self.ended_at - runtime.last_timestamp > runtime.config.stale_seconds
            ):
                runtime.state = "OFFLINE"
                runtime.record(
                    Finding(
                        code="DATA_STALE",
                        severity=Severity.CRITICAL,
                        sensor_id=runtime.sensor_id,
                        timestamp=runtime.last_timestamp,
                        summary="该数据源在记录结束前已停止更新",
                        evidence=[
                            Evidence(
                                metric="silent_seconds",
                                actual=round(self.ended_at - runtime.last_timestamp, 3),
                                expected=f"<= {runtime.config.stale_seconds}",
                            )
                        ],
                        possible_causes=["节点退出", "通信中断", "传感器掉电"],
                    )
                )
            findings = sorted(
                runtime.findings.values(), key=lambda item: (-item.severity, item.code)
            )
            penalty = sum(25 if item.severity == Severity.CRITICAL else 10 for item in findings)
            score = max(0, 100 - penalty)
            sensor_reports.append(
                SensorReport(
                    sensor_id=runtime.sensor_id,
                    sensor_type=runtime.sensor_type,
                    state=runtime.state,
                    health_score=score,
                    samples=runtime.samples,
                    metrics=runtime.metrics(),
                    findings=findings,
                )
            )
        overall = (
            round(sum(report.health_score for report in sensor_reports) / len(sensor_reports))
            if sensor_reports
            else 0
        )
        return DiagnosticReport(
            generated_at=datetime.now(timezone.utc).isoformat(),
            source=self.source,
            observations=self.observations,
            started_at=self.started_at,
            ended_at=self.ended_at,
            overall_score=overall,
            sensors=sorted(sensor_reports, key=lambda item: item.sensor_id),
        )
