import logging
import math
import statistics
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from simulation.database import SimulationDatabase
from simulation.engine import SimulationError, validate_node_configs
from simulation.models import (
    DEFAULT_ADAPTIVE_WEIGHTS,
    DEFAULT_NODE_CONFIGS,
    TASK_TEMPLATES,
    WORKLOAD_PROFILES,
    Node,
    Task,
    simulation_iso_time,
    utc_now_iso,
)
from simulation.scheduler import (
    SchedulingError,
    choose_candidate,
    eligible_candidates,
    validate_algorithm,
    validate_weights,
)


BENCHMARK_ALGORITHMS = (
    "round_robin",
    "weighted_round_robin",
    "least_loaded",
    "min_min",
    "adaptive_fog_cloud",
)
ALGORITHM_LABELS = {
    "round_robin": "Round Robin",
    "weighted_round_robin": "Weighted Round Robin",
    "least_loaded": "Least Loaded",
    "min_min": "Min-Min Scheduling",
    "adaptive_fog_cloud": "Adaptive Fog–Cloud",
}
MIN_MIN_BATCH_SECONDS = 1.0


class BenchmarkCancelled(Exception):
    pass


def prepare_workload(
    duration_seconds: int,
    workload: str,
    *,
    epoch: datetime | None = None,
) -> list[dict[str, Any]]:
    if (
        isinstance(duration_seconds, bool)
        or not isinstance(duration_seconds, int)
        or duration_seconds not in {30, 60, 120}
    ):
        raise SimulationError("duration_seconds must be 30, 60, or 120.")
    if not isinstance(workload, str) or workload not in WORKLOAD_PROFILES:
        raise SimulationError("workload must be light, normal, or heavy.")

    start = epoch or datetime(2000, 1, 1, tzinfo=timezone.utc)
    rate = WORKLOAD_PROFILES[workload]["arrival_rate_per_second"]
    task_count = int(math.floor(duration_seconds * rate + 1e-12))
    definitions: list[dict[str, Any]] = []
    for index in range(task_count):
        arrival_clock = (index + 1) / rate
        template = TASK_TEMPLATES[index % len(TASK_TEMPLATES)]
        definitions.append(
            {
                "task_id": f"benchmark-task-{index + 1:06d}",
                **template,
                "arrival_clock": arrival_clock,
                "arrival_time": simulation_iso_time(start, arrival_clock),
            }
        )
    return definitions


def _new_task(definition: dict[str, Any]) -> Task:
    return Task(
        task_id=definition["task_id"],
        source_sensor_id=definition["source_sensor_id"],
        task_type=definition["task_type"],
        cpu_demand=definition["cpu_demand"],
        data_size_mb=definition["data_size_mb"],
        priority=definition["priority"],
        deadline_seconds=definition["deadline_seconds"],
        arrival_time=definition["arrival_time"],
        arrival_clock=definition["arrival_clock"],
    )


def _start_next(node: Node, tasks: dict[str, Task], now: float) -> None:
    if node.active_task_id is not None or not node.queue:
        return
    task_id = node.queue.pop(0)
    task = tasks[task_id]
    task.status = "running"
    node.active_task_id = task_id
    node.active_task_started_at = now
    node.active_task_finishes_at = (
        now + node.network_latency_ms / 1000.0 + task.cpu_demand / node.processing_rate
    )


def _assign(
    task: Task,
    candidate: Any,
    explanation: str,
    tasks: dict[str, Task],
    now: float,
) -> None:
    node = candidate.node
    task.assigned_node_id = node.node_id
    task.estimated_network_latency_ms = node.network_latency_ms
    task.estimated_processing_time_ms = candidate.processing_time_ms
    task.estimated_response_time_ms = candidate.response_time_ms
    task.allocation_explanation = explanation
    if node.active_task_id is None:
        node.queue.append(task.task_id)
        _start_next(node, tasks, now)
    else:
        task.status = "queued"
        node.queue.append(task.task_id)


