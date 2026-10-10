# Fault Graph Guide

## Built-in symptoms

- `LOCALIZATION_DRIFT`
- `POSE_JUMP`
- `MAP_GHOSTING`
- `GLOBAL_PLAN_FAILED`
- `LOCAL_PLANNER_OSCILLATION`
- `CONTROLLER_CRASH`
- `END_TO_END_LATENCY`
- `NETWORK_RECOVERY_FAILED`

`NAV_OSCILLATION` is accepted as an alias of `LOCAL_PLANNER_OSCILLATION`.

## Graph semantics

```text
Symptom
  → Hypothesis
      → Probe
          → expected observation
      → candidate remediation
```

A hypothesis score combines a small domain prior with matched contract findings and their severity. The score is a triage ranking, not a calibrated causal probability.

Hypotheses with no direct evidence remain visible at a low score. This is important because an input record may simply omit the signal required to evaluate them.

## Probe design

A good probe:

- tests one boundary or hypothesis;
- is read-only or runs in offline/simulated conditions;
- has a clear expected result;
- records enough evidence to update the graph;
- costs less than changing multiple parameters and rerunning the robot.

Examples:

- freeze Pose input to isolate localization from navigation;
- inject a known smooth path to isolate global planning;
- replay one bag offline to separate algorithm correctness from runtime load;
- compare source/publish/receive/consume timestamps to identify queue age;
- inspect TF ownership and transform availability at the failing timestamp.

## Adding a symptom

Add a `Symptom` definition with:

- a stable uppercase code;
- a human-readable summary;
- hypotheses with evidence-code weights;
- at least one probe per hypothesis;
- candidate remediations.

Then add a unit test that verifies graph export and ranking from representative findings.
