from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field


class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_frequency_hz: float | None = Field(default=None, gt=0)
    frequency_tolerance: float = Field(default=0.25, ge=0, le=1)
    latency_warning_ms: float = Field(default=50, ge=0)
    latency_critical_ms: float = Field(default=200, ge=0)
    drop_warning_rate: float = Field(default=0.01, ge=0, le=1)
    drop_critical_rate: float = Field(default=0.05, ge=0, le=1)
    stale_seconds: float = Field(default=2, gt=0)
    frozen_window: int = Field(default=8, ge=3)
    frozen_variance: float = Field(default=1e-9, ge=0)
    pose_jump_m: float = Field(default=1.5, gt=0)
    stationary_drift_m: float = Field(default=0.3, gt=0)
    imu_accel_limit: float = Field(default=160, gt=0)
    imu_gyro_limit: float = Field(default=35, gt=0)
    gnss_min_satellites: int = Field(default=6, ge=0)
    lidar_min_points: int = Field(default=1000, ge=0)
    camera_min_features: int = Field(default=30, ge=0)


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    defaults: Thresholds = Field(default_factory=Thresholds)
    sensors: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def for_sensor(self, sensor_id: str) -> Thresholds:
        values = self.defaults.model_dump()
        override = deepcopy(self.sensors.get(sensor_id, {}))
        override.pop("type", None)
        values.update(override)
        return Thresholds.model_validate(values)


def load_config(path: Path | None = None) -> AppConfig:
    if path is None:
        return AppConfig()
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return AppConfig.model_validate(data)