def _reject(task: Task, explanation: str) -> None:
    task.status = "rejected"
    task.allocation_explanation = explanation


def _schedule_individual(
    algorithm: str,
    task: Task,
    nodes: list[Node],
    tasks: dict[str, Task],
    now: float,
    weights: dict[str, float],
    round_robin_offset: int,
) -> int:
    candidates, excluded = eligible_candidates(nodes, tasks, task, now)
    if not candidates:
        detail = "; ".join(f"{node}: {reason}" for node, reason in excluded.items())
        _reject(
            task,
            "Task rejected: no online node satisfies capacity and deadline "
            f"constraints. {detail}",
        )
        return round_robin_offset

    try:
        selected, reason = choose_candidate(
            algorithm, candidates, round_robin_offset, weights
        )
    except SchedulingError as exc:
        raise SimulationError(str(exc)) from exc
    if excluded:
        reason += " Excluded nodes: " + "; ".join(
            f"{node}: {detail}" for node, detail in excluded.items()
        ) + "."
    _assign(task, selected, reason, tasks, now)
    if algorithm in {"round_robin", "weighted_round_robin"}:
        return round_robin_offset + 1
    return round_robin_offset


def _schedule_min_min_batch(
    batch: list[Task],
    nodes: list[Node],
    tasks: dict[str, Task],
    now: float,
) -> None:
    waiting = sorted(batch, key=lambda task: (task.arrival_clock, task.task_id))
    while waiting:
        best: tuple[float, str, str, Task, Any] | None = None
        no_candidate: dict[str, str] = {}
        for task in waiting:
            candidates, excluded = eligible_candidates(nodes, tasks, task, now)
            adjusted_candidates = [
                candidate
                for candidate in candidates
                if task.deadline_seconds is None
                or candidate.completion_time_seconds
                + now
                - task.arrival_clock
                <= task.deadline_seconds
            ]
            if not adjusted_candidates:
                no_candidate[task.task_id] = "; ".join(
                    f"{node}: {reason}" for node, reason in excluded.items()
                ) or "estimated completion misses the task deadline"
                continue
            for candidate in adjusted_candidates:
                key = (
                    candidate.completion_time_seconds,
                    task.task_id,
                    candidate.node.node_id,
                    task,
                    candidate,
                )
                if best is None or key[:3] < best[:3]:
                    best = key

        if best is None:
            for task in waiting:
                _reject(
                    task,
                    "Min-Min rejected task: no node satisfies availability, "
                    f"capacity, and deadline constraints. {no_candidate.get(task.task_id, '')}",
                )
            return

        _, _, _, selected_task, candidate = best
        explanation = (
            f"Min-Min batch scheduling selected {candidate.node.name}: this "
            f"task-node pair has the smallest estimated completion time "
            f"({candidate.completion_time_seconds:.3f}s) among the remaining "
            "tasks and eligible nodes in the current batch."
        )
        _assign(selected_task, candidate, explanation, tasks, now)
        waiting.remove(selected_task)


