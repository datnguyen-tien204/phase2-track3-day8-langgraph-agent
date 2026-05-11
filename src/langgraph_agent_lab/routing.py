"""Routing functions for conditional edges.

Each function receives the current AgentState and returns a node name string.
They are kept pure (no side effects) so they can be unit-tested without a
running graph.
"""

from __future__ import annotations

from .state import AgentState, Route


def route_after_classify(state: AgentState) -> str:
    """Map the classified route to the next graph node.

    Falls back to 'answer' for any unrecognised route value so the graph
    always terminates.
    """
    route = state.get("route", Route.SIMPLE.value)
    mapping: dict[str, str] = {
        Route.SIMPLE.value: "answer",
        Route.TOOL.value: "tool",
        Route.MISSING_INFO.value: "clarify",
        Route.RISKY.value: "risky_action",
        Route.ERROR.value: "retry",
    }
    return mapping.get(route, "answer")


def route_after_retry(state: AgentState) -> str:
    """Decide whether to retry the tool or give up (dead-letter).

    Bounded retry: once attempt >= max_attempts the node routes to dead_letter
    instead of looping back to tool, preventing infinite cycles.
    """
    attempt = int(state.get("attempt", 0))
    max_attempts = int(state.get("max_attempts", 3))
    if attempt >= max_attempts:
        return "dead_letter"
    return "tool"


def route_after_evaluate(state: AgentState) -> str:
    """Decide whether the tool result is acceptable or needs another attempt.

    This is the core 'done?' check that enables LangGraph's retry loop —
    a capability that plain LCEL chains cannot express natively.
    """
    if state.get("evaluation_result") == "needs_retry":
        return "retry"
    return "answer"


def route_after_approval(state: AgentState) -> str:
    """Continue only if the human reviewer approved the proposed action.

    Rejected actions are routed to clarify so the user receives feedback
    rather than a silent failure.
    """
    approval = state.get("approval") or {}
    return "tool" if approval.get("approved") else "clarify"
