from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import load_config
from .engine import DiagnosticEngine
from .fault_graph import diagnose, render_dot, render_mermaid, symptom_codes
from .io import read_observations, write_jsonl
from .models import DiagnosticReport, FaultDiagnosis, Finding, Severity
from .reporting import render_markdown
from .simulator import simulate as generate_simulation

app = typer.Typer(
    name="slam-doctor",
    help="基于接口契约、运行证据和故障图的 SLAM 导航诊断工具。",
    no_args_is_help=True,
)
console = Console()


def _version(value: bool) -> None:
    if value:
        console.print(f"slam-doctor {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option("--version", callback=_version, is_eager=True, help="显示版本。"),
    ] = None,
) -> None:
    """SlamDoctor diagnostics CLI."""


def _run(path: Path | None, config_path: Path | None, source: str) -> DiagnosticReport:
    engine = DiagnosticEngine(load_config(config_path), source=source)
    for observation in read_observations(path):
        engine.process(observation)
    return engine.report()


def _render(report: DiagnosticReport) -> None:
    score_style = "green" if report.overall_score >= 80 else "yellow"
    if report.overall_score < 50:
        score_style = "red"
    console.print(
        f"\n总体健康分: [{score_style}]{report.overall_score}/100[/{score_style}]  "
        f"观测数: {report.observations}"
    )
    table = Table(title="传感器状态")
    table.add_column("传感器")
    table.add_column("类型")
    table.add_column("状态")
    table.add_column("健康分", justify="right")
    table.add_column("频率 Hz", justify="right")
    table.add_column("丢帧率", justify="right")
    table.add_column("P95 延迟 ms", justify="right")
    for sensor in report.sensors:
        metrics = sensor.metrics
        table.add_row(
            sensor.sensor_id,
            sensor.sensor_type,
            sensor.state,
            str(sensor.health_score),
            _display(metrics.get("frequency_hz")),
            _display(metrics.get("drop_rate")),
            _display(metrics.get("latency_p95_ms")),
        )
    console.print(table)
    findings = [finding for sensor in report.sensors for finding in sensor.findings]
    if not findings:
        console.print("[green]未发现异常。[/green]")
        return
    console.print("\n[bold]异常与证据[/bold]")
    for finding in findings:
        color = "red" if finding.severity == Severity.CRITICAL else "yellow"
        evidence = "; ".join(
            f"{item.metric}={item.actual}（期望 {item.expected}）" for item in finding.evidence
        )
        causes = "、".join(finding.possible_causes)
        console.print(
            f"[{color}]{finding.code}[/{color}] {finding.sensor_id}: "
            f"{finding.summary}\n  证据: {evidence}\n  可能原因: {causes}"
        )


def _display(value: object) -> str:
    return "-" if value is None else str(value)


def _render_diagnosis(diagnosis: FaultDiagnosis) -> None:
    console.print(f"\n[bold]{diagnosis.symptom}[/bold] — {diagnosis.summary}")
    table = Table(title="根因假设排序")
    table.add_column("#", justify="right")
    table.add_column("假设")
    table.add_column("分数", justify="right")
    table.add_column("证据")
    table.add_column("下一 Probe")
    for index, hypothesis in enumerate(diagnosis.ranked_hypotheses, start=1):
        probe = hypothesis.probes[0].title if hypothesis.probes else "-"
        evidence = "、".join(hypothesis.matched_findings) or "暂无直接证据"
        table.add_row(
            str(index),
            hypothesis.title,
            f"{hypothesis.score:.0%}",
            evidence,
            probe,
        )
    console.print(table)
    top = diagnosis.ranked_hypotheses[0] if diagnosis.ranked_hypotheses else None
    if top and top.probes:
        probe = top.probes[0]
        console.print(
            f"\n[bold]建议首先执行：{probe.title}[/bold]\n"
            f"  操作：{probe.instructions}\n"
            f"  预期：{probe.expected}"
        )


def _write_report(report: DiagnosticReport, output: Path | None) -> None:
    content = report.model_dump_json(indent=2)
    if output:
        output.write_text(content + "\n", encoding="utf-8")
        console.print(f"报告已写入 {output}")
    else:
        console.print_json(content)


@app.command("validate")
def validate_command(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
) -> None:
    """逐行校验 JSONL 或 CSV 数据，不执行诊断。"""
    count = 0
    try:
        for _ in read_observations(path):
            count += 1
    except (ValueError, ValidationError) as exc:
        console.print(f"[red]校验失败:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]校验通过[/green]，共 {count} 条观测。")


