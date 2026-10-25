import math
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from simulation.database import SimulationDatabase
from simulation.faults import (
    NodeFaultSchedule,
    create_node_fault_schedules,
)
from simulation.models import (
    DEFAULT_NODE_CONFIGS,
    FOG_NODE_COUNT_MAX,
    FOG_NODE_COUNT_MIN,
    TASK_TEMPLATES,
    WORKLOAD_NAMES,
    Node,
    Task,
    create_node_configs,
    simulation_iso_time,
    utc_now_iso,
    workload_arrival_rate,
)
from simulation.scheduler import (
    SchedulerState,
    SchedulingError,
    choose_candidate,
    eligible_candidates,
    validate_algorithm,
    validate_weights,
)


class SimulationError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _number(value: Any, field_name: str, *, minimum: float, strict: bool = False) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or (value <= minimum if strict else value < minimum)
    ):
        comparator = "greater than" if strict else "at least"
        raise SimulationError(f"{field_name} must be a finite number {comparator} {minimum}.")
    return float(value)


def validate_node_configs(configs: list[dict[str, Any]] | None) -> list[Node]:
    raw_configs = DEFAULT_NODE_CONFIGS if configs is None else configs
    if not isinstance(raw_configs, (list, tuple)):
        raise SimulationError("Node configuration must be a list or tuple.")

    nodes: list[Node] = []
    ids: set[str] = set()
    for config in raw_configs:
        if not isinstance(config, dict):
            raise SimulationError("Every node configuration must be an object.")
        node_id = config.get("id")
        name = config.get("name")
        node_type = config.get("node_type")
        if not isinstance(node_id, str) or not node_id.strip() or node_id in ids:
            raise SimulationError("Node IDs must be unique, non-empty strings.")
        if not isinstance(name, str) or not name.strip():
            raise SimulationError(f"Node '{node_id}' must have a non-empty name.")
        if not isinstance(node_type, str) or node_type not in {"fog", "cloud"}:
            raise SimulationError(f"Node '{node_id}' must have type fog or cloud.")
        if not isinstance(config.get("online", True), bool):
            raise SimulationError(f"Node '{node_id}' online must be a boolean.")
        ids.add(node_id)
        nodes.append(
            Node(
                node_id=node_id,
                name=name,
                node_type=node_type,
                processing_capacity=_number(
                    config.get("processing_capacity"),
                    f"{node_id}.processing_capacity",
                    minimum=0,
                    strict=True,
                ),
                processing_rate=_number(
                    config.get("processing_rate"),
                    f"{node_id}.processing_rate",
                    minimum=0,
                    strict=True,
                ),
                network_latency_ms=_number(
                    config.get("network_latency_ms"),
                    f"{node_id}.network_latency_ms",
                    minimum=0,
                ),
                online=config.get("online", True),
            )
        )
    fog_count = sum(node.node_type == "fog" for node in nodes)
    cloud_count = sum(node.node_type == "cloud" for node in nodes)
    if not FOG_NODE_COUNT_MIN <= fog_count <= FOG_NODE_COUNT_MAX or cloud_count != 1:
        raise SimulationError(
            f"Node configuration requires {FOG_NODE_COUNT_MIN} to "
            f"{FOG_NODE_COUNT_MAX} fog nodes and one cloud server."
        )
    return nodes


