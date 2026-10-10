from __future__ import annotations

from dataclasses import dataclass

from .models import (
    DiagnosticReport,
    FaultDiagnosis,
    ProbeSpec,
    RankedHypothesis,
)


@dataclass(frozen=True)
class Hypothesis:
    id: str
    title: str
    evidence_weights: dict[str, float]
    probes: tuple[ProbeSpec, ...]
    remediations: tuple[str, ...]
    prior: float = 0.08


@dataclass(frozen=True)
class Symptom:
    code: str
    summary: str
    hypotheses: tuple[Hypothesis, ...]


def _probe(
    probe_id: str,
    title: str,
    instructions: str,
    expected: str,
    cost: int = 1,
) -> ProbeSpec:
    return ProbeSpec(
        id=probe_id,
        title=title,
        instructions=instructions,
        expected=expected,
        cost=cost,
    )


TIME_PROBE = _probe(
    "CHECK_EVENT_AGE",
    "检查端到端数据年龄",
    "比较 source、publish、receive、consume 时间，按链路分解 P50/P95/P99。",
    "关键控制输入的数据年龄低于契约 deadline，且时钟基准一致。",
)
TF_PROBE = _probe(
    "CHECK_TF",
    "检查 TF 与坐标语义",
    "验证 TF 树连通、时间可外推、frame_id 正确且无重复发布者。",
    "目标时刻可解析唯一变换，静态外参与配置版本一致。",
)
REPLAY_PROBE = _probe(
    "OFFLINE_REPLAY",
    "同一记录离线回放",
    "使用相同参数离线回放 rosbag/JSONL，并与在线结果对比。",
    "若离线正常而在线异常，优先定位资源、队列或通信问题。",
    2,
)
POSE_FREEZE_PROBE = _probe(
    "FREEZE_POSE_INPUT",
    "冻结定位输入隔离导航",
    "在仿真或离线回放中给规划控制链输入稳定已知 Pose。",
    "若振荡消失，根因位于定位、TF 或地图同步上游。",
    2,
)
PATH_INJECT_PROBE = _probe(
    "INJECT_KNOWN_PATH",
    "注入已验证平滑路径",
    "在仿真或受控环境绕过全局规划，输入曲率连续的参考路径。",
    "若局部控制恢复，根因位于全局路径或 BT 转发。",
    2,
)
RESOURCE_PROBE = _probe(
    "CHECK_RESOURCES",
    "检查资源与调度",
    "采集 CPU 单核、GPU、内存、温度、线程阻塞和消息队列深度。",
    "无持续饱和、热降频、内存增长或队列积压。",
)


