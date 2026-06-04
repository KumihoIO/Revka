# SOP Observability & Audit

This page covers where SOP execution evidence is stored and how to inspect it.

## 1. Audit Persistence

SOP audit entries are persisted via `SopAuditLogger` into the configured Memory backend, category `sop`.

Common key patterns:

- `sop_run_{run_id}`: run snapshot (start + completion updates)
- `sop_step_{run_id}_{step_number}`: per-step result
- `sop_approval_{run_id}_{step_number}`: operator approval record
- `sop_timeout_approve_{run_id}_{step_number}`: timeout auto-approval record

## 2. Inspection Paths

<!-- TODO screenshot: terminal showing `revka sop list` output -->
![Terminal showing revka sop list output](../../assets/reference/observability-01-sop-list.png)

### 2.1 Definition-level CLI

```bash
revka sop list
revka sop validate [name]
revka sop show <name>
```

<!-- TODO screenshot: dashboard showing in-flight SOP runs and per-step gate status -->
![Dashboard showing in-flight SOP runs and per-step gate status](../../assets/reference/observability-02-dashboard-sop-runs.png)

### 2.2 Runtime run-state tools

SOP run state is queried from in-agent tools:

- `sop_status` — active/finished runs and optional metrics
- `sop_status` with `include_gate_status: true` — trust phase and gate evaluator state (when available)
- `sop_approve` — approve waiting run step
- `sop_advance` — submit step result and move run forward

<!-- TODO screenshot: Prometheus metrics endpoint response for Revka SOP execution counters -->
![Prometheus metrics endpoint response for Revka SOP execution counters](../../assets/reference/observability-03-prometheus-metrics.png)

## 3. Metrics

- `/metrics` exposes observer metrics when `[observability] backend = "prometheus"`.
- Current exported names are `revka_*` families (general runtime metrics).
- SOP-specific aggregates are available through `sop_status` with `include_metrics: true`.
