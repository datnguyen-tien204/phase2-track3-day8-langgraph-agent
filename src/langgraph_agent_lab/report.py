"""Report generation for the Day-08 lab.

Reads MetricsReport and renders a filled-in Markdown lab report.
"""

from __future__ import annotations

from pathlib import Path

from .metrics import MetricsReport, ScenarioMetric


def _route_emoji(success: bool) -> str:
    return "✅" if success else "❌"


def _scenario_table(metrics: list[ScenarioMetric]) -> str:
    header = (
        "| Scenario | Expected | Actual | OK | Retries | Interrupts | Approval |\n"
        "|---|---|---|:---:|:---:|:---:|:---:|\n"
    )
    rows = []
    for m in metrics:
        ok = _route_emoji(m.success)
        appr = "✅" if m.approval_observed else ("⚠️" if m.approval_required else "—")
        rows.append(
            f"| {m.scenario_id} | {m.expected_route} | {m.actual_route or '?'} "
            f"| {ok} | {m.retry_count} | {m.interrupt_count} | {appr} |"
        )
    return header + "\n".join(rows)


def render_report(metrics: MetricsReport) -> str:  # noqa: ANN001
    """Render a comprehensive lab report as a Markdown string."""
    scenario_table = _scenario_table(metrics.scenario_metrics)
    failed = [m for m in metrics.scenario_metrics if not m.success]
    failed_section = ""
    if failed:
        lines = [
            f"- **{m.scenario_id}**: expected `{m.expected_route}`, got `{m.actual_route}`"
            for m in failed
        ]
        failed_section = "\n".join(lines)
    else:
        failed_section = "_All scenarios passed._"

    return f"""# Day 08 Lab Report — LangGraph Agentic Orchestration

## 1. Team / Student

- **Name:** (fill in your name)
- **Repo / commit:** (fill in your repo URL and commit hash)
- **Date:** 2026-05-11

---

## 2. Architecture

The agent is a stateful LangGraph `StateGraph` with **11 nodes** and 6 conditional
routing functions. Every query passes through `intake → classify` before being
dispatched to one of five specialised paths.

```
START → intake → classify ─┬─→ answer       → finalize → END  (simple)
                            ├─→ tool → evaluate ─┬─→ answer → finalize → END  (tool)
                            │               └─→ retry ─┬─→ tool   (retry loop)
                            │                          └─→ dead_letter → finalize → END
                            ├─→ clarify      → finalize → END  (missing_info)
                            ├─→ risky_action → approval → tool → evaluate
                            │                                  → answer → finalize → END  (risky)
                            └─→ retry → …                                             (error)
```

### Node responsibilities

| Node | Responsibility |
|---|---|
| `intake` | Strip/normalize raw query; emit audit event |
| `classify` | Keyword-based route decision (risky > tool > missing_info > error > simple) |
| `answer` | Compose final response grounded in tool results / approval |
| `tool` | Execute mock tool; simulate transient errors for retry-loop testing |
| `evaluate` | Gate: check latest tool result for ERROR → `needs_retry` or `success` |
| `clarify` | Ask targeted clarification question for vague queries |
| `risky_action` | Prepare HITL payload with proposed action and risk justification |
| `approval` | Mock (or real interrupt) human approval; default auto-approves in CI |
| `retry` | Increment attempt counter; record exponential-backoff metadata |
| `dead_letter` | Log exhausted-retry scenarios for manual follow-up |
| `finalize` | Close run; emit summary audit event |

---

## 3. State Schema

Fields are serializable Python primitives to ensure checkpoint compatibility.

| Field | Reducer | Why |
|---|---|---|
| `thread_id` | overwrite | one ID per run for checkpointer keying |
| `scenario_id` | overwrite | grading identifier |
| `query` | overwrite | normalized once by intake |
| `route` | overwrite | current routing decision |
| `risk_level` | overwrite | low / medium / high |
| `attempt` | overwrite | monotonically increasing retry counter |
| `max_attempts` | overwrite | per-scenario ceiling |
| `final_answer` | overwrite | last produced answer |
| `pending_question` | overwrite | clarification question (missing_info path) |
| `proposed_action` | overwrite | HITL payload description |
| `approval` | overwrite | approval decision dict |
| `evaluation_result` | overwrite | `"success"` or `"needs_retry"` |
| `messages` | **append** | conversation log — never lost |
| `tool_results` | **append** | all raw tool responses — audit trail |
| `errors` | **append** | all transient errors — never overwritten |
| `events` | **append** | full ordered audit log of node visits |

---

## 4. Scenario Results

{scenario_table}

### Summary

| Metric | Value |
|---|---|
| Total scenarios | {metrics.total_scenarios} |
| **Success rate** | **{metrics.success_rate:.0%}** |
| Avg nodes visited | {metrics.avg_nodes_visited:.1f} |
| Total retries | {metrics.total_retries} |
| Total HITL interrupts | {metrics.total_interrupts} |
| Crash-resume demonstrated | {"Yes" if metrics.resume_success else "Not in this run"} |

---

## 5. Failure Analysis

### 5.1 Transient tool failure (S05 — error path)

**Scenario:** "Timeout failure while processing request"

The `classify_node` detects the keyword `timeout` and routes to `error →
retry`. The `retry_or_fallback_node` increments the attempt counter. The
`tool_node` simulates transient failures for `attempt < 2`:

- Attempt 1 → `ERROR` result → `evaluate` → `needs_retry` → retry
- Attempt 2 → `ERROR` result → `evaluate` → `needs_retry` → retry
- Attempt 3 → success result → `evaluate` → `success` → answer

**Mitigation:** The loop is bounded by `max_attempts` (default 3). If all
attempts fail, `route_after_retry` sends the request to `dead_letter`.

### 5.2 Max-retry exhaustion (S07 — dead letter)

**Scenario:** `max_attempts=1`. After the first retry, `attempt (1) >=
max_attempts (1)`, so `route_after_retry` returns `dead_letter`. The user
receives a message that manual review has been triggered.

### 5.3 Risky action without approval

If `approval_node` returns `approved=False` (e.g., a human reviewer rejects
the action), `route_after_approval` routes to `clarify`. The user gets a
clarification message rather than a silent failure or an accidental execution.

---

## 6. Persistence / Recovery Evidence

The `build_checkpointer("memory")` wires a `MemorySaver` into the compiled
graph. Every `graph.invoke()` call is tagged with a unique `thread_id`
(`thread-{{scenario_id}}`), which the checkpointer uses as the primary key.

**State history:** Call `graph.get_state_history(config)` to list all
intermediate snapshots for a run. This enables time-travel debugging — you
can replay from any prior checkpoint.

**SQLite extension (optional):** Set `checkpointer: sqlite` in `configs/lab.yaml`.
The `build_checkpointer("sqlite")` function opens a WAL-mode connection with
`sqlite3.connect(db_path, check_same_thread=False)` and returns
`SqliteSaver(conn=conn)`. This persists checkpoints across process restarts;
kill the process mid-run and re-invoke with the same `thread_id` to resume.

---

## 7. Extension Work

### Graph diagram (bonus)

Call `export_mermaid("outputs/graph.md")` from `langgraph_agent_lab.graph` to
generate a Mermaid diagram of the compiled graph automatically using
`graph.get_graph().draw_mermaid()`. The diagram is written to
`outputs/graph.md` and can be previewed in any Markdown renderer.

### SQLite crash-resume (bonus)

Switch `configs/lab.yaml` to `checkpointer: sqlite`, run scenarios, kill the
process with `Ctrl-C` during execution, then re-run. LangGraph resumes
interrupted threads from the last saved checkpoint because the `thread_id` key
remains in `checkpoints.db`.

---

## 8. Improvement Plan

If given another day:

1. **LLM-as-judge in `evaluate_node`**: Replace the `starts-with("ERROR")`
   heuristic with a structured validation call (e.g., assert JSON schema fields
   are present, confidence > threshold).

2. **Real HITL with Streamlit**: Set `LANGGRAPH_INTERRUPT=true` and build a
   minimal Streamlit page that displays the proposed action and lets a reviewer
   click Approve / Reject. Resume the thread via `graph.invoke(None, config)`.

3. **Parallel fan-out**: Use `Send()` to dispatch two mock tools concurrently
   (e.g., order lookup + fraud check), merge via the `add` reducer, then
   pass combined evidence to `evaluate_node`.

4. **Postgres in production**: Replace SQLite with PostgresSaver backed by a
   managed RDS instance for multi-worker horizontal scaling.

5. **Observability**: Wire LangSmith tracing (`LANGCHAIN_TRACING_V2=true`)
   to capture token counts, latency, and node-level spans in a production
   dashboard.

---

## 9. Failed Scenarios

{failed_section}
"""


def write_report(metrics: MetricsReport, output_path: str | Path) -> None:
    """Write the rendered report to disk."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(metrics), encoding="utf-8")
