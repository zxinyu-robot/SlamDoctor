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
from .io import read_observations, write_jsonl
from .models import DiagnosticReport, Finding, Severity
from .simulator import simulate as generate_simulation

app = typer.Typer(
    name="esis",
    help="机器人传感器、定位与系统行为的可解释分层诊断工具。",
    no_args_is_help=True,
)
console = Console()


def _version(value: bool) -> None:
    if value:
        console.print(f"esis {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option("--version", callback=_version, is_eager=True, help="显示版本。"),
    ] = None,
) -> None:
    """ESIS diagnostics CLI."""


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
