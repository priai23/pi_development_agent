"""
telemetry.py — Observability, metrics collection, and health reporting.

Tracks runtime statistics, token usage, tool invocation latency, error rates,
and system status across projects and agent runs.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func

import models


@dataclass
class MetricSnapshot:
    active_runs: int = 0
    queued_runs: int = 0
    awaiting_approval: int = 0
    failed_runs_24h: int = 0
    total_runs: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    tool_calls_total: int = 0
    tool_failures_total: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class TelemetryCollector:
    """In-memory and DB-backed metrics collector."""

    def __init__(self):
        self._tool_counts: dict[str, int] = defaultdict(int)
        self._tool_errors: dict[str, int] = defaultdict(int)
        self._tool_latencies: dict[str, list[float]] = defaultdict(list)

    def record_tool_call(self, tool_name: str, duration_sec: float, success: bool = True) -> None:
        self._tool_counts[tool_name] += 1
        if not success:
            self._tool_errors[tool_name] += 1
        latencies = self._tool_latencies[tool_name]
        latencies.append(duration_sec)
        if len(latencies) > 500:
            self._tool_latencies[tool_name] = latencies[-250:]

    def get_tool_metrics(self) -> dict[str, dict[str, Any]]:
        result = {}
        for tool, count in self._tool_counts.items():
            errs = self._tool_errors.get(tool, 0)
            latencies = self._tool_latencies.get(tool, [])
            avg_lat = sum(latencies) / len(latencies) if latencies else 0.0
            result[tool] = {
                "invocations": count,
                "errors": errs,
                "error_rate": round(errs / count, 3) if count else 0.0,
                "avg_latency_sec": round(avg_lat, 3),
            }
        return result

    def get_system_snapshot(self, db) -> dict[str, Any]:
        active_runs = db.query(models.AgentRun).filter(models.AgentRun.status == "running").count()
        queued_runs = db.query(models.AgentRun).filter(models.AgentRun.status == "queued").count()
        awaiting = db.query(models.PendingAction).filter(models.PendingAction.status == "pending_approval").count()
        total_runs = db.query(models.AgentRun).count()

        token_sum = db.query(
            func.sum(models.AgentRun.input_tokens),
            func.sum(models.AgentRun.output_tokens),
            func.sum(models.AgentRun.cost_usd),
        ).first()

        total_in = int(token_sum[0] or 0)
        total_out = int(token_sum[1] or 0)
        total_cost = float(token_sum[2] or 0.0)

        instance_count = db.query(models.Instance).count()
        ready_instances = db.query(models.Instance).filter(models.Instance.status.in_(("ready", "connected"))).count()

        return {
            "status": "healthy",
            "runs": {
                "active": active_runs,
                "queued": queued_runs,
                "awaiting_approval": awaiting,
                "total": total_runs,
            },
            "usage": {
                "input_tokens": total_in,
                "output_tokens": total_out,
                "total_tokens": total_in + total_out,
                "total_cost_usd": round(total_cost, 4),
            },
            "instances": {
                "total": instance_count,
                "ready": ready_instances,
            },
            "tools": self.get_tool_metrics(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# Global telemetry singleton
telemetry = TelemetryCollector()