def run_experiment(
    algorithm: str,
    task_definitions: list[dict[str, Any]],
    node_configs: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    weights: dict[str, float],
    duration_seconds: int,
    *,
    cancellation: threading.Event | None = None,
    progress: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    if algorithm not in BENCHMARK_ALGORITHMS:
        raise SimulationError("Unknown benchmark algorithm.")
    if (
        isinstance(duration_seconds, bool)
        or not isinstance(duration_seconds, int)
        or duration_seconds not in {30, 60, 120}
    ):
        raise SimulationError("duration_seconds must be 30, 60, or 120.")
    try:
        checked_weights = validate_weights(weights)
        if algorithm != "min_min":
            validate_algorithm(algorithm)
    except SchedulingError as exc:
        raise SimulationError(str(exc)) from exc

    nodes = validate_node_configs([dict(config) for config in node_configs])
    tasks: dict[str, Task] = {}
    definitions = iter(task_definitions)
    next_definition = next(definitions, None)
    pending_batch: list[Task] = []
    round_robin_offset = 0
    now = 0.0
    next_min_min_batch = MIN_MIN_BATCH_SECONDS
    last_progress = -1.0
    busy_seconds = {node.node_id: 0.0 for node in nodes}
    completed: list[Task] = []

    while True:
        if cancellation is not None and cancellation.is_set():
            raise BenchmarkCancelled("Benchmark cancellation was requested.")

        completion_times = [
            node.active_task_finishes_at
            for node in nodes
            if node.active_task_finishes_at is not None
        ]
        events = [
            event
            for event in (
                next_definition["arrival_clock"] if next_definition is not None else None,
                min(completion_times) if completion_times else None,
                next_min_min_batch
                if algorithm == "min_min"
                and pending_batch
                and next_min_min_batch <= duration_seconds
                else None,
            )
            if event is not None
        ]
        if not events:
            if (
                algorithm == "min_min"
                and pending_batch
                and next_definition is None
            ):
                event_time = float(duration_seconds)
            else:
                break
        else:
            event_time = min(events)
        now = max(now, event_time)

        for node in nodes:
            if (
                node.active_task_finishes_at is not None
                and node.active_task_finishes_at <= now + 1e-12
            ):
                task = tasks[node.active_task_id]
                task.status = "completed"
                task.completion_clock = node.active_task_finishes_at
                task.completion_time = simulation_iso_time(
                    datetime.fromisoformat(task.arrival_time).astimezone(timezone.utc),
                    task.completion_clock - task.arrival_clock,
                )
                task.actual_response_time_ms = (
                    task.completion_clock - task.arrival_clock
                ) * 1000.0
                busy_seconds[node.node_id] += (
                    task.cpu_demand / node.processing_rate
                )
                completed.append(task)
                node.active_task_id = None
                node.active_task_started_at = None
                node.active_task_finishes_at = None
                _start_next(node, tasks, now)

        while (
            next_definition is not None
            and next_definition["arrival_clock"] <= now + 1e-12
        ):
            task = _new_task(next_definition)
            tasks[task.task_id] = task
            if algorithm == "min_min":
                pending_batch.append(task)
            else:
                round_robin_offset = _schedule_individual(
                    algorithm,
                    task,
                    nodes,
                    tasks,
                    now,
                    checked_weights,
                    round_robin_offset,
                )
            next_definition = next(definitions, None)

        if algorithm == "min_min" and pending_batch and (
            now + 1e-12 >= next_min_min_batch or next_definition is None
        ):
            _schedule_min_min_batch(pending_batch, nodes, tasks, now)
            pending_batch = []
            while next_min_min_batch <= now + 1e-12:
                next_min_min_batch += MIN_MIN_BATCH_SECONDS

        if (
            progress is not None
            and (now - last_progress >= max(1.0, duration_seconds / 30.0))
        ):
            progress(min(now, float(duration_seconds)))
            last_progress = now

        if next_definition is None and not pending_batch and not any(
            node.active_task_id is not None or node.queue for node in nodes
        ):
            break

    for node in nodes:
        while node.active_task_id is not None or node.queue:
            if node.active_task_id is None:
                _start_next(node, tasks, now)
            finishes_at = node.active_task_finishes_at
            if finishes_at is None:
                break
            now = max(now, finishes_at)
            task = tasks[node.active_task_id]
            task.status = "completed"
            task.completion_clock = finishes_at
            task.completion_time = simulation_iso_time(
                datetime.fromisoformat(task.arrival_time).astimezone(timezone.utc),
                finishes_at - task.arrival_clock,
            )
            task.actual_response_time_ms = (
                finishes_at - task.arrival_clock
            ) * 1000.0
            busy_seconds[node.node_id] += task.cpu_demand / node.processing_rate
            completed.append(task)
            node.active_task_id = None
            node.active_task_started_at = None
            node.active_task_finishes_at = None
            _start_next(node, tasks, now)

    if progress is not None:
        progress(float(duration_seconds))

    task_records = sorted(tasks.values(), key=lambda task: task.task_id)
    task_count = len(task_records)
    completed_count = sum(task.status == "completed" for task in task_records)
    failed_count = sum(task.status == "rejected" for task in task_records)
    response_times = [
        task.actual_response_time_ms
        for task in task_records
        if task.status == "completed" and task.actual_response_time_ms is not None
    ]
    deadline_misses = sum(
        task.deadline_seconds is not None
        and (
            (
                task.status == "rejected"
                and "deadline" in task.allocation_explanation.lower()
            )
            or (
                task.actual_response_time_ms is not None
                and task.actual_response_time_ms / 1000.0 > task.deadline_seconds
            )
        )
        for task in task_records
    )
    makespan = max(
        (task.completion_clock for task in task_records if task.completion_clock is not None),
        default=None,
    )
    node_utilization = (
        {
            node.node_id: busy_seconds[node.node_id] / makespan * 100.0
            for node in nodes
        }
        if makespan is not None and makespan > 0
        else {node.node_id: None for node in nodes}
    )
    populated_utilization = [
        value for value in node_utilization.values() if value is not None
    ]
    average_utilization = (
        sum(populated_utilization) / len(populated_utilization)
        if populated_utilization
        else None
    )
    load_imbalance = (
        statistics.pstdev(populated_utilization)
        if populated_utilization
        else None
    )
    cloud_count = sum(
        task.assigned_node_id is not None
        and any(
            node.node_id == task.assigned_node_id and node.node_type == "cloud"
            for node in nodes
        )
        for task in task_records
    )
    completed_response_times = [float(value) for value in response_times]

    return {
        "algorithm": algorithm,
        "algorithm_label": ALGORITHM_LABELS[algorithm],
        "duration_seconds": duration_seconds,
        "task_count": task_count,
        "completed_count": completed_count,
        "failed_count": failed_count,
        "deadline_misses": deadline_misses,
        "average_response_time_ms": (
            statistics.fmean(completed_response_times)
            if completed_response_times
            else None
        ),
        "median_response_time_ms": (
            statistics.median(completed_response_times)
            if completed_response_times
            else None
        ),
        "throughput_tasks_per_second": completed_count / duration_seconds,
        "average_node_utilization_percent": average_utilization,
        "load_imbalance_percent": load_imbalance,
        "cloud_offloading_rate_percent": (
            cloud_count / task_count * 100.0 if task_count else None
        ),
        "makespan_seconds": makespan,
        "node_utilization_percent": node_utilization,
        "task_outcomes": [
            {
                "task_id": task.task_id,
                "source_sensor_id": task.source_sensor_id,
                "assigned_node_id": task.assigned_node_id,
                "status": task.status,
                "arrival_clock": task.arrival_clock,
                "completion_clock": task.completion_clock,
                "actual_response_time_ms": task.actual_response_time_ms,
                "deadline_seconds": task.deadline_seconds,
                "allocation_explanation": task.allocation_explanation,
            }
            for task in task_records
        ],
    }


class BenchmarkManager:
    def __init__(
        self,
        database: SimulationDatabase,
        *,
        node_configs: list[dict[str, Any]] | None = None,
        weights: dict[str, float] | None = None,
        runner: Callable[..., dict[str, Any]] = run_experiment,
        logger: logging.Logger | None = None,
    ):
        self.database = database
        self.node_configs = [
            dict(config)
            for config in (
                DEFAULT_NODE_CONFIGS if node_configs is None else node_configs
            )
        ]
        validate_node_configs(self.node_configs)
        try:
            self.weights = validate_weights(weights or DEFAULT_ADAPTIVE_WEIGHTS)
        except SchedulingError as exc:
            raise SimulationError(str(exc)) from exc
        self.runner = runner
        self.logger = logger or logging.getLogger(__name__)
        self._lock = threading.RLock()
        self._active_id: str | None = None
        self._cancellation: threading.Event | None = None
        self.database.mark_interrupted_benchmarks(utc_now_iso())

    def start(self, duration_seconds: int, workload: str) -> dict[str, Any]:
        if (
            isinstance(duration_seconds, bool)
            or not isinstance(duration_seconds, int)
            or duration_seconds not in {30, 60, 120}
        ):
            raise SimulationError("duration_seconds must be 30, 60, or 120.")
        if not isinstance(workload, str) or workload not in WORKLOAD_PROFILES:
            raise SimulationError("workload must be light, normal, or heavy.")
        with self._lock:
            if self._active_id is not None:
                active = self.database.get_benchmark(self._active_id)
                if active is not None and active["status"] == "running":
                    raise SimulationError("A benchmark is already running.", 409)
            benchmark_id = str(uuid.uuid4())
            created_at = utc_now_iso()
            task_definitions = prepare_workload(duration_seconds, workload)
            configuration = {
                "duration_seconds": duration_seconds,
                "workload": workload,
                "task_count": len(task_definitions),
                "algorithm_order": list(BENCHMARK_ALGORITHMS),
                "adaptive_weights": self.weights,
                "node_configs": self.node_configs,
                "min_min_batch_seconds": MIN_MIN_BATCH_SECONDS,
                "workload_generator": "deterministic-cyclic-templates-v1",
            }
            self.database.create_benchmark(
                benchmark_id,
                duration_seconds,
                workload,
                configuration,
                task_definitions,
                created_at,
            )
            cancellation = threading.Event()
            self._active_id = benchmark_id
            self._cancellation = cancellation
            worker = threading.Thread(
                target=self._run,
                args=(
                    benchmark_id,
                    duration_seconds,
                    task_definitions,
                    cancellation,
                ),
                name=f"fogflow-benchmark-{benchmark_id[:8]}",
                daemon=True,
            )
            worker.start()
            return self.database.get_benchmark(benchmark_id) or {}

    def cancel(self, benchmark_id: str) -> dict[str, Any]:
        with self._lock:
            record = self.database.get_benchmark(benchmark_id)
            if record is None:
                raise SimulationError("Benchmark not found.", 404)
            if (
                benchmark_id != self._active_id
                or record["status"] != "running"
                or self._cancellation is None
            ):
                raise SimulationError("Only a running benchmark can be cancelled.", 409)
            self._cancellation.set()
            return record

    def get(self, benchmark_id: str) -> dict[str, Any]:
        record = self.database.get_benchmark(benchmark_id)
        if record is None:
            raise SimulationError("Benchmark not found.", 404)
        return record

    def list(self) -> list[dict[str, Any]]:
        return self.database.list_benchmarks()

    def results(self, benchmark_id: str) -> dict[str, Any]:
        record = self.database.get_benchmark(benchmark_id, include_results=True)
        if record is None:
            raise SimulationError("Benchmark not found.", 404)
        if record["status"] != "completed":
            raise SimulationError(
                "Benchmark results are available only after all five experiments complete.",
                409,
            )
        if record["result_count"] != len(BENCHMARK_ALGORITHMS):
            raise SimulationError(
                "The completed benchmark has an invalid result count.", 500
            )
        return {
            "benchmark_id": benchmark_id,
            "configuration": record["configuration"],
            "results": record["results"],
        }

    def _write_progress(
        self,
        benchmark_id: str,
        started_at: float,
        *,
        current_algorithm: str | None,
        completed_experiments: int,
        progress_percent: float,
        simulated_seconds: float,
        results: list[dict[str, Any]],
        status: str = "running",
        error: str | None = None,
    ) -> None:
        self.database.update_benchmark(
            benchmark_id,
            status=status,
            current_algorithm=current_algorithm,
            completed_experiments=completed_experiments,
            progress_percent=progress_percent,
            simulated_seconds=simulated_seconds,
            elapsed_seconds=max(0.0, time.monotonic() - started_at),
            results=results,
            error=error,
            updated_at=utc_now_iso(),
        )

    def _run(
        self,
        benchmark_id: str,
        duration_seconds: int,
        task_definitions: list[dict[str, Any]],
        cancellation: threading.Event,
    ) -> None:
        started_at = time.monotonic()
        results: list[dict[str, Any]] = []
        try:
            for index, algorithm in enumerate(BENCHMARK_ALGORITHMS):
                if cancellation.is_set():
                    raise BenchmarkCancelled("Benchmark cancellation was requested.")
                self._write_progress(
                    benchmark_id,
                    started_at,
                    current_algorithm=algorithm,
                    completed_experiments=index,
                    progress_percent=index / len(BENCHMARK_ALGORITHMS) * 100.0,
                    simulated_seconds=0.0,
                    results=results,
                )

                def report_progress(simulated_seconds: float) -> None:
                    if cancellation.is_set():
                        raise BenchmarkCancelled(
                            "Benchmark cancellation was requested."
                        )
                    fraction = min(
                        max(simulated_seconds, 0.0) / duration_seconds, 1.0
                    )
                    percent = (
                        index + fraction
                    ) / len(BENCHMARK_ALGORITHMS) * 100.0
                    self._write_progress(
                        benchmark_id,
                        started_at,
                        current_algorithm=algorithm,
                        completed_experiments=index,
                        progress_percent=min(percent, 99.9),
                        simulated_seconds=simulated_seconds,
                        results=results,
                    )

                result = self.runner(
                    algorithm,
                    task_definitions,
                    self.node_configs,
                    self.weights,
                    duration_seconds,
                    cancellation=cancellation,
                    progress=report_progress,
                )
                if cancellation.is_set():
                    raise BenchmarkCancelled("Benchmark cancellation was requested.")
                results.append(result)
                self._write_progress(
                    benchmark_id,
                    started_at,
                    current_algorithm=(
                        BENCHMARK_ALGORITHMS[index + 1]
                        if index + 1 < len(BENCHMARK_ALGORITHMS)
                        else None
                    ),
                    completed_experiments=index + 1,
                    progress_percent=(index + 1) / len(BENCHMARK_ALGORITHMS) * 100.0,
                    simulated_seconds=float(duration_seconds),
                    results=results,
                )
            self._write_progress(
                benchmark_id,
                started_at,
                current_algorithm=None,
                completed_experiments=len(BENCHMARK_ALGORITHMS),
                progress_percent=100.0,
                simulated_seconds=float(duration_seconds),
                results=results,
                status="completed",
            )
        except BenchmarkCancelled as exc:
            self._write_progress(
                benchmark_id,
                started_at,
                current_algorithm=None,
                completed_experiments=len(results),
                progress_percent=len(results) / len(BENCHMARK_ALGORITHMS) * 100.0,
                simulated_seconds=0.0,
                results=results,
                status="cancelled",
                error=str(exc),
            )
        except Exception as exc:
            self.logger.exception("Benchmark %s failed.", benchmark_id)
            self._write_progress(
                benchmark_id,
                started_at,
                current_algorithm=None,
                completed_experiments=len(results),
                progress_percent=len(results) / len(BENCHMARK_ALGORITHMS) * 100.0,
                simulated_seconds=0.0,
                results=results,
                status="failed",
                error=str(exc) or type(exc).__name__,
            )
        finally:
            with self._lock:
                if self._active_id == benchmark_id:
                    self._active_id = None
                    self._cancellation = None
