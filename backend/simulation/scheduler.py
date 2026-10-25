import math
import random
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
    "fuzzy_logic",
    "topsis",
    "ant_colony",
    "particle_swarm",
}
GROUP_1_ALGORITHMS = (
    "round_robin",
    "weighted_round_robin",
    "least_loaded",
    "adaptive_fog_cloud",
)
GROUP_2_ALGORITHMS = (
    "fuzzy_logic",
    "topsis",
    "ant_colony",
    "particle_swarm",
)
ALGORITHM_LABELS = {
    "round_robin": "Round Robin",
    "weighted_round_robin": "Weighted Round Robin",
    "least_loaded": "Least Loaded",
    "adaptive_fog_cloud": "Weighted Sum Model (WSM)",
    "fuzzy_logic": "Fuzzy Logic",
    "topsis": "TOPSIS",
    "ant_colony": "Ant Colony Optimization (ACO)",
    "particle_swarm": "Particle Swarm Optimization (PSO)",
}
WEIGHT_NAMES = {"response_time", "utilization", "completion_time"}
WEIGHTED_ROUND_ROBIN_SCALE = 10_000
ACO_ANTS_PER_TASK = 12
ACO_EVAPORATION_RATE = 0.15
PSO_PARTICLE_COUNT = 12
PSO_ITERATIONS = 10


class SchedulerState:
    def __init__(self, seed: int):
        self.random = random.Random(seed)
        self.pheromone: dict[str, float] = {}


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
            "Weighted Sum Model weights must define response_time, utilization, "
            "and completion_time."
        )
    for name, weight in configured.items():
        if (
            isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or not math.isfinite(weight)
            or weight < 0
        ):
            raise SchedulingError(f"Weighted Sum Model weight '{name}' must be a finite non-negative number.")
        configured[name] = float(weight)
    if not math.isclose(sum(configured.values()), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise SchedulingError("Weighted Sum Model weights must sum to 1.")
    return configured


def validate_algorithm(algorithm: str) -> str:
    if algorithm not in ALGORITHMS:
        raise SchedulingError(
            "algorithm must be one of the supported Group 1 or Group 2 scheduler IDs."
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
                deadline_urgency=(
                    min(completion_time_seconds / task.deadline_seconds, 1.0)
                    if task.deadline_seconds is not None
                    and task.deadline_seconds > 0
                    else 0.0
                ),
            )
        )
    return candidates, excluded