SYMPTOMS: dict[str, Symptom] = {
    "LOCALIZATION_DRIFT": Symptom(
        "LOCALIZATION_DRIFT",
        "定位长期漂移或静止时位姿持续移动",
        (
            Hypothesis(
                "H_TIME_SYNC",
                "LiDAR/IMU 时间同步或去畸变异常",
                {"TIME_REGRESSION": 0.35, "HIGH_LATENCY": 0.25, "FREQUENCY_DRIFT": 0.15},
                (TIME_PROBE, REPLAY_PROBE),
                ("统一传感器时钟基准", "重新估计时间偏移并验证点云去畸变"),
                0.16,
            ),
            Hypothesis(
                "H_GEOMETRY_DEGENERACY",
                "场景几何退化或匹配约束不足",
                {"SLAM_DEGENERATE": 0.45, "SLAM_LOW_INLIERS": 0.35, "LOCALIZATION_UNCERTAIN": 0.2},
                (
                    _probe(
                        "CHECK_OBSERVABILITY",
                        "检查匹配可观测性",
                        "检查 inlier、残差、信息矩阵特征值及退化方向。",
                        "各受控自由度具有足够几何约束。",
                    ),
                ),
                ("增加退化检测和安全降级", "引入回环或外部锚点"),
                0.14,
            ),
            Hypothesis(
                "H_EXTRINSIC",
                "传感器外参或 TF 配置错误",
                {"FRAME_CHANGED": 0.3, "TF_UNAVAILABLE": 0.45, "POSE_JUMP": 0.15},
                (TF_PROBE,),
                ("重新标定外参", "锁定标定版本并消除重复 TF 发布者"),
                0.11,
            ),
            Hypothesis(
                "H_IMU",
                "IMU 饱和、振动或零偏未收敛",
                {"IMU_ACCEL_SATURATION": 0.35, "IMU_GYRO_SATURATION": 0.35, "VALUE_FROZEN": 0.2},
                (
                    _probe(
                        "CHECK_IMU",
                        "检查 IMU 统计",
                        "检查 bias、饱和、振动频谱、单位和安装方向。",
                        "静止均值与噪声满足传感器规格。",
                    ),
                ),
                ("修正单位和安装方向", "增加减振并重新初始化 bias"),
                0.1,
            ),
        ),
    ),
    "POSE_JUMP": Symptom(
        "POSE_JUMP",
        "位姿或 map→odom 突然跳变",
        (
            Hypothesis(
                "H_BAD_RELOCALIZATION",
                "错误重定位或错误回环约束",
                {"POSE_JUMP": 0.5, "SLAM_LOW_INLIERS": 0.2, "LOCALIZATION_UNCERTAIN": 0.15},
                (
                    _probe(
                        "CHECK_RELOCALIZATION",
                        "检查重定位门控",
                        "核对候选、配准 fitness、overlap、位姿跳变量和协方差。",
                        "只有通过几何与时间一致性门控的候选被接受。",
                    ),
                ),
                ("提高配准门控", "增加连续健康帧确认和错误候选黑名单"),
                0.17,
            ),
            Hypothesis(
                "H_TF_CONFLICT",
                "TF 重复发布或 map/odom 所有权冲突",
                {"TF_UNAVAILABLE": 0.4, "FRAME_CHANGED": 0.35, "POSE_JUMP": 0.2},
                (TF_PROBE,),
                ("明确 map→odom 唯一写入者", "修正 frame_id 和生命周期"),
                0.15,
            ),
            Hypothesis(
                "H_OUT_OF_ORDER_POSE",
                "位姿乱序或旧版本覆盖新版本",
                {"TIME_REGRESSION": 0.4, "SEQUENCE_DISORDER": 0.35, "HIGH_LATENCY": 0.2},
                (TIME_PROBE,),
                ("按事件时间和序列号拒绝旧状态", "收敛 DDS history 和队列"),
                0.12,
            ),
        ),
    ),
    "MAP_GHOSTING": Symptom(
        "MAP_GHOSTING",
        "地图重影、撕裂或动态物体长期残留",
        (
            Hypothesis(
                "H_DESKEW",
                "点云去畸变或时间同步异常",
                {"HIGH_LATENCY": 0.25, "TIME_REGRESSION": 0.35, "FREQUENCY_DRIFT": 0.15},
                (TIME_PROBE, REPLAY_PROBE),
                ("修正 LiDAR/IMU 对齐", "使用原始时间戳而非接收时间"),
                0.16,
            ),
            Hypothesis(
                "H_POSE_NOISE",
                "建图位姿抖动或漂移",
                {"POSE_JUMP": 0.35, "STATIONARY_DRIFT": 0.35, "LOCALIZATION_UNCERTAIN": 0.2},
                (POSE_FREEZE_PROBE,),
                ("先修复定位健康度", "地图写入前增加位姿质量门控"),
                0.15,
            ),
            Hypothesis(
                "H_DYNAMIC_RESIDUE",
                "动态点未分离或清除语义错误",
                {"MAP_STALE": 0.25, "QUEUE_BACKLOG": 0.15},
                (
                    _probe(
                        "CHECK_MAP_LIFECYCLE",
                        "检查地图生命周期",
                        "区分 ADD/UPDATE/REMOVE/EVICT，核对射线清除和动态对象 TTL。",
                        "缓存淘汰不会被解释为真实空间清除。",
                        2,
                    ),
                ),
                ("分离动态层", "为动态障碍增加 TTL 和多帧确认"),
                0.13,
            ),
        ),
    ),
    "GLOBAL_PLAN_FAILED": Symptom(
        "GLOBAL_PLAN_FAILED",
        "全局规划失败或无法生成可执行路径",
        (
            Hypothesis(
                "H_GOAL_FRAME",
                "目标坐标系或地图版本不一致",
                {"TF_UNAVAILABLE": 0.4, "FRAME_CHANGED": 0.25, "PATH_STALE": 0.15},
                (TF_PROBE,),
                ("将目标绑定到 map/session/version", "执行前重绑定并重新校验"),
                0.17,
            ),
            Hypothesis(
                "H_MAP_DISCONNECTED",
                "占据地图不连通或机器人起点处于障碍",
                {"PLANNING_FAILED": 0.35, "MAP_STALE": 0.25},
                (
                    _probe(
                        "CHECK_COSTMAP",
                        "检查起点、终点和连通性",
                        "导出规划时刻 costmap，检查 footprint、unknown、inflation 和连通域。",
                        "起点终点均位于自由连通域。",
                    ),
                ),
                ("修正 footprint/inflation", "修复地图投影和清除策略"),
                0.16,
            ),
            Hypothesis(
                "H_PLANNER_TIMEOUT",
                "规划超时或计算资源不足",
                {"PLANNING_SLOW": 0.45, "CPU_SATURATION": 0.25, "QUEUE_BACKLOG": 0.2},
                (RESOURCE_PROBE,),
                ("降低地图分辨率或搜索空间", "隔离规划线程和资源预算"),
                0.1,
            ),
        ),
    ),
    "LOCAL_PLANNER_OSCILLATION": Symptom(
        "LOCAL_PLANNER_OSCILLATION",
        "局部轨迹或机器人控制持续振荡",
        (
            Hypothesis(
                "H_LOCALIZATION_JITTER",
                "定位抖动传播到局部地图和控制器",
                {"POSE_JUMP": 0.4, "STATIONARY_DRIFT": 0.35, "LOCALIZATION_UNCERTAIN": 0.2},
                (POSE_FREEZE_PROBE,),
                ("提高位姿健康门控", "控制器消费平滑 odom，隔离全局修正跳变"),
                0.18,
            ),
            Hypothesis(
                "H_PATH_QUALITY",
                "参考路径折返、曲率不连续或路径头位于身后",
                {"PATH_STALE": 0.3, "PLANNING_FAILED": 0.15},
                (PATH_INJECT_PROBE,),
                ("裁剪已走路径头", "对全局路径重采样并施加曲率约束"),
                0.17,
            ),
            Hypothesis(
                "H_CONTROLLER_TUNING",
                "控制器代价项、时域或本体约束不匹配",
                {"TRACKING_ERROR": 0.4, "CONTROLLER_CYCLE_MISS": 0.25},
                (
                    _probe(
                        "CHECK_CONTROLLER_TERMS",
                        "检查控制误差和代价项",
                        "比较横向、航向、速度误差以及各 critic/cost 分量。",
                        "误差收敛且没有两个高权重代价项持续对抗。",
                        2,
                    ),
                ),
                ("单变量调整控制参数", "锁定非全向 vy 并核对速度/加速度限幅"),
                0.15,
            ),
            Hypothesis(
                "H_STALE_CONTROL_INPUT",
                "局部地图或路径数据年龄过大",
                {"HIGH_LATENCY": 0.25, "PATH_STALE": 0.4, "QUEUE_BACKLOG": 0.3},
                (TIME_PROBE,),
                ("消费最新值并丢弃过期输入", "缩短队列并统一 deadline"),
                0.13,
            ),
        ),
    ),
    "CONTROLLER_CRASH": Symptom(
        "CONTROLLER_CRASH",
        "controller_server 或控制进程异常退出",
        (
            Hypothesis(
                "H_CONTROLLER_EXCEPTION",
                "插件异常、非法参数或数值非有限",
                {"CONTROLLER_CRASH": 0.55, "TRACKING_ERROR": 0.1},
                (
                    _probe(
                        "CHECK_BACKTRACE",
                        "采集 core dump 与回溯",
                        "保存退出信号、栈回溯、参数快照和输入消息。",
                        "定位到明确插件、函数和触发输入。",
                    ),
                ),
                ("为输入增加 finite/range 校验", "修复插件异常并加入回归用例"),
                0.2,
            ),
            Hypothesis(
                "H_MEMORY",
                "内存耗尽、越界或持续增长",
                {"MEMORY_PRESSURE": 0.45, "CONTROLLER_CRASH": 0.2},
                (RESOURCE_PROBE,),
                ("使用 ASan/UBSan 复现", "限制轨迹和代价地图历史缓存"),
                0.13,
            ),
            Hypothesis(
                "H_INVALID_TF_PATH",
                "控制周期收到无效 TF、空路径或过期路径",
                {"TF_UNAVAILABLE": 0.25, "PATH_STALE": 0.3, "CONTROLLER_CRASH": 0.2},
                (TF_PROBE, PATH_INJECT_PROBE),
                ("空路径和 TF 失败时进入安全等待", "拒绝过期轨迹"),
                0.12,
            ),
        ),
    ),
    "END_TO_END_LATENCY": Symptom(
        "END_TO_END_LATENCY",
        "传感器到控制输出的数据年龄或延迟异常",
        (
            Hypothesis(
                "H_QUEUE_BACKLOG",
                "消息队列积压，算法计算快但消费旧数据",
                {"QUEUE_BACKLOG": 0.5, "HIGH_LATENCY": 0.25, "PATH_STALE": 0.15},
                (TIME_PROBE,),
                ("使用 latest-value/KeepLast(1)", "监测并限制队列深度"),
                0.2,
            ),
            Hypothesis(
                "H_COMPUTE_SATURATION",
                "CPU/GPU 饱和或线程调度竞争",
                {"CPU_SATURATION": 0.4, "THERMAL_THROTTLE": 0.35, "PLANNING_SLOW": 0.2},
                (RESOURCE_PROBE,),
                ("隔离实时线程", "降低非关键处理频率并设置资源预算"),
                0.16,
            ),
            Hypothesis(
                "H_TRANSPORT",
                "DDS/网络重传、丢包或 QoS 不匹配",
                {"FRAME_DROP": 0.3, "HIGH_LATENCY": 0.3, "SEQUENCE_DISORDER": 0.2},
                (
                    _probe(
                        "CHECK_TRANSPORT",
                        "检查 DDS 与网络",
                        "检查 QoS、丢包、重传、队列和 shared-memory fallback。",
                        "关键流满足 deadline，旧消息不会阻塞最新状态。",
                    ),
                ),
                ("按消息类别拆分 QoS", "大载荷与实时状态分离"),
                0.14,
            ),
        ),
    ),
    "NETWORK_RECOVERY_FAILED": Symptom(
        "NETWORK_RECOVERY_FAILED",
        "网络恢复后地图、任务或状态未恢复一致",
        (
            Hypothesis(
                "H_SESSION_MISMATCH",
                "会话或地图代数不一致",
                {"SEQUENCE_DISORDER": 0.25, "FRAME_CHANGED": 0.2, "DATA_STALE": 0.15},
                (
                    _probe(
                        "CHECK_SESSION",
                        "检查会话和版本握手",
                        "比较 session、context_generation、map_epoch 和 graph_version。",
                        "恢复后先完成版本握手，再恢复数据与技能。",
                    ),
                ),
                ("增加 RESET/ACK/NACK 协议", "拒绝旧会话缓存"),
                0.19,
            ),
            Hypothesis(
                "H_MISSING_DELTA",
                "断网期间增量缺口未补传",
                {"FRAME_DROP": 0.35, "SEQUENCE_DISORDER": 0.3},
                (
                    _probe(
                        "CHECK_VERSION_GAP",
                        "检查增量版本连续性",
                        "核对 base_version、commit_version 和缺口补传记录。",
                        "缺口被重放或触发完整快照恢复。",
                    ),
                ),
                ("实现版本缺口检测", "缺口过大时请求 checkpoint"),
                0.16,
            ),
            Hypothesis(
                "H_STALE_TASK",
                "网络恢复后继续执行过期任务",
                {"PATH_STALE": 0.35, "HIGH_LATENCY": 0.2},
                (TIME_PROBE,),
                ("为任务设置 TTL 和来源快照", "恢复后重新验证 SpatialIntent"),
                0.14,
            ),
        ),
    ),
}