class SimulationEngine:
    def __init__(
        self,
        database: SimulationDatabase,
        node_configs: list[dict[str, Any]] | None = None,
        adaptive_weights: dict[str, float] | None = None,
        background_simulation: bool = True,
    ):
        self.database = database
        try:
            self.weights = validate_weights(adaptive_weights)
        except SchedulingError as exc:
            raise SimulationError(str(exc)) from exc
        self._initial_node_configs = node_configs
        self.background_simulation = background_simulation
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._worker: threading.Thread | None = None
        self._last_wall_clock: float | None = None
        self._generator_count = 0
        self._next_arrival_clock: float | None = None
        self._round_robin_offset = 0
        self._node_faults: dict[str, NodeFaultSchedule] = {}
        self._scheduler_state = SchedulerState(0)
        self.nodes: list[Node] = []
        self.tasks: dict[str, Task] = {}
        self.simulation_clock = 0.0
        self.state = "idle"
        self.algorithm = "adaptive_fog_cloud"
        self.workload = "normal"
        self.manual_tasks_per_minute: int | None = None
        self.started_at: str | None = None
        self.epoch = datetime.now(timezone.utc)
        self.run_id = ""
        self._new_run()

    def _new_run(self) -> None:
        self.run_id = str(uuid.uuid4())
        self.nodes = validate_node_configs(self._initial_node_configs)
        self.tasks = {}
        self.simulation_clock = 0.0
        self.state = "idle"
        self.algorithm = "adaptive_fog_cloud"
        self.workload = "normal"
        self.manual_tasks_per_minute = None
        self.started_at = None
        self.epoch = datetime.now(timezone.utc)
        self._generator_count = 0
        self._next_arrival_clock = None
        self._round_robin_offset = 0
        self._scheduler_state = SchedulerState(0)
        self._node_faults = {}
        self.database.create_run(
            self.run_id,
            self.algorithm,
            self.workload,
            self.weights,
            utc_now_iso(),
        )

    def _persist_run(self, ended_at: str | None = None) -> None:
        self.database.update_run(
            self.run_id,
            self.state,
            self.algorithm,
            self.workload,
            self.weights,
            self.simulation_clock,
            self.started_at,
            ended_at,
        )

    def _sync_wall_clock_locked(self) -> None:
        now = time.monotonic()
        if self.state == "running" and self._last_wall_clock is not None:
            elapsed = max(0.0, now - self._last_wall_clock)
            self._advance_locked(elapsed)
        self._last_wall_clock = now if self.state == "running" else None

    def _ensure_worker(self) -> None:
        if not self.background_simulation:
            return
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(
                target=self._worker_loop,
                name="fogflow-simulation",
                daemon=True,
            )
            self._worker.start()
        self._condition.notify_all()

    def _worker_loop(self) -> None:
        with self._condition:
            while True:
                self._condition.wait_for(lambda: self.state == "running")
                self._condition.wait(timeout=0.05)
                if self.state != "running":
                    continue
                self._sync_wall_clock_locked()

    def start(
        self,
        workload: str = "normal",
        algorithm: str = "adaptive_fog_cloud",
        weights: dict[str, float] | None = None,
        manual_tasks_per_minute: int | None = None,
    ) -> dict[str, Any]:
        with self._condition:
            self._sync_wall_clock_locked()
            if self.state == "running":
                raise SimulationError("Simulation is already running.", 409)
            if self.state == "paused":
                raise SimulationError("Simulation is paused; use the resume endpoint.", 409)
            if not isinstance(workload, str) or workload not in WORKLOAD_NAMES:
                raise SimulationError("workload must be light, normal, heavy, extreme, or manual.")
            try:
                rate = workload_arrival_rate(workload, manual_tasks_per_minute)
            except ValueError as exc:
                raise SimulationError(str(exc)) from exc
            try:
                algorithm = validate_algorithm(algorithm)
                validated_weights = validate_weights(weights) if weights is not None else self.weights
            except SchedulingError as exc:
                raise SimulationError(str(exc)) from exc
            self._node_faults = create_node_fault_schedules(
                self.nodes, uuid.UUID(self.run_id).int & 0xFFFFFFFF
            )
            self._scheduler_state = SchedulerState(
                uuid.UUID(self.run_id).int & 0xFFFFFFFF
            )
            self.algorithm = algorithm
            self.workload = workload
            self.manual_tasks_per_minute = manual_tasks_per_minute
            self.weights = validated_weights
            self.started_at = utc_now_iso()
            self.state = "running"
            self._next_arrival_clock = self.simulation_clock + 1.0 / rate
            for node in self.nodes:
                self._start_next_locked(node)
            self._last_wall_clock = time.monotonic()
            self._persist_run()
            self._ensure_worker()
            return self._snapshot_locked()

    def configure_fog_node_count(self, fog_node_count: int) -> list[dict[str, Any]]:
        try:
            node_configs = create_node_configs(fog_node_count)
        except ValueError as exc:
            raise SimulationError(str(exc)) from exc
        with self._condition:
            self._sync_wall_clock_locked()
            if self.state != "idle" or self.tasks:
                raise SimulationError(
                    "Reset the simulation before changing the fog node count.", 409
                )
            self._initial_node_configs = node_configs
            self.nodes = validate_node_configs(node_configs)
            return [node.to_dict(self.tasks) for node in self.nodes]

    def pause(self) -> dict[str, Any]:
        with self._condition:
            self._sync_wall_clock_locked()
            if self.state != "running":
                raise SimulationError("Only a running simulation can be paused.", 409)
            self.state = "paused"
            self._last_wall_clock = None
            self._persist_run()
            self._condition.notify_all()
            return self._snapshot_locked()

    def resume(self) -> dict[str, Any]:
        with self._condition:
            if self.state != "paused":
                raise SimulationError("Only a paused simulation can be resumed.", 409)
            self.state = "running"
            self._last_wall_clock = time.monotonic()
            self._persist_run()
            self._ensure_worker()
            return self._snapshot_locked()

    def reset(self) -> dict[str, Any]:
        with self._condition:
            self._sync_wall_clock_locked()
            old_run_id = self.run_id
            old_tasks = [task.to_dict() for task in self.tasks.values()]
            self.state = "reset"
            self._last_wall_clock = None
            self._persist_run(utc_now_iso())
            self.database.finish_experiment(
                old_run_id,
                self.algorithm,
                self.workload,
                old_tasks,
                utc_now_iso(),
            )
            self._new_run()
            self._condition.notify_all()
            return self._snapshot_locked()

    def advance(self, seconds: float) -> dict[str, Any]:
        duration = _number(seconds, "seconds", minimum=0)
        with self._lock:
            if self.state != "running":
                raise SimulationError("Simulation time can advance only while running.", 409)
            self._advance_locked(duration)
            self._last_wall_clock = time.monotonic()
            return self._snapshot_locked()

    def _advance_locked(self, duration: float) -> None:
        if duration <= 0:
            return
        pending_tasks: dict[str, Task] = {}
        pending_decisions: list[dict[str, Any]] = []
        target_clock = self.simulation_clock + duration
        if self._next_arrival_clock is None:
            rate = workload_arrival_rate(
                self.workload, self.manual_tasks_per_minute
            )
            self._next_arrival_clock = self.simulation_clock + 1.0 / rate

        while True:
            completions = [
                node.active_task_finishes_at
                for node in self.nodes
                if node.active_task_finishes_at is not None
            ]
            next_completion = min(completions) if completions else None
            next_fault = min(
                (
                    schedule.next_transition_at
                    for schedule in self._node_faults.values()
                ),
                default=None,
            )
            next_arrival = self._next_arrival_clock
            possible_events = [
                event
                for event in (next_completion, next_fault, next_arrival)
                if event is not None and event <= target_clock
            ]
            if not possible_events:
                break
            event_clock = min(possible_events)
            self.simulation_clock = event_clock

            for node in self.nodes:
                if (
                    node.active_task_finishes_at is not None
                    and node.active_task_finishes_at <= event_clock + 1e-12
                ):
                    self._complete_active_locked(node, pending_tasks)

            for node in self.nodes:
                schedule = self._node_faults.get(node.node_id)
                if (
                    schedule is None
                    or schedule.next_transition_at > event_clock + 1e-12
                ):
                    continue
                if schedule.transition(event_clock):
                    node.online = False
                    if node.active_task_id is not None:
                        failed_task = self.tasks[node.active_task_id]
                        failed_task.status = "failed"
                        failed_task.allocation_explanation += (
                            f" Task failed when {node.name} experienced a transient "
                            "node outage during processing."
                        )
                        self._save_task_locked(failed_task, pending_tasks)
                        node.active_task_id = None
                        node.active_task_started_at = None
                        node.active_task_finishes_at = None
                else:
                    node.online = True
                    self._start_next_locked(node, pending_tasks)

            if (
                self._next_arrival_clock is not None
                and self._next_arrival_clock <= event_clock + 1e-12
            ):
                self._generate_profile_task_locked(
                    event_clock, pending_tasks, pending_decisions
                )
                rate = workload_arrival_rate(
                    self.workload, self.manual_tasks_per_minute
                )
                self._next_arrival_clock += 1.0 / rate

        self.simulation_clock = target_clock
        if pending_tasks or pending_decisions:
            self.database.save_simulation_batch(
                self.run_id,
                [
                    (
                        task.to_dict(),
                        task.arrival_clock,
                        task.completion_clock,
                    )
                    for task in pending_tasks.values()
                ],
                pending_decisions,
            )
        self._persist_run()

    def _new_task(self, spec: dict[str, Any], arrival_clock: float) -> Task:
        task_id = f"task-{self.run_id[:8]}-{len(self.tasks) + 1:06d}"
        return Task(
            task_id=task_id,
            source_sensor_id=spec["source_sensor_id"],
            task_type=spec["task_type"],
            cpu_demand=spec["cpu_demand"],
            data_size_mb=spec["data_size_mb"],
            priority=spec["priority"],
            deadline_seconds=spec["deadline_seconds"],
            arrival_time=simulation_iso_time(self.epoch, arrival_clock),
            arrival_clock=arrival_clock,
        )

    def _generate_profile_task_locked(
        self,
        arrival_clock: float,
        pending_tasks: dict[str, Task] | None = None,
        pending_decisions: list[dict[str, Any]] | None = None,
    ) -> None:
        template = TASK_TEMPLATES[self._generator_count % len(TASK_TEMPLATES)]
        self._generator_count += 1
        task = self._new_task(dict(template), arrival_clock)
        self._schedule_locked(task, pending_tasks, pending_decisions)

    def generate_tasks(self, specifications: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with self._condition:
            self._sync_wall_clock_locked()
            created: list[dict[str, Any]] = []
            for specification in specifications:
                task = self._new_task(specification, self.simulation_clock)
                self.tasks[task.task_id] = task
                self._schedule_locked(task)
                created.append(task.to_dict())
            self._persist_run()
            return created

    def template_specs(self, count: int) -> list[dict[str, Any]]:
        with self._condition:
            self._sync_wall_clock_locked()
            specifications = []
            for _ in range(count):
                template = TASK_TEMPLATES[
                    self._generator_count % len(TASK_TEMPLATES)
                ]
                self._generator_count += 1
                specifications.append(dict(template))
            return specifications

    def _save_task_locked(
        self, task: Task, pending_tasks: dict[str, Task] | None
    ) -> None:
        if pending_tasks is None:
            self.database.save_task(
                self.run_id,
                task.to_dict(),
                task.arrival_clock,
                task.completion_clock,
            )
        else:
            pending_tasks[task.task_id] = task

    def _save_decision_locked(
        self,
        task: Task,
        selected_node_id: str | None,
        explanation: str,
        candidates: list[dict[str, Any]],
        pending_decisions: list[dict[str, Any]] | None,
    ) -> None:
        if pending_decisions is None:
            self.database.save_decision(
                self.run_id,
                task.task_id,
                self.algorithm,
                selected_node_id,
                explanation,
                candidates,
                self.simulation_clock,
            )
        else:
            pending_decisions.append(
                {
                    "task_id": task.task_id,
                    "algorithm": self.algorithm,
                    "selected_node_id": selected_node_id,
                    "explanation": explanation,
                    "candidates": candidates,
                    "decision_clock": self.simulation_clock,
                }
            )

    def _schedule_locked(
        self,
        task: Task,
        pending_tasks: dict[str, Task] | None = None,
        pending_decisions: list[dict[str, Any]] | None = None,
    ) -> None:
        self.tasks[task.task_id] = task
        candidates, excluded = eligible_candidates(
            self.nodes, self.tasks, task, self.simulation_clock
        )
        candidate_records = [
            {
                "node_id": candidate.node.node_id,
                "projected_workload": candidate.projected_workload,
                "projected_utilization": candidate.projected_utilization,
                "estimated_network_latency_ms": candidate.node.network_latency_ms,
                "estimated_processing_time_ms": candidate.processing_time_ms,
                "estimated_response_time_ms": candidate.response_time_ms,
            }
            for candidate in candidates
        ]
        if not candidates:
            detail = "; ".join(f"{node}: {reason}" for node, reason in excluded.items())
            task.status = "rejected"
            task.allocation_explanation = (
                "Task rejected: no online node satisfies capacity and deadline "
                f"constraints. {detail}"
            )
            self._save_task_locked(task, pending_tasks)
            self._save_decision_locked(
                task,
                None,
                task.allocation_explanation,
                candidate_records,
                pending_decisions,
            )
            return

        try:
            selected, reason = choose_candidate(
                self.algorithm,
                candidates,
                self._round_robin_offset,
                self.weights,
                self._scheduler_state,
            )
        except SchedulingError as exc:
            raise SimulationError(str(exc)) from exc
        if excluded:
            excluded_summary = "; ".join(
                f"{node_id}: {reason}" for node_id, reason in excluded.items()
            )
            reason = f"{reason} Excluded nodes: {excluded_summary}."
        if self.algorithm in {"round_robin", "weighted_round_robin"}:
            self._round_robin_offset += 1

        node = selected.node
        task.assigned_node_id = node.node_id
        task.estimated_network_latency_ms = node.network_latency_ms
        task.estimated_processing_time_ms = selected.processing_time_ms
        task.estimated_response_time_ms = selected.response_time_ms
        task.allocation_explanation = reason
        self.tasks[task.task_id] = task

        if self.state == "running" and node.active_task_id is None:
            node.active_task_id = task.task_id
            node.active_task_started_at = self.simulation_clock
            node.active_task_finishes_at = (
                self.simulation_clock
                + node.network_latency_ms / 1000.0
                + selected.processing_time_ms / 1000.0
            )
            task.status = "running"
        else:
            task.status = "queued"
            node.queue.append(task.task_id)

        self._save_task_locked(task, pending_tasks)
        self._save_decision_locked(
            task,
            node.node_id,
            reason,
            candidate_records,
            pending_decisions,
        )

    def _complete_active_locked(
        self, node: Node, pending_tasks: dict[str, Task] | None = None
    ) -> None:
        if node.active_task_id is None:
            return
        task = self.tasks[node.active_task_id]
        task.status = "completed"
        task.completion_clock = self.simulation_clock
        task.completion_time = simulation_iso_time(self.epoch, self.simulation_clock)
        task.actual_response_time_ms = (
            self.simulation_clock - task.arrival_clock
        ) * 1000.0
        self._save_task_locked(task, pending_tasks)
        node.active_task_id = None
        node.active_task_started_at = None
        node.active_task_finishes_at = None
        self._start_next_locked(node, pending_tasks)

    def _start_next_locked(
        self, node: Node, pending_tasks: dict[str, Task] | None = None
    ) -> None:
        if (
            self.state != "running"
            or not node.online
            or node.active_task_id is not None
            or not node.queue
        ):
            return
        task_id = node.queue.pop(0)
        task = self.tasks[task_id]
        task.status = "running"
        node.active_task_id = task_id
        node.active_task_started_at = self.simulation_clock
        node.active_task_finishes_at = (
            self.simulation_clock
            + node.network_latency_ms / 1000.0
            + task.cpu_demand / node.processing_rate
        )
        self._save_task_locked(task, pending_tasks)

    def nodes_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            self._sync_wall_clock_locked()
            return [self._node_telemetry_locked(node) for node in self.nodes]

    def _node_telemetry_locked(self, node: Node) -> dict[str, Any]:
        record = node.to_dict(self.tasks)
        schedule = self._node_faults.get(node.node_id)
        record["faulted"] = schedule.is_down if schedule is not None else False
        return record

    def node_configs_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "id": node.node_id,
                    "name": node.name,
                    "node_type": node.node_type,
                    "processing_capacity": node.processing_capacity,
                    "processing_rate": node.processing_rate,
                    "network_latency_ms": node.network_latency_ms,
                    "online": node.online,
                }
                for node in self.nodes
            ]

    def tasks_snapshot(
        self,
        limit: int,
        offset: int,
        status: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._sync_wall_clock_locked()
            records, total = self.database.list_tasks(
                self.run_id, limit, offset, status
            )
            return {
                "run_id": self.run_id,
                "tasks": records,
                "total": total,
                "limit": limit,
                "offset": offset,
            }

    def metrics_snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._sync_wall_clock_locked()
            records = [task.to_dict() for task in self.tasks.values()]
            completed = [
                task.actual_response_time_ms
                for task in self.tasks.values()
                if task.status == "completed"
                and task.actual_response_time_ms is not None
            ]
            task_counts_by_sensor: dict[str, int] = {}
            for task in self.tasks.values():
                task_counts_by_sensor[task.source_sensor_id] = (
                    task_counts_by_sensor.get(task.source_sensor_id, 0) + 1
                )
            return {
                "run_id": self.run_id,
                "state": self.state,
                "algorithm": self.algorithm,
                "workload": self.workload,
                "manual_tasks_per_minute": self.manual_tasks_per_minute,
                "simulation_clock_seconds": self.simulation_clock,
                "task_count": len(records),
                "tasks_completed": sum(task["status"] == "completed" for task in records),
                "tasks_running": sum(task["status"] == "running" for task in records),
                "tasks_queued": sum(task["status"] == "queued" for task in records),
                "tasks_failed": sum(task["status"] == "failed" for task in records),
                "tasks_rejected": sum(task["status"] == "rejected" for task in records),
                "average_response_time_ms": (
                    sum(completed) / len(completed) if completed else None
                ),
                "task_counts_by_sensor": task_counts_by_sensor,
                "nodes": [self._node_telemetry_locked(node) for node in self.nodes],
            }

    def _snapshot_locked(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "state": self.state,
            "algorithm": self.algorithm,
            "workload": self.workload,
            "manual_tasks_per_minute": self.manual_tasks_per_minute,
            "simulation_clock_seconds": self.simulation_clock,
        }
