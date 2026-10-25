from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


FOG_NODE_COUNT_MIN = 1
FOG_NODE_COUNT_MAX = 75
FOG_NODE_PROFILES = (
    {"processing_capacity": 100.0, "processing_rate": 25.0, "network_latency_ms": 4.0},
    {"processing_capacity": 120.0, "processing_rate": 30.0, "network_latency_ms": 7.0},
    {"processing_capacity": 80.0, "processing_rate": 20.0, "network_latency_ms": 12.0},
)
CLOUD_NODE_CONFIG = {
    "id": "cloud-1",
    "name": "Cloud Server",
    "node_type": "cloud",
    "processing_capacity": 500.0,
    "processing_rate": 15.0,
    "network_latency_ms": 38.0,
    "online": True,
}


def create_node_configs(fog_node_count: int) -> list[dict[str, Any]]:
    if (
        isinstance(fog_node_count, bool)
        or not isinstance(fog_node_count, int)
        or not FOG_NODE_COUNT_MIN <= fog_node_count <= FOG_NODE_COUNT_MAX
    ):
        raise ValueError(
            f"fog_node_count must be an integer between "
            f"{FOG_NODE_COUNT_MIN} and {FOG_NODE_COUNT_MAX}."
        )
    fog_nodes = [
        {
            "id": f"fog-{index + 1}",
            "name": f"Fog Node {index + 1}",
            "node_type": "fog",
            **FOG_NODE_PROFILES[index % len(FOG_NODE_PROFILES)],
            "online": True,
        }
        for index in range(fog_node_count)
    ]
    return [*fog_nodes, dict(CLOUD_NODE_CONFIG)]


DEFAULT_NODE_CONFIGS = tuple(create_node_configs(3))

DEFAULT_ADAPTIVE_WEIGHTS = {
    "response_time": 0.4,
    "utilization": 0.35,
    "completion_time": 0.25,
}

WORKLOAD_PROFILES = {
    "light": {"arrival_rate_per_second": 1.0},
    "normal": {"arrival_rate_per_second": 4.0},
    "heavy": {"arrival_rate_per_second": 10.0},
    "extreme": {"arrival_rate_per_second": 20.0},
}
MANUAL_TASKS_PER_MINUTE_MIN = 1
MANUAL_TASKS_PER_MINUTE_MAX = 10_000
WORKLOAD_NAMES = (*WORKLOAD_PROFILES, "manual")


def workload_arrival_rate(
    workload: str,
    manual_tasks_per_minute: int | None = None,
) -> float:
    if workload == "manual":
        if (
            isinstance(manual_tasks_per_minute, bool)
            or not isinstance(manual_tasks_per_minute, int)
            or not MANUAL_TASKS_PER_MINUTE_MIN
            <= manual_tasks_per_minute
            <= MANUAL_TASKS_PER_MINUTE_MAX
        ):
            raise ValueError(
                "manual_tasks_per_minute must be an integer between "
                f"{MANUAL_TASKS_PER_MINUTE_MIN} and {MANUAL_TASKS_PER_MINUTE_MAX}."
            )
        return manual_tasks_per_minute / 60.0
    if workload not in WORKLOAD_PROFILES:
        raise ValueError("workload must be light, normal, heavy, extreme, or manual.")
    if manual_tasks_per_minute is not None:
        raise ValueError(
            "manual_tasks_per_minute is only accepted for the manual workload."
        )
    return WORKLOAD_PROFILES[workload]["arrival_rate_per_second"]

