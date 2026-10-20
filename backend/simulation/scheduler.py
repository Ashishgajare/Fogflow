import math
from functools import lru_cache
from typing import Iterable

from simulation.models import (
    AllocationCandidate,
    DEFAULT_ADAPTIVE_WEIGHTS,
    Node,
    Task,
)


ALGORITHMS = {
    "round_robin",
    "weighted_round_robin",
    "least_loaded",
    "adaptive_fog_cloud",
}
WEIGHT_NAMES = {"response_time", "utilization", "completion_time"}
WEIGHTED_ROUND_ROBIN_SCALE = 10_000


@lru_cache(maxsize=32)
def _weighted_schedule(weights: tuple[tuple[str, int], ...]) -> tuple[str, ...]:
    total = sum(weight for _, weight in weights)
    current = {node_id: 0 for node_id, _ in weights}
    divisor = total
    for _, weight in weights:
        divisor = math.gcd(divisor, weight)
    period = total // max(divisor, 1)
    sequence: list[str] = []
    for _ in range(period):
        for node_id, weight in weights:
            current[node_id] += weight
        selected = max(
            (node_id for node_id, _ in weights),
            key=lambda node_id: (current[node_id], node_id),
        )
        current[selected] -= total
        sequence.append(selected)
    return tuple(sequence)


class SchedulingError(ValueError):
    pass


def validate_weights(weights: dict[str, float] | None) -> dict[str, float]:
    configured = dict(DEFAULT_ADAPTIVE_WEIGHTS if weights is None else weights)
    if set(configured) != WEIGHT_NAMES:
        raise SchedulingError(
            "Adaptive weights must define response_time, utilization, and completion_time."
        )
    for name, weight in configured.items():
        if (
            isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or not math.isfinite(weight)
            or weight < 0
        ):
            raise SchedulingError(f"Adaptive weight '{name}' must be a finite non-negative number.")
        configured[name] = float(weight)
    if not math.isclose(sum(configured.values()), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise SchedulingError("Adaptive weights must sum to 1.")
    return configured


def validate_algorithm(algorithm: str) -> str:
    if algorithm not in ALGORITHMS:
        raise SchedulingError(
            "algorithm must be round_robin, weighted_round_robin, least_loaded, "
            "or adaptive_fog_cloud."
        )
    return algorithm


def eligible_candidates(
    nodes: Iterable[Node],
    tasks: dict[str, Task],
    task: Task,
    now: float,
) -> tuple[list[AllocationCandidate], dict[str, str]]:
    candidates: list[AllocationCandidate] = []
    excluded: dict[str, str] = {}

    for node in nodes:
        if not node.online:
            excluded[node.node_id] = "node is offline"
            continue
        outstanding = node.workload(tasks)
        projected_workload = outstanding + task.cpu_demand
        if projected_workload > node.processing_capacity:
            excluded[node.node_id] = (
                f"capacity exceeded ({projected_workload:g} > "
                f"{node.processing_capacity:g} CPU units)"
            )
            continue

        processing_time_ms = task.cpu_demand / node.processing_rate * 1000.0
        busy_seconds = 0.0
        if node.active_task_id is not None and node.active_task_finishes_at is not None:
            busy_seconds = max(0.0, node.active_task_finishes_at - now)
        busy_seconds += sum(
            node.network_latency_ms / 1000.0
            + tasks[queued_id].cpu_demand / node.processing_rate
            for queued_id in node.queue
        )
        completion_time_seconds = (
            busy_seconds
            + node.network_latency_ms / 1000.0
            + processing_time_ms / 1000.0
        )
        if (
            task.deadline_seconds is not None
            and completion_time_seconds > task.deadline_seconds
        ):
            excluded[node.node_id] = (
                f"deadline missed (estimated {completion_time_seconds:.3f}s > "
                f"{task.deadline_seconds:.3f}s)"
            )
            continue

        candidates.append(
            AllocationCandidate(
                node=node,
                projected_workload=projected_workload,
                projected_utilization=(
                    projected_workload / node.processing_capacity * 100.0
                ),
                processing_time_ms=processing_time_ms,
                response_time_ms=(
                    node.network_latency_ms + processing_time_ms
                ),
                completion_time_seconds=completion_time_seconds,
            )
        )
    return candidates, excluded


def _adaptive_score(
    candidate: AllocationCandidate,
    candidates: list[AllocationCandidate],
    weights: dict[str, float],
) -> float:
    maxima = {
        "response_time": max(item.response_time_ms for item in candidates),
        "utilization": max(item.projected_utilization for item in candidates),
        "completion_time": max(item.completion_time_seconds for item in candidates),
    }
    values = {
        "response_time": candidate.response_time_ms,
        "utilization": candidate.projected_utilization,
        "completion_time": candidate.completion_time_seconds,
    }
    return sum(
        weights[name] * (values[name] / maxima[name] if maxima[name] > 0 else 0.0)
        for name in WEIGHT_NAMES
    )


def choose_candidate(
    algorithm: str,
    candidates: list[AllocationCandidate],
    round_robin_offset: int,
    weights: dict[str, float],
) -> tuple[AllocationCandidate, str]:
    validate_algorithm(algorithm)
    if not candidates:
        raise SchedulingError("No eligible node satisfies availability, capacity, and deadline constraints.")

    if algorithm == "round_robin":
        candidate = candidates[round_robin_offset % len(candidates)]
        reason = (
            f"Round Robin selected the next eligible node, {candidate.node.name}; "
            f"projected utilization is {candidate.projected_utilization:.1f}%."
        )
        return candidate, reason

    if algorithm == "weighted_round_robin":
        total_capacity = sum(item.node.processing_capacity for item in candidates)
        normalized_weights = tuple(
            sorted(
                (
                    item.node.node_id,
                    max(
                        1,
                        round(
                            item.node.processing_capacity
                            / total_capacity
                            * WEIGHTED_ROUND_ROBIN_SCALE
                        ),
                    ),
                )
                for item in candidates
            )
        )
        sequence = _weighted_schedule(normalized_weights)
        selected_node_id = sequence[round_robin_offset % len(sequence)]
        candidate = next(
            item for item in candidates if item.node.node_id == selected_node_id
        )
        reason = (
            f"Weighted Round Robin selected {candidate.node.name} using processing "
            f"capacity weights; projected utilization is "
            f"{candidate.projected_utilization:.1f}%."
        )
        return candidate, reason

    if algorithm == "least_loaded":
        candidate = min(
            candidates,
            key=lambda item: (
                item.projected_utilization,
                item.node.network_latency_ms,
                item.node.node_id,
            ),
        )
        reason = (
            f"Least Loaded selected {candidate.node.name} with the lowest projected "
            f"utilization ({candidate.projected_utilization:.1f}%)."
        )
        return candidate, reason

    candidate = min(
        candidates,
        key=lambda item: (
            _adaptive_score(item, candidates, weights),
            item.response_time_ms,
            item.node.node_id,
        ),
    )
    score = _adaptive_score(candidate, candidates, weights)
    reason = (
        f"Adaptive Fog–Cloud selected {candidate.node.name} with normalized score "
        f"{score:.3f}; weights are response={weights['response_time']:.2f}, "
        f"utilization={weights['utilization']:.2f}, "
        f"completion={weights['completion_time']:.2f}."
    )
    return candidate, reason
