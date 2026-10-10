from __future__ import annotations

from enum import IntEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


class Severity(IntEnum):
    INFO = 0
    WARNING = 1
    CRITICAL = 2


class Observation(BaseModel):
    """A runtime-neutral sensor or subsystem observation."""

    model_config = ConfigDict(extra="forbid")

    sensor_id: str = Field(min_length=1)
    sensor_type: str = Field(min_length=1)
    timestamp: float
    receive_timestamp: float | None = None
    sequence: int | None = Field(default=None, ge=0)
    payload_size: int | None = Field(default=None, ge=0)
    values: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("sensor_type")
    @classmethod
    def normalize_sensor_type(cls, value: str) -> str:
        return value.strip().lower()


class Evidence(BaseModel):
    metric: str
    actual: float | int | str | bool | None
    expected: str


class Finding(BaseModel):
    code: str
    severity: Severity
    sensor_id: str
    timestamp: float
    summary: str
    evidence: list[Evidence] = Field(default_factory=list)
    possible_causes: list[str] = Field(default_factory=list)

    @field_serializer("severity")
    def serialize_severity(self, severity: Severity) -> str:
        return severity.name


class SensorReport(BaseModel):
    sensor_id: str
    sensor_type: str
    state: str
    health_score: int = Field(ge=0, le=100)
    samples: int
    metrics: dict[str, float | int | None]
    findings: list[Finding]


class DiagnosticReport(BaseModel):
    generated_at: str
    source: str
    observations: int
    started_at: float | None
    ended_at: float | None
    overall_score: int
    sensors: list[SensorReport]


class ProbeSpec(BaseModel):
    id: str
    title: str
    instructions: str
    expected: str
    cost: int = Field(default=1, ge=1, le=5)


class RankedHypothesis(BaseModel):
    id: str
    title: str
    score: float = Field(ge=0, le=1)
    rationale: str
    matched_findings: list[str] = Field(default_factory=list)
    probes: list[ProbeSpec] = Field(default_factory=list)
    remediations: list[str] = Field(default_factory=list)


class FaultDiagnosis(BaseModel):
    symptom: str
    summary: str
    source: str
    ranked_hypotheses: list[RankedHypothesis]
    observed_findings: list[str]