@app.command("inspect")
def inspect_command(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    config: Annotated[
        Path | None, typer.Option("--config", "-c", exists=True, dir_okay=False)
    ] = None,
    output_format: Annotated[
        str, typer.Option("--format", "-f", help="terminal 或 json")
    ] = "terminal",
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
) -> None:
    """对完整记录执行分层诊断。"""
    try:
        report = _run(path, config, str(path))
    except (ValueError, ValidationError) as exc:
        console.print(f"[red]诊断失败:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    if output_format == "json" or output:
        _write_report(report, output)
    elif output_format == "terminal":
        _render(report)
    else:
        console.print("[red]--format 仅支持 terminal 或 json[/red]")
        raise typer.Exit(code=2)


@app.command("diagnose")
def diagnose_command(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    symptom: Annotated[str, typer.Option("--symptom", "-s")],
    config: Annotated[
        Path | None, typer.Option("--config", "-c", exists=True, dir_okay=False)
    ] = None,
    output_format: Annotated[
        str, typer.Option("--format", "-f", help="terminal 或 json")
    ] = "terminal",
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
) -> None:
    """根据运行证据和故障图对指定症状进行根因排序。"""
    try:
        report = _run(path, config, str(path))
        diagnosis = diagnose(report, symptom)
    except (ValueError, ValidationError) as exc:
        console.print(f"[red]诊断失败:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    if output_format == "json" or output:
        content = diagnosis.model_dump_json(indent=2)
        if output:
            output.write_text(content + "\n", encoding="utf-8")
            console.print(f"故障图诊断已写入 {output}")
        else:
            console.print_json(content)
    elif output_format == "terminal":
        _render(report)
        _render_diagnosis(diagnosis)
    else:
        console.print("[red]--format 仅支持 terminal 或 json[/red]")
        raise typer.Exit(code=2)


@app.command("graph")
def graph_command(
    symptom: Annotated[str, typer.Option("--symptom", "-s")],
    output_format: Annotated[
        str, typer.Option("--format", "-f", help="mermaid 或 dot")
    ] = "mermaid",
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
) -> None:
    """导出症状—假设—Probe 故障图。"""
    try:
        content = (
            render_mermaid(symptom)
            if output_format == "mermaid"
            else render_dot(symptom)
            if output_format == "dot"
            else None
        )
    except ValueError as exc:
        console.print(f"[red]导出失败:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    if content is None:
        console.print("[red]--format 仅支持 mermaid 或 dot[/red]")
        raise typer.Exit(code=2)
    if output:
        output.write_text(content, encoding="utf-8")
        console.print(f"故障图已写入 {output}")
    else:
        console.print(content, markup=False)


@app.command("report")
def report_command(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    symptom: Annotated[str | None, typer.Option("--symptom", "-s")] = None,
    config: Annotated[
        Path | None, typer.Option("--config", "-c", exists=True, dir_okay=False)
    ] = None,
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(
        "slamdoctor-report.md"
    ),
) -> None:
    """生成带契约异常、根因假设和下一 Probe 的 Markdown 报告。"""
    try:
        report = _run(path, config, str(path))
        diagnosis = diagnose(report, symptom) if symptom else None
    except (ValueError, ValidationError) as exc:
        console.print(f"[red]报告生成失败:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    output.write_text(render_markdown(report, diagnosis), encoding="utf-8")
    console.print(f"Markdown 报告已写入 {output}")


@app.command("symptoms")
def symptoms_command() -> None:
    """列出内置故障图支持的症状。"""
    for code in symptom_codes():
        console.print(code)


@app.command("watch")
def watch_command(
    config: Annotated[
        Path | None, typer.Option("--config", "-c", exists=True, dir_okay=False)
    ] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
) -> None:
    """从标准输入持续读取 JSONL，结束后生成诊断报告。"""
    try:
        report = _run(None, config, "stdin")
    except (ValueError, ValidationError) as exc:
        console.print(f"[red]诊断失败:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    _write_report(report, output) if output else _render(report)


@app.command("simulate")
def simulate_command(
    sensor: Annotated[str, typer.Option("--sensor", "-s")] = "imu",
    fault: Annotated[str, typer.Option("--fault")] = "none",
    samples: Annotated[int, typer.Option("--samples", min=1)] = 100,
    frequency: Annotated[float, typer.Option("--frequency", min=0.1)] = 10,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
) -> None:
    """生成可复现的正常或故障 JSONL 数据。"""
    observations = generate_simulation(sensor, fault, samples, frequency)
    if output:
        with output.open("w", encoding="utf-8") as handle:
            write_jsonl(observations, handle)
        console.print(f"模拟数据已写入 {output}")
    else:
        import sys

        write_jsonl(observations, sys.stdout)


@app.command("replay")
def replay_command(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    config: Annotated[
        Path | None, typer.Option("--config", "-c", exists=True, dir_okay=False)
    ] = None,
    speed: Annotated[float, typer.Option("--speed", min=0.01)] = 10,
) -> None:
    """按时间顺序回放记录，并实时显示新发现的异常。"""
    engine = DiagnosticEngine(load_config(config), source=str(path))
    previous_timestamp: float | None = None
    for observation in read_observations(path):
        if previous_timestamp is not None:
            time.sleep(max(0, observation.timestamp - previous_timestamp) / speed)
        for finding in engine.process(observation):
            _render_live_finding(finding)
        previous_timestamp = observation.timestamp
    _render(engine.report())


def _render_live_finding(finding: Finding) -> None:
    payload = {
        "timestamp": finding.timestamp,
        "sensor_id": finding.sensor_id,
        "severity": finding.severity.name,
        "code": finding.code,
        "summary": finding.summary,
    }
    console.print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    app()
