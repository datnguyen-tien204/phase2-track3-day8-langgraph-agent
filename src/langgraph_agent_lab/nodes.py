"""Node implementations for the LangGraph workflow.

Each function is small, testable, and returns a partial state update.
Input state is never mutated in-place.
"""

from __future__ import annotations

import re
import time
from typing import Any

from .state import AgentState, ApprovalDecision, Route, make_event

# ---------------------------------------------------------------------------
# Keyword sets — priority: RISKY > TOOL > MISSING_INFO > ERROR > SIMPLE
# ---------------------------------------------------------------------------

_RISKY_KEYWORDS: frozenset[str] = frozenset(
    {"refund", "delete", "send", "cancel", "remove", "revoke", "terminate", "wipe", "purge", "close"}
)
_TOOL_KEYWORDS: frozenset[str] = frozenset(
    {"status", "order", "lookup", "track", "find", "search", "retrieve", "fetch", "check"}
)
_ERROR_KEYWORDS: frozenset[str] = frozenset(
    {"timeout", "fail", "failure", "error", "crash", "unavailable", "exception", "cannot"}
)
_VAGUE_PRONOUNS: frozenset[str] = frozenset({"it", "this", "that", "they", "them"})

_VAGUE_WORD_LIMIT = 5


def _tokenize(text: str) -> frozenset[str]:
    """Split into lower-case word tokens (strips punctuation)."""
    return frozenset(re.split(r"[^a-z0-9]+", text.lower())) - {""}


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def intake_node(state: AgentState) -> dict[str, Any]:
    """Normalize the raw query: strip whitespace, collapse multiple spaces."""
    raw = state.get("query", "")
    normalized = " ".join(raw.strip().split())
    return {
        "query": normalized,
        "messages": [f"intake:{normalized[:60]}"],
        "events": [
            make_event(
                "intake",
                "completed",
                "query normalized",
                raw_length=len(raw),
                normalized_length=len(normalized),
            )
        ],
    }


def classify_node(state: AgentState) -> dict[str, Any]:
    """Classify the query into a route using keyword-based heuristics.

    Priority (highest to lowest):
        risky > tool > missing_info > error > simple

    Word-boundary tokenization prevents substring false-positives
    (e.g. "iteration" does not match the pronoun "it").
    """
    query = state.get("query", "")
    tokens = _tokenize(query)

    # 1. Risky — destructive or irreversible actions (highest priority)
    if tokens & _RISKY_KEYWORDS:
        matched = sorted(tokens & _RISKY_KEYWORDS)
        return {
            "route": Route.RISKY.value,
            "risk_level": "high",
            "events": [
                make_event("classify", "completed", "route=risky", matched_keywords=matched)
            ],
        }

    # 2. Tool — requires external data lookup
    if tokens & _TOOL_KEYWORDS:
        matched = sorted(tokens & _TOOL_KEYWORDS)
        return {
            "route": Route.TOOL.value,
            "risk_level": "low",
            "events": [
                make_event("classify", "completed", "route=tool", matched_keywords=matched)
            ],
        }

    # 3. Missing info — short query containing a vague pronoun
    word_list = [t for t in re.split(r"[^a-z0-9]+", query.lower()) if t]
    if len(word_list) < _VAGUE_WORD_LIMIT and bool(tokens & _VAGUE_PRONOUNS):
        matched = sorted(tokens & _VAGUE_PRONOUNS)
        return {
            "route": Route.MISSING_INFO.value,
            "risk_level": "low",
            "events": [
                make_event(
                    "classify",
                    "completed",
                    "route=missing_info",
                    word_count=len(word_list),
                    vague_pronouns=matched,
                )
            ],
        }

    # 4. Error — transient failures or system issues
    if tokens & _ERROR_KEYWORDS:
        matched = sorted(tokens & _ERROR_KEYWORDS)
        return {
            "route": Route.ERROR.value,
            "risk_level": "medium",
            "events": [
                make_event("classify", "completed", "route=error", matched_keywords=matched)
            ],
        }

    # 5. Simple — safe informational query (default)
    return {
        "route": Route.SIMPLE.value,
        "risk_level": "low",
        "events": [make_event("classify", "completed", "route=simple")],
    }


def ask_clarification_node(state: AgentState) -> dict[str, Any]:
    """Ask a targeted clarification question when the query lacks context."""
    query = state.get("query", "")
    if re.search(r"\bit\b", query, re.IGNORECASE):
        question = (
            "I'd be happy to help, but I'm not sure what 'it' refers to. "
            "Could you describe the issue in more detail — for example the order number, "
            "product name, or the specific action you need?"
        )
    else:
        question = (
            "Could you provide more context? For example: an order or account ID, "
            "the product involved, or a clearer description of what you need."
        )
    return {
        "pending_question": question,
        "final_answer": question,
        "events": [make_event("clarify", "completed", "missing information requested", question=question)],
    }


def tool_node(state: AgentState) -> dict[str, Any]:
    """Execute a mock tool call with idempotent retry semantics.

    Simulates transient failures for error-route scenarios on the first two
    attempts (attempt < 2) to exercise the retry loop. On attempt >= 2 it
    returns a successful result, demonstrating eventual recovery.
    """
    attempt = int(state.get("attempt", 0))
    scenario_id = state.get("scenario_id", "unknown")
    route = state.get("route", "")
    t0 = time.time()

    if route == Route.ERROR.value and attempt < 2:
        result = f"ERROR: transient failure attempt={attempt} scenario={scenario_id}"
        status = "transient_error"
    else:
        result = (
            f"mock-tool-result for scenario={scenario_id} "
            f"order_status=SHIPPED estimated_delivery=2026-05-15"
        )
        status = "success"

    latency = int((time.time() - t0) * 1000)
    return {
        "tool_results": [result],
        "events": [
            make_event(
                "tool",
                status,
                f"tool executed attempt={attempt}",
                attempt=attempt,
                latency_ms=latency,
                route=route,
            )
        ],
    }