ALIASES = {
    "NAV_OSCILLATION": "LOCAL_PLANNER_OSCILLATION",
    "PLANNER_OSCILLATION": "LOCAL_PLANNER_OSCILLATION",
    "LATENCY": "END_TO_END_LATENCY",
}


def symptom_codes() -> list[str]:
    return sorted(SYMPTOMS)


def resolve_symptom(code: str) -> Symptom:
    normalized = code.strip().upper()
    normalized = ALIASES.get(normalized, normalized)
    if normalized not in SYMPTOMS:
        supported = ", ".join(symptom_codes())
        raise ValueError(f"未知症状 {code!r}；支持: {supported}")
    return SYMPTOMS[normalized]


def diagnose(report: DiagnosticReport, symptom_code: str) -> FaultDiagnosis:
    symptom = resolve_symptom(symptom_code)
    findings = [finding for sensor in report.sensors for finding in sensor.findings]
    by_code = {finding.code: finding for finding in findings}
    ranked: list[RankedHypothesis] = []

    for hypothesis in symptom.hypotheses:
        matched = sorted(set(hypothesis.evidence_weights) & set(by_code))
        unexplained_fraction = 1.0
        for code in matched:
            finding = by_code[code]
            severity_factor = 1.0 + 0.25 * int(finding.severity)
            contribution = min(
                0.75,
                hypothesis.evidence_weights[code] * severity_factor * 0.75,
            )
            unexplained_fraction *= 1.0 - contribution
        evidence_score = 1.0 - unexplained_fraction
        score = hypothesis.prior + (1.0 - hypothesis.prior) * evidence_score
        if matched:
            rationale = "匹配运行证据：" + "、".join(matched)
        else:
            rationale = "当前记录缺少直接证据；按系统先验保留该假设"
        ranked.append(
            RankedHypothesis(
                id=hypothesis.id,
                title=hypothesis.title,
                score=round(score, 3),
                rationale=rationale,
                matched_findings=matched,
                probes=list(hypothesis.probes),
                remediations=list(hypothesis.remediations),
            )
        )

    ranked.sort(
        key=lambda item: (
            -item.score,
            min((probe.cost for probe in item.probes), default=5),
            item.id,
        )
    )
    return FaultDiagnosis(
        symptom=symptom.code,
        summary=symptom.summary,
        source=report.source,
        ranked_hypotheses=ranked,
        observed_findings=sorted(by_code),
    )


