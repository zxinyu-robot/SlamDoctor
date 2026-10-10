from __future__ import annotations

from esis.config import AppConfig, Thresholds
from esis.engine import DiagnosticEngine
from esis.models import Observation
from esis.simulator import simulate


def run_simulation(sensor: str, fault: str) -> tuple[DiagnosticEngine, set[str]]:
    config = AppConfig(
        defaults=Thresholds(
            expected_frequency_hz=10,
            latency_warning_ms=50,
            latency_critical_ms=200,
        )
    )
    engine = DiagnosticEngine(config)
    codes: set[str] = set()
    for observation in simulate(sensor_type=sensor, fault=fault):
        codes.update(finding.code for finding in engine.process(observation))
    return engine, codes


def test_healthy_imu_has_full_score() -> None:
    engine, codes = run_simulation("imu", "none")
    report = engine.report()

    assert not codes
    assert report.overall_score == 100
    assert report.sensors[0].state == "HEALTHY"
    assert report.sensors[0].metrics["frequency_hz"] == 10


def test_frame_drop_is_detected() -> None:
    engine, codes = run_simulation("imu", "frame-drop")

    assert "FRAME_DROP" in codes
    assert engine.report().sensors[0].health_score < 100


def test_stationary_localization_drift_is_detected() -> None:
    _, codes = run_simulation("localization", "drift")

    assert "STATIONARY_DRIFT" in codes


def test_stopped_sensor_is_marked_offline() -> None:
    engine = DiagnosticEngine(AppConfig(defaults=Thresholds(stale_seconds=1)))
    engine.process(
        Observation(sensor_id="imu", sensor_type="imu", timestamp=0, sequence=1, values={})
    )
    engine.process(
        Observation(sensor_id="camera", sensor_type="camera", timestamp=3, sequence=1, values={})
    )

    sensors = {sensor.sensor_id: sensor for sensor in engine.report().sensors}
    assert sensors["imu"].state == "OFFLINE"
    assert any(finding.code == "DATA_STALE" for finding in sensors["imu"].findings)


def test_time_regression_contains_evidence() -> None:
    engine = DiagnosticEngine()
    engine.process(
        Observation(sensor_id="gnss", sensor_type="gnss", timestamp=2, sequence=1, values={})
    )
    findings = engine.process(
        Observation(sensor_id="gnss", sensor_type="gnss", timestamp=1, sequence=2, values={})
    )

    finding = next(item for item in findings if item.code == "TIME_REGRESSION")
    assert finding.evidence[0].metric == "timestamp"
    assert finding.possible_causes


def test_slam_quality_and_tf_contracts_are_checked() -> None:
    engine = DiagnosticEngine()
    findings = engine.process(
        Observation(
            sensor_id="slam",
            sensor_type="slam",
            timestamp=1,
            sequence=1,
            values={
                "x": 0,
                "y": 0,
                "inlier_ratio": 0.1,
                "degeneracy_score": 0.95,
            },
            metadata={"frame_id": "map", "tf_available": False},
        )
    )
    codes = {finding.code for finding in findings}

    assert {"SLAM_LOW_INLIERS", "SLAM_DEGENERATE", "TF_UNAVAILABLE"} <= codes


def test_planner_controller_and_runtime_contracts_are_checked() -> None:
    engine = DiagnosticEngine()
    planner = engine.process(
        Observation(
            sensor_id="planner",
            sensor_type="planner",
            timestamp=1,
            values={"path_age_ms": 500, "planning_time_ms": 900, "success": False},
        )
    )
    controller = engine.process(
        Observation(
            sensor_id="controller",
            sensor_type="controller",
            timestamp=1,
            values={"tracking_error_m": 1.2, "cycle_time_ms": 150, "crashed": True},
        )
    )
    runtime = engine.process(
        Observation(
            sensor_id="system",
            sensor_type="system",
            timestamp=1,
            values={
                "queue_depth": 12,
                "cpu_percent": 98,
                "memory_percent": 95,
                "temperature_c": 90,
            },
        )
    )
    codes = {item.code for item in planner + controller + runtime}

    assert {
        "PATH_STALE",
        "PLANNING_SLOW",
        "PLANNING_FAILED",
        "TRACKING_ERROR",
        "CONTROLLER_CYCLE_MISS",
        "CONTROLLER_CRASH",
        "QUEUE_BACKLOG",
        "CPU_SATURATION",
        "MEMORY_PRESSURE",
        "THERMAL_THROTTLE",
    } <= codes