def risky_action_node(state: AgentState) -> dict[str, Any]:
    """Prepare a risky action payload for human review before execution."""
    query = state.get("query", "")
    scenario_id = state.get("scenario_id", "unknown")
    proposed = (
        f"PROPOSED ACTION for scenario={scenario_id}: '{query}'. "
        "This operation is irreversible and requires explicit approval. "
        "Risk level: HIGH."
    )
    return {
        "proposed_action": proposed,
        "events": [
            make_event(
                "risky_action",
                "pending_approval",
                "risky action prepared, awaiting human approval",
                scenario_id=scenario_id,
                risk_level="high",
            )
        ],
    }


def approval_node(state: AgentState) -> dict[str, Any]:
    """Human-in-the-loop approval step.

    Set LANGGRAPH_INTERRUPT=true to use real interrupt() for HITL demos.
    Default uses mock approval so tests and CI run offline.
    """
    import os

    if os.getenv("LANGGRAPH_INTERRUPT", "").lower() == "true":
        from langgraph.types import interrupt  # type: ignore[import]

        value = interrupt(
            {
                "proposed_action": state.get("proposed_action"),
                "risk_level": state.get("risk_level"),
                "scenario_id": state.get("scenario_id"),
            }
        )
        if isinstance(value, dict):
            decision = ApprovalDecision(**value)
        else:
            decision = ApprovalDecision(approved=bool(value))
    else:
        decision = ApprovalDecision(
            approved=True,
            reviewer="mock-reviewer",
            comment="Automatically approved in offline/CI mode.",
        )

    return {
        "approval": decision.model_dump(),
        "events": [
            make_event(
                "approval",
                "completed",
                f"approval decision recorded: approved={decision.approved}",
                approved=decision.approved,
                reviewer=decision.reviewer,
                comment=decision.comment,
            )
        ],
    }


def retry_or_fallback_node(state: AgentState) -> dict[str, Any]:
    """Increment the attempt counter and record the retry event.

    Includes exponential backoff metadata (base 200 ms, cap 5 s)
    for downstream monitoring.
    """
    attempt = int(state.get("attempt", 0)) + 1
    backoff_ms = min(200 * (2 ** (attempt - 1)), 5000)
    return {
        "attempt": attempt,
        "errors": [f"transient failure attempt={attempt}"],
        "events": [
            make_event(
                "retry",
                "retry_recorded",
                f"retry attempt {attempt} recorded",
                attempt=attempt,
                backoff_ms=backoff_ms,
            )
        ],
    }


def answer_node(state: AgentState) -> dict[str, Any]:
    """Compose the final answer, grounding it in tool results and approval."""
    tool_results = state.get("tool_results") or []
    approval = state.get("approval")
    route = state.get("route", "")

    if tool_results:
        latest = tool_results[-1]
        if route == Route.RISKY.value and approval:
            approved_by = approval.get("reviewer", "reviewer")
            answer = (
                f"Your request has been approved by {approved_by} and executed. "
                f"Result: {latest}"
            )
        else:
            answer = f"Here is the information you requested: {latest}"
    elif approval:
        answer = (
            f"Your request was reviewed and approved. "
            f"Comment: {approval.get('comment', 'No comment provided.')}"
        )
    else:
        answer = "Your request has been processed successfully."

    return {
        "final_answer": answer,
        "events": [make_event("answer", "completed", "final answer generated", route=route)],
    }


def evaluate_node(state: AgentState) -> dict[str, Any]:
    """Gate node for the retry loop.

    Checks the latest tool result for error markers. In production this
    would call an LLM-as-judge or run structured schema validation.
    """
    tool_results = state.get("tool_results") or []
    latest = tool_results[-1] if tool_results else ""

    if latest.startswith("ERROR"):
        return {
            "evaluation_result": "needs_retry",
            "events": [
                make_event(
                    "evaluate",
                    "needs_retry",
                    "tool result indicates failure, scheduling retry",
                    latest_result_prefix=latest[:80],
                )
            ],
        }
    return {
        "evaluation_result": "success",
        "events": [
            make_event(
                "evaluate",
                "success",
                "tool result satisfactory, proceeding to answer",
                latest_result_prefix=latest[:80],
            )
        ],
    }


def dead_letter_node(state: AgentState) -> dict[str, Any]:
    """Log unresolvable failures for manual review (dead-letter queue).

    In production: persist to dead-letter table, alert on-call, create ticket.
    """
    attempt = int(state.get("attempt", 0))
    scenario_id = state.get("scenario_id", "unknown")
    errors = state.get("errors") or []
    summary = (
        f"DEAD LETTER — scenario={scenario_id} exhausted {attempt} attempt(s). "
        f"Errors recorded: {errors}"
    )
    return {
        "final_answer": (
            "Your request could not be completed after the maximum number of retry attempts. "
            "Our support team has been notified and will follow up within 24 hours."
        ),
        "events": [
            make_event(
                "dead_letter",
                "max_retries_exceeded",
                summary,
                attempt=attempt,
                scenario_id=scenario_id,
                error_count=len(errors),
            )
        ],
    }


def finalize_node(state: AgentState) -> dict[str, Any]:
    """Close the run and emit a final audit event with summary statistics."""
    events = state.get("events") or []
    return {
        "events": [
            make_event(
                "finalize",
                "completed",
                "workflow finished",
                nodes_visited=len(events),
                route=state.get("route"),
                has_answer=bool(state.get("final_answer") or state.get("pending_question")),
            )
        ]
    }