def render_mermaid(symptom_code: str) -> str:
    symptom = resolve_symptom(symptom_code)
    lines = ["flowchart LR", f'  S["{symptom.code}<br/>{symptom.summary}"]']
    for index, hypothesis in enumerate(symptom.hypotheses):
        hypothesis_node = f"H{index}"
        lines.append(f'  {hypothesis_node}["{hypothesis.title}"]')
        lines.append(f"  S --> {hypothesis_node}")
        for probe_index, probe in enumerate(hypothesis.probes):
            probe_node = f"P{index}_{probe_index}"
            lines.append(f'  {probe_node}(["{probe.title}"])')
            lines.append(f"  {hypothesis_node} --> {probe_node}")
    return "\n".join(lines) + "\n"


def render_dot(symptom_code: str) -> str:
    symptom = resolve_symptom(symptom_code)
    lines = ["digraph fault_graph {", "  rankdir=LR;"]
    lines.append(f'  symptom [shape=box, label="{symptom.code}\\n{symptom.summary}"];')
    for index, hypothesis in enumerate(symptom.hypotheses):
        hypothesis_node = f"h{index}"
        lines.append(f'  {hypothesis_node} [label="{hypothesis.title}"];')
        lines.append(f"  symptom -> {hypothesis_node};")
        for probe_index, probe in enumerate(hypothesis.probes):
            probe_node = f"p{index}_{probe_index}"
            lines.append(f'  {probe_node} [shape=ellipse, label="{probe.title}"];')
            lines.append(f"  {hypothesis_node} -> {probe_node};")
    lines.append("}")
    return "\n".join(lines) + "\n"