def _weighted_sum_cost(
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


def _candidate_cost(
    candidate: AllocationCandidate,
    candidates: list[AllocationCandidate],
    weights: dict[str, float],
) -> float:
    return _weighted_sum_cost(candidate, candidates, weights)


def _fuzzy_score(
    candidate: AllocationCandidate,
    candidates: list[AllocationCandidate],
) -> float:
    max_response = max(item.response_time_ms for item in candidates)
    max_utilization = max(item.projected_utilization for item in candidates)
    response = candidate.response_time_ms / max_response if max_response else 0.0
    utilization = (
        candidate.projected_utilization / max_utilization
        if max_utilization
        else 0.0
    )
    low_response = max(0.0, min(1.0, (0.75 - response) / 0.5))
    high_response = max(0.0, min(1.0, (response - 0.25) / 0.5))
    low_utilization = max(0.0, min(1.0, (0.75 - utilization) / 0.5))
    high_utilization = max(0.0, min(1.0, (utilization - 0.25) / 0.5))
    urgency = candidate.deadline_urgency
    rule_scores = (
        (min(low_response, low_utilization), 1.0),
        (min(low_response, high_utilization, urgency), 0.85),
        (min(low_response, high_utilization), 0.65),
        (min(high_response, low_utilization, urgency), 0.55),
        (min(high_response, low_utilization), 0.35),
        (min(high_response, high_utilization), 0.0),
    )
    total_strength = sum(strength for strength, _ in rule_scores)
    if total_strength == 0:
        return 0.5
    return sum(strength * consequent for strength, consequent in rule_scores) / total_strength


def _topsis_closeness(
    candidate: AllocationCandidate,
    candidates: list[AllocationCandidate],
) -> float:
    alternatives = [
        (item.response_time_ms, item.projected_utilization, item.completion_time_seconds)
        for item in candidates
    ]
    denominators = tuple(
        math.sqrt(sum(values[index] ** 2 for values in alternatives))
        for index in range(3)
    )
    normalized = [
        tuple(
            values[index] / denominators[index] if denominators[index] else 0.0
            for index in range(3)
        )
        for values in alternatives
    ]
    weights = (0.4, 0.3, 0.3)
    weighted = [
        tuple(values[index] * weights[index] for index in range(3))
        for values in normalized
    ]
    ideal = tuple(min(values[index] for values in weighted) for index in range(3))
    anti_ideal = tuple(
        max(values[index] for values in weighted) for index in range(3)
    )
    candidate_index = candidates.index(candidate)
    current = weighted[candidate_index]
    distance_to_ideal = math.sqrt(
        sum((current[index] - ideal[index]) ** 2 for index in range(3))
    )
    distance_to_anti_ideal = math.sqrt(
        sum((current[index] - anti_ideal[index]) ** 2 for index in range(3))
    )
    distance_sum = distance_to_ideal + distance_to_anti_ideal
    return distance_to_anti_ideal / distance_sum if distance_sum else 0.5


def _aco_candidate(
    candidates: list[AllocationCandidate],
    weights: dict[str, float],
    state: SchedulerState,
) -> AllocationCandidate:
    initial_pheromone = 1.0
    costs = {
        item.node.node_id: _candidate_cost(item, candidates, weights)
        for item in candidates
    }
    for node_id in costs:
        state.pheromone.setdefault(node_id, initial_pheromone)

    best: AllocationCandidate | None = None
    best_cost = math.inf
    for _ in range(ACO_ANTS_PER_TASK):
        desirabilities = [
            state.pheromone[item.node.node_id]
            / (costs[item.node.node_id] + 0.05) ** 2
            for item in candidates
        ]
        total = sum(desirabilities)
        threshold = state.random.random() * total
        for item, desirability in zip(candidates, desirabilities):
            threshold -= desirability
            if threshold <= 0:
                cost = costs[item.node.node_id]
                if cost < best_cost:
                    best, best_cost = item, cost
                break
    for node_id in costs:
        state.pheromone[node_id] *= 1.0 - ACO_EVAPORATION_RATE
    if best is None:
        best = min(candidates, key=lambda item: costs[item.node.node_id])
        best_cost = costs[best.node.node_id]
    state.pheromone[best.node.node_id] += 1.0 / (0.1 + best_cost)
    return best


def _pso_candidate(
    candidates: list[AllocationCandidate],
    weights: dict[str, float],
    state: SchedulerState,
) -> AllocationCandidate:
    costs = [_candidate_cost(item, candidates, weights) for item in candidates]
    if len(candidates) == 1:
        return candidates[0]

    positions = [
        state.random.uniform(0.0, len(candidates) - 1)
        for _ in range(PSO_PARTICLE_COUNT)
    ]
    velocities = [0.0] * PSO_PARTICLE_COUNT
    personal_best_positions = positions.copy()
    personal_best_costs = [
        costs[round(position)] for position in positions
    ]
    global_best_position = personal_best_positions[
        min(range(PSO_PARTICLE_COUNT), key=personal_best_costs.__getitem__)
    ]
    lower, upper = 0.0, float(len(candidates) - 1)

    for _ in range(PSO_ITERATIONS):
        for index in range(PSO_PARTICLE_COUNT):
            current_index = round(positions[index])
            current_cost = costs[current_index]
            if current_cost < personal_best_costs[index]:
                personal_best_positions[index] = positions[index]
                personal_best_costs[index] = current_cost
            if current_cost < costs[round(global_best_position)]:
                global_best_position = positions[index]
        for index in range(PSO_PARTICLE_COUNT):
            velocities[index] = (
                0.5 * velocities[index]
                + 1.4 * state.random.random()
                * (personal_best_positions[index] - positions[index])
                + 1.4 * state.random.random()
                * (global_best_position - positions[index])
            )
            positions[index] = max(
                lower, min(upper, positions[index] + velocities[index])
            )

    return candidates[round(global_best_position)]


def choose_candidate(
    algorithm: str,
    candidates: list[AllocationCandidate],
    round_robin_offset: int,
    weights: dict[str, float],
    state: SchedulerState | None = None,
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

    if algorithm == "fuzzy_logic":
        candidate = max(
            candidates,
            key=lambda item: (
                _fuzzy_score(item, candidates),
                item.node.node_id,
            ),
        )
        return candidate, (
            f"Fuzzy Logic selected {candidate.node.name} using interpretable "
            "latency, utilization, and completion-time rules."
        )

    if algorithm == "topsis":
        candidate = max(
            candidates,
            key=lambda item: (
                _topsis_closeness(item, candidates),
                item.node.node_id,
            ),
        )
        return candidate, (
            f"TOPSIS selected {candidate.node.name} by closeness to the ideal "
            "multi-criteria node and distance from the anti-ideal."
        )

    if algorithm == "ant_colony":
        state = state or SchedulerState(0)
        candidate = _aco_candidate(candidates, weights, state)
        return candidate, (
            f"Ant Colony Optimization (ACO) selected {candidate.node.name} "
            "using pheromone-guided exploration and cost-based reinforcement."
        )

    if algorithm == "particle_swarm":
        state = state or SchedulerState(0)
        candidate = _pso_candidate(candidates, weights, state)
        return candidate, (
            f"Particle Swarm Optimization (PSO) selected {candidate.node.name} "
            "after searching the discrete eligible-node assignment space."
        )

    candidate = min(
        candidates,
        key=lambda item: (
            _weighted_sum_cost(item, candidates, weights),
            item.response_time_ms,
            item.node.node_id,
        ),
    )
    score = _weighted_sum_cost(candidate, candidates, weights)
    reason = (
        f"Weighted Sum Model (WSM) selected {candidate.node.name} with minimized "
        f"normalized cost {score:.3f}; weights are response={weights['response_time']:.2f}, "
        f"utilization={weights['utilization']:.2f}, "
        f"completion={weights['completion_time']:.2f}."
    )
    return candidate, reason
