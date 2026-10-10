from __future__ import annotations

from esis.engine import DiagnosticEngine
from esis.fault_graph import diagnose, render_dot, render_mermaid, symptom_codes
from esis.models import Observation
from esis.reporting import render_markdown


def _oscillation_report():
    engine = DiagnosticEngine(source="test-oscillation.jsonl")
    engine.process(
        Observation(
            sensor_id="localization",
            sensor_type="localization",
            timestamp=0,
            sequence=1,
            values={"x": 0.0, "y": 0.0},
        )
    )
    engine.process(
        Observation(
            sensor_id="localization",
            sensor_type="localization",
            timestamp=0.1,
            sequence=2,
            values={"x": 2.0, "y": 0.0},
        )
    )
    engine.process(
        Observation(
            sensor_id="planner",
            sensor_type="planner",
            timestamp=0.1,
            sequence=1,
            values={"path_age_ms": 450.0, "success": True},
        )
    )
    return engine.report()


def test_fault_graph_ranks_hypotheses_from_evidence() -> None:
    diagnosis = diagnose(_oscillation_report(), "NAV_OSCILLATION")

    assert diagnosis.symptom == "LOCAL_PLANNER_OSCILLATION"
    assert diagnosis.ranked_hypotheses[0].matched_findings
    assert diagnosis.ranked_hypotheses[0].probes
    assert {
        "H_LOCALIZATION_JITTER",
        "H_STALE_CONTROL_INPUT",
    }.issubset({item.id for item in diagnosis.ranked_hypotheses[:3]})


def test_fault_graph_exports_mermaid_and_dot() -> None:
    mermaid = render_mermaid("POSE_JUMP")
    dot = render_dot("POSE_JUMP")

    assert "flowchart LR" in mermaid
    assert "检查重定位门控" in mermaid
    assert "digraph fault_graph" in dot


def test_markdown_report_contains_next_probe() -> None:
    report = _oscillation_report()
    diagnosis = diagnose(report, "LOCAL_PLANNER_OSCILLATION")
    content = render_markdown(report, diagnosis)

    assert "# SlamDoctor Diagnostic Report" in content
    assert "Fault-graph diagnosis" in content
    assert "Next probes" in content


def test_all_required_symptoms_are_available() -> None:
    assert {
        "LOCALIZATION_DRIFT",
        "POSE_JUMP",
        "MAP_GHOSTING",
        "GLOBAL_PLAN_FAILED",
        "LOCAL_PLANNER_OSCILLATION",
        "CONTROLLER_CRASH",
        "END_TO_END_LATENCY",
        "NETWORK_RECOVERY_FAILED",
    } == set(symptom_codes())