TASK_TEMPLATES = (
    {
        "source_sensor_id": "IOT-CAM-01",
        "task_type": "computer_vision",
        "cpu_demand": 18.0,
        "data_size_mb": 12.4,
        "priority": 3,
        "deadline_seconds": 2.0,
    },
    {
        "source_sensor_id": "IOT-MED-02",
        "task_type": "vitals",
        "cpu_demand": 10.0,
        "data_size_mb": 0.82,
        "priority": 3,
        "deadline_seconds": 1.5,
    },
    {
        "source_sensor_id": "IOT-GRD-03",
        "task_type": "anomaly_detection",
        "cpu_demand": 8.0,
        "data_size_mb": 0.045,
        "priority": 2,
        "deadline_seconds": 1.0,
    },
    {
        "source_sensor_id": "IOT-AIR-04",
        "task_type": "aggregation",
        "cpu_demand": 5.0,
        "data_size_mb": 0.012,
        "priority": 1,
        "deadline_seconds": 3.0,
    },
    {
        "source_sensor_id": "IOT-FLW-05",
        "task_type": "flow_analysis",
        "cpu_demand": 14.0,
        "data_size_mb": 3.4,
        "priority": 2,
        "deadline_seconds": 2.5,
    },
    {
        "source_sensor_id": "IOT-BUS-06",
        "task_type": "telematics",
        "cpu_demand": 12.0,
        "data_size_mb": 2.1,
        "priority": 2,
        "deadline_seconds": None,
    },
    {
        "source_sensor_id": "IOT-EV-07",
        "task_type": "power_telemetry",
        "cpu_demand": 8.0,
        "data_size_mb": 0.32,
        "priority": 2,
        "deadline_seconds": 1.5,
    },
    {
        "source_sensor_id": "IOT-STR-08",
        "task_type": "aggregation",
        "cpu_demand": 5.0,
        "data_size_mb": 0.018,
        "priority": 1,
        "deadline_seconds": 3.0,
    },
    {
        "source_sensor_id": "IOT-LOG-09",
        "task_type": "telematics",
        "cpu_demand": 12.0,
        "data_size_mb": 1.8,
        "priority": 2,
        "deadline_seconds": None,
    },
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Task:
    task_id: str
    source_sensor_id: str
    task_type: str
    cpu_demand: float
    data_size_mb: float
    priority: int
    deadline_seconds: float | None
    arrival_time: str
    arrival_clock: float
    assigned_node_id: str | None = None
    estimated_network_latency_ms: float | None = None
    estimated_processing_time_ms: float | None = None
    estimated_response_time_ms: float | None = None
    actual_response_time_ms: float | None = None
    completion_time: str | None = None
    completion_clock: float | None = None
    status: str = "pending"
    allocation_explanation: str = "Waiting for scheduling."

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "source_sensor_id": self.source_sensor_id,
            "task_type": self.task_type,
            "cpu_demand": self.cpu_demand,
            "data_size_mb": self.data_size_mb,
            "priority": self.priority,
            "deadline_seconds": self.deadline_seconds,
            "arrival_time": self.arrival_time,
            "assigned_node_id": self.assigned_node_id,
            "estimated_network_latency_ms": self.estimated_network_latency_ms,
            "estimated_processing_time_ms": self.estimated_processing_time_ms,
            "estimated_response_time_ms": self.estimated_response_time_ms,
            "actual_response_time_ms": self.actual_response_time_ms,
            "completion_time": self.completion_time,
            "status": self.status,
            "allocation_explanation": self.allocation_explanation,
        }


@dataclass
class Node:
    node_id: str
    name: str
    node_type: str
    processing_capacity: float
    processing_rate: float
    network_latency_ms: float
    online: bool = True
    active_task_id: str | None = None
    active_task_started_at: float | None = None
    active_task_finishes_at: float | None = None
    queue: list[str] = field(default_factory=list)

    def workload(self, tasks: dict[str, Task]) -> float:
        task_ids = list(self.queue)
        if self.active_task_id is not None:
            task_ids.append(self.active_task_id)
        return sum(tasks[task_id].cpu_demand for task_id in task_ids)

    def current_workload(self, tasks: dict[str, Task]) -> float:
        if self.active_task_id is None:
            return 0.0
        return tasks[self.active_task_id].cpu_demand

    def projected_utilization(self, tasks: dict[str, Task]) -> float:
        return self.workload(tasks) / self.processing_capacity * 100.0

    def to_dict(self, tasks: dict[str, Task]) -> dict[str, Any]:
        return {
            "id": self.node_id,
            "name": self.name,
            "node_type": self.node_type,
            "processing_capacity": self.processing_capacity,
            "processing_rate": self.processing_rate,
            "network_latency_ms": self.network_latency_ms,
            "current_workload": self.current_workload(tasks),
            "projected_workload": self.workload(tasks),
            "projected_utilization": self.projected_utilization(tasks),
            "queue_length": len(self.queue),
            "online": self.online,
            "active_task_id": self.active_task_id,
        }


@dataclass(frozen=True)
class AllocationCandidate:
    node: Node
    projected_workload: float
    projected_utilization: float
    processing_time_ms: float
    response_time_ms: float
    completion_time_seconds: float
    deadline_urgency: float


def simulation_iso_time(epoch: datetime, clock_seconds: float) -> str:
    return datetime.fromtimestamp(
        epoch.timestamp() + clock_seconds, timezone.utc
    ).isoformat()
