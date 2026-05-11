"""Graph construction for the Day-08 LangGraph lab.

This module is intentionally import-safe: LangGraph is only imported inside
build_graph() so that unit tests covering schema/metrics can run even when
students are still debugging graph wiring.
"""

from __future__ import annotations

from .nodes import (
    answer_node,
    approval_node,
    ask_clarification_node,
    classify_node,
    dead_letter_node,
    evaluate_node,
    finalize_node,
    intake_node,
    retry_or_fallback_node,
    risky_action_node,
    tool_node,
)
from .routing import (
    route_after_approval,
    route_after_classify,
    route_after_evaluate,
    route_after_retry,
)
from .state import AgentState


def build_graph(checkpointer: object | None = None) -> object:
    """Build and compile the LangGraph workflow.

    Architecture overview
    ---------------------
    START → intake → classify → [conditional]
      simple       → answer       → finalize → END
      tool         → tool → evaluate → answer     → finalize → END
      missing_info → clarify      → finalize → END
      risky        → risky_action → approval → tool → evaluate → answer → finalize → END
      error        → retry → tool → evaluate → retry → … (bounded by max_attempts)
      max retry    → dead_letter  → finalize → END

    The retry loop (retry → tool → evaluate → retry) is bounded by the
    route_after_retry function which routes to dead_letter once
    state['attempt'] >= state['max_attempts'].
    """
    try:
        from langgraph.graph import END, START, StateGraph
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "LangGraph is required. Run: pip install -e '.[dev]' or pip install langgraph"
        ) from exc

    graph = StateGraph(AgentState)

    # ── Register nodes ──────────────────────────────────────────────────────
    graph.add_node("intake", intake_node)
    graph.add_node("classify", classify_node)
    graph.add_node("answer", answer_node)
    graph.add_node("tool", tool_node)
    graph.add_node("evaluate", evaluate_node)
    graph.add_node("clarify", ask_clarification_node)
    graph.add_node("risky_action", risky_action_node)
    graph.add_node("approval", approval_node)
    graph.add_node("retry", retry_or_fallback_node)
    graph.add_node("dead_letter", dead_letter_node)
    graph.add_node("finalize", finalize_node)

    # ── Wire edges ──────────────────────────────────────────────────────────
    graph.add_edge(START, "intake")
    graph.add_edge("intake", "classify")

    # Classify fans out to five possible paths
    graph.add_conditional_edges("classify", route_after_classify)

    # Tool always flows through the evaluate gate
    graph.add_edge("tool", "evaluate")
    graph.add_conditional_edges("evaluate", route_after_evaluate)

    # Missing-info path
    graph.add_edge("clarify", "finalize")

    # Risky path requires HITL approval before tool execution
    graph.add_edge("risky_action", "approval")
    graph.add_conditional_edges("approval", route_after_approval)

    # Retry loop (bounded via route_after_retry)
    graph.add_conditional_edges("retry", route_after_retry)

    # All successful paths converge at finalize → END
    graph.add_edge("answer", "finalize")
    graph.add_edge("dead_letter", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=checkpointer)


def export_mermaid(output_path: str = "outputs/graph.md") -> str:
    """Export the compiled graph as a Mermaid diagram.

    Writes a Markdown file containing a mermaid code block.
    Returns the diagram string.

    Usage:
        from langgraph_agent_lab.graph import export_mermaid
        diagram = export_mermaid("outputs/graph.md")
        print(diagram)
    """
    from pathlib import Path

    compiled = build_graph()
    diagram: str = compiled.get_graph().draw_mermaid()

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"```mermaid\n{diagram}\n```\n", encoding="utf-8")
    return diagram
