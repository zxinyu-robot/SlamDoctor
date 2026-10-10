from __future__ import annotations

from .models import DiagnosticReport, FaultDiagnosis


def render_markdown(report: DiagnosticReport, diagnosis: FaultDiagnosis | None = None) -> str:
    lines = [
        "# SlamDoctor Diagnostic Report",
        "",
        f"- Source: `{report.source}`",
        f"- Observations: {report.observations}",
        f"- Overall health: **{report.overall_score}/100**",
        f"- Generated: {report.generated_at}",
        "",
        "## Runtime health",
        "",
        "| Source | Type | State | Score | Frequency | Drop rate | P95 latency |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for sensor in report.sensors:
        metrics = sensor.metrics
        lines.append(
            "| "
            + " | ".join(
                (
                    sensor.sensor_id,
                    sensor.sensor_type,
                    sensor.state,
                    str(sensor.health_score),
                    _display(metrics.get("frequency_hz")),
                    _display(metrics.get("drop_rate")),
                    _display(metrics.get("latency_p95_ms")),
                )
            )
            + " |"
        )

    lines.extend(["", "## Contract findings", ""])
    findings = [finding for sensor in report.sensors for finding in sensor.findings]
    if not findings:
        lines.append("No contract violations were detected.")
    else:
        for finding in findings:
            evidence = "; ".join(
                f"`{item.metric}={item.actual}` expected {item.expected}"
                for item in finding.evidence
            )
            lines.extend(
                [
                    f"### {finding.severity.name}: {finding.code}",
                    "",
                    f"{finding.sensor_id}: {finding.summary}",
                    "",
                    f"- Evidence: {evidence}",
                    f"- Candidate causes: {', '.join(finding.possible_causes)}",
                    "",
                ]
            )

    if diagnosis is not None:
        lines.extend(
            [
                "## Fault-graph diagnosis",
                "",
                f"**{diagnosis.symptom}** — {diagnosis.summary}",
                "",
            ]
        )
        for index, hypothesis in enumerate(diagnosis.ranked_hypotheses, start=1):
            lines.extend(
                [
                    f"### {index}. {hypothesis.title} ({hypothesis.score:.0%})",
                    "",
                    hypothesis.rationale,
                    "",
                ]
            )
            if hypothesis.probes:
                lines.append("Next probes:")
                for probe in hypothesis.probes:
                    lines.append(
                        f"- **{probe.title}** (cost {probe.cost}/5): "
                        f"{probe.instructions} Expected: {probe.expected}"
                    )
            if hypothesis.remediations:
                lines.append("Candidate remediations:")
                lines.extend(f"- {item}" for item in hypothesis.remediations)
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _display(value: object) -> str:
    return "-" if value is None else str(value)
