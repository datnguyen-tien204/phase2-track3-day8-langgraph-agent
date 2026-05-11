"""Checkpointer adapter for the LangGraph lab.

Supports three modes:
  - "none"    : no persistence (useful for quick smoke tests)
  - "memory"  : MemorySaver (in-process, perfect for dev/CI)
  - "sqlite"  : SqliteSaver with WAL mode (survives process restart)
  - "postgres": PostgresSaver (production-grade, requires psycopg2)
"""

from __future__ import annotations

from typing import Any


def build_checkpointer(kind: str = "memory", database_url: str | None = None) -> Any | None:
    """Return a LangGraph checkpointer configured for the requested backend.

    SQLite notes
    ------------
    langgraph-checkpoint-sqlite 2.x / 3.x uses SqliteSaver(conn=...) directly.
    WAL mode is enabled for better concurrent read performance.

    Thread safety: sqlite3 connections must not be shared across threads.
    Pass check_same_thread=False only when the graph is invoked from a single
    thread (the default CLI use-case). For multi-threaded servers use Postgres.
    """
    if kind == "none":
        return None

    if kind == "memory":
        from langgraph.checkpoint.memory import MemorySaver  # type: ignore[import]

        return MemorySaver()

    if kind == "sqlite":
        import sqlite3

        try:
            from langgraph.checkpoint.sqlite import SqliteSaver  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "SQLite checkpointer requires: pip install langgraph-checkpoint-sqlite"
            ) from exc

        db_path = database_url or "checkpoints.db"
        conn = sqlite3.connect(db_path, check_same_thread=False)
        # Enable WAL for better concurrency and crash recovery
        conn.execute("PRAGMA journal_mode=WAL")
        conn.commit()
        return SqliteSaver(conn=conn)

    if kind == "postgres":
        try:
            from langgraph.checkpoint.postgres import PostgresSaver  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "Postgres checkpointer requires: pip install langgraph-checkpoint-postgres"
            ) from exc
        dsn = database_url or ""
        return PostgresSaver.from_conn_string(dsn)

    raise ValueError(f"Unknown checkpointer kind: {kind!r}. Choose from: none, memory, sqlite, postgres")
