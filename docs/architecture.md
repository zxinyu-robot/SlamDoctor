# SlamDoctor Architecture

SlamDoctor combines four structures:

1. **Dependency DAG** — how sensor, localization, mapping, planning and control data propagate.
2. **Contract engine** — whether each interface satisfies time, frame, rate, sequence and quality constraints.
3. **Fault graph** — symptom → hypothesis → probe → remediation.
4. **Evidence report** — the concrete observation, metric, log or replay result supporting each decision.

```text
JSONL / CSV / stdin
        ↓
Observation normalization
        ↓
Bounded streaming windows
        ↓
Contract engine
        ├── time / sequence / frame / frequency
        ├── sensor quality
        ├── SLAM quality
        ├── map / planner / controller
        └── runtime resources
        ↓
DiagnosticReport
        ↓
Executable fault graph
        ↓
Ranked hypotheses + next cheapest probe
```

## Runtime-neutral core

The core intentionally does not import ROS. rosbag2, MCAP, ROS diagnostics, LTTng and vendor SDK adapters should translate their source into the standard observation schema. This makes the same diagnosis reproducible on a robot, an operations laptop and CI.

## Contract classes

- **Validity**: schema, finite values, valid ranges.
- **Freshness**: source-to-receive latency, path/map age and stale sources.
- **Ordering**: monotonic timestamps and sequences.
- **Spatial consistency**: stable frame identity and TF availability.
- **Quality**: inliers, degeneracy, covariance and tracking error.
- **Performance**: planning/controller deadline, queues, CPU, memory and temperature.

## Safety

SlamDoctor is advisory. It ranks hypotheses and proposes probes; it does not modify robot parameters, restart nodes or send control commands. Probes requiring injected Pose, path or command data must run in simulation, offline replay or a controlled test setup.

## Extension points

- Add source adapters without changing the engine.
- Add thresholds in configuration.
- Add symptoms and hypotheses in `fault_graph.py`.
- Add findings in the contract engine.
- Add report renderers independently of diagnosis.
