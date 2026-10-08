from __future__ import annotations

import math
import random
from collections.abc import Iterator

from .models import Observation


def simulate(
    sensor_type: str = "imu",
    fault: str = "none",
    samples: int = 100,
    frequency_hz: float = 10,
    seed: int = 7,
) -> Iterator[Observation]:
    random.seed(seed)
    sensor_type = sensor_type.lower()
    sensor_id = f"{sensor_type}_demo"
    sequence = 0
    for index in range(samples):
        timestamp = index / frequency_hz
        sequence += 3 if fault == "frame-drop" and index == samples // 2 else 1
        receive_timestamp = timestamp + random.uniform(0.002, 0.008)
        if fault == "latency" and index >= samples // 2:
            receive_timestamp += 0.35
        values, metadata = _values(sensor_type, index, frequency_hz, fault, samples)
        yield Observation(
            sensor_id=sensor_id,
            sensor_type=sensor_type,
            timestamp=timestamp,
            receive_timestamp=receive_timestamp,
            sequence=sequence,
            payload_size=128,
            values=values,
            metadata=metadata,
        )


def _values(
    sensor_type: str, index: int, frequency_hz: float, fault: str, samples: int
) -> tuple[dict[str, object], dict[str, object]]:
    noise = 0 if fault == "frozen" and index >= samples // 2 else random.uniform(-0.01, 0.01)
    if sensor_type == "imu":
        return {
            "accel_x": noise,
            "accel_y": noise,
            "accel_z": 9.81 + noise,
            "gyro_x": noise,
            "gyro_y": noise,
            "gyro_z": noise,
        }, {}
    if sensor_type == "gnss":
        lost = fault == "gnss-loss" and index >= samples // 2
        return {
            "latitude": 31.2304 + noise * 1e-5,
            "longitude": 121.4737 + noise * 1e-5,
            "satellites": 2 if lost else 16,
            "fix_type": "no_fix" if lost else "rtk_fixed",
        }, {}
    if sensor_type == "lidar":
        return {
            "points_count": 300 if fault == "sparse" and index >= samples // 2 else 60000,
            "mean_range": 8 + noise,
        }, {}
    if sensor_type == "camera":
        return {
            "feature_count": 5 if fault == "low-features" and index >= samples // 2 else 260,
            "brightness": 120 + noise,
        }, {}
    if sensor_type in {"pose", "localization", "slam"}:
        drift = index * 0.015 if fault == "drift" else 0
        jump = 2.5 if fault == "pose-jump" and index >= samples // 2 else 0
        return {
            "x": drift + jump + noise * 0.01,
            "y": drift * 0.3 + noise * 0.01,
            "yaw": math.sin(index / frequency_hz) * 0.001,
            "position_covariance": 0.02,
        }, {"stationary": True}
    return {"value": noise}, {}
