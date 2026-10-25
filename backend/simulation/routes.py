from typing import Any

from flask import Blueprint, current_app, jsonify, request
from werkzeug.exceptions import BadRequest, UnsupportedMediaType

from simulation.benchmark import BenchmarkManager
from simulation.engine import SimulationEngine, SimulationError, _number
from simulation.models import WORKLOAD_NAMES
from simulation.scheduler import ALGORITHMS, SchedulingError


api = Blueprint("simulation_api", __name__)
VALID_TASK_STATUSES = {
    "pending",
    "queued",
    "running",
    "completed",
    "failed",
    "rejected",
}
TASK_FIELDS = {
    "source_sensor_id",
    "task_type",
    "cpu_demand",
    "data_size_mb",
    "priority",
    "deadline_seconds",
}


def _engine() -> SimulationEngine:
    return current_app.extensions["fogflow_engine"]


def _benchmarks() -> BenchmarkManager:
    return current_app.extensions["fogflow_benchmarks"]


def _json_object() -> dict[str, Any]:
    if not request.data:
        return {}
    try:
        body = request.get_json()
    except (BadRequest, UnsupportedMediaType) as exc:
        raise SimulationError("Request body must be valid JSON.") from exc
    if not isinstance(body, dict):
        raise SimulationError("Request body must be a JSON object.")
    return body


def _validate_task_specification(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SimulationError("Every task must be a JSON object.")
    unknown = set(value) - TASK_FIELDS
    if unknown:
        raise SimulationError(f"Unknown task fields: {', '.join(sorted(unknown))}.")
    for field in ("source_sensor_id", "task_type"):
        content = value.get(field)
        if not isinstance(content, str) or not content.strip():
            raise SimulationError(f"{field} must be a non-empty string.")

    cpu_demand = _number(
        value.get("cpu_demand"), "cpu_demand", minimum=0, strict=True
    )
    data_size_mb = _number(value.get("data_size_mb"), "data_size_mb", minimum=0)
    priority = value.get("priority", 1)
    if isinstance(priority, bool) or not isinstance(priority, int) or not 1 <= priority <= 10:
        raise SimulationError("priority must be an integer between 1 and 10.")

    deadline = value.get("deadline_seconds")
    if deadline is not None:
        deadline = _number(deadline, "deadline_seconds", minimum=0, strict=True)

    return {
        "source_sensor_id": value["source_sensor_id"].strip(),
        "task_type": value["task_type"].strip(),
        "cpu_demand": cpu_demand,
        "data_size_mb": data_size_mb,
        "priority": priority,
        "deadline_seconds": deadline,
    }


def _error_response(error: SimulationError):
    return jsonify(error={"message": str(error)}), error.status_code


@api.errorhandler(SimulationError)
def handle_simulation_error(error: SimulationError):
    return _error_response(error)


@api.get("/nodes")
def get_nodes():
    return jsonify(nodes=_engine().nodes_snapshot())


@api.post("/nodes/configure")
def configure_nodes():
    body = _json_object()
    if set(body) != {"fog_node_count"}:
        raise SimulationError("Only fog_node_count is accepted.")
    fog_node_count = body["fog_node_count"]
    if isinstance(fog_node_count, bool) or not isinstance(fog_node_count, int):
        raise SimulationError("fog_node_count must be an integer.")
    nodes = _engine().configure_fog_node_count(fog_node_count)
    _benchmarks().set_node_configs(_engine().node_configs_snapshot())
    return jsonify(nodes=nodes)


@api.get("/tasks")
def get_tasks():
    limit_value = request.args.get("limit", "50")
    offset_value = request.args.get("offset", "0")
    status = request.args.get("status")
    try:
        limit = int(limit_value)
        offset = int(offset_value)
    except ValueError as exc:
        raise SimulationError("limit and offset must be integers.") from exc
    if not 1 <= limit <= 500:
        raise SimulationError("limit must be between 1 and 500.")
    if offset < 0:
        raise SimulationError("offset must be zero or greater.")
    if status is not None and status not in VALID_TASK_STATUSES:
        raise SimulationError(
            f"status must be one of: {', '.join(sorted(VALID_TASK_STATUSES))}."
        )
    return jsonify(_engine().tasks_snapshot(limit, offset, status))


@api.get("/metrics")
def get_metrics():
    return jsonify(_engine().metrics_snapshot())


@api.post("/benchmarks")
def start_benchmark():
    body = _json_object()
    if set(body) - {"duration_seconds", "workload", "manual_tasks_per_minute"}:
        raise SimulationError(
            "Only duration_seconds, workload, and manual_tasks_per_minute are accepted."
        )
    duration = body.get("duration_seconds", 60)
    workload = body.get("workload", "normal")
    manual_tasks_per_minute = body.get("manual_tasks_per_minute")
    if isinstance(duration, bool) or not isinstance(duration, int):
        raise SimulationError("duration_seconds must be an integer.")
    if not isinstance(workload, str) or workload not in WORKLOAD_NAMES:
        raise SimulationError("workload must be light, normal, heavy, extreme, or manual.")
    benchmark = _benchmarks().start(
        duration, workload, manual_tasks_per_minute
    )
    return jsonify(benchmark=benchmark), 202


@api.get("/benchmarks")
def list_benchmarks():
    return jsonify(benchmarks=_benchmarks().list())


@api.get("/benchmarks/<benchmark_id>")
def get_benchmark(benchmark_id: str):
    return jsonify(benchmark=_benchmarks().get(benchmark_id))


@api.get("/benchmarks/<benchmark_id>/results")
def get_benchmark_results(benchmark_id: str):
    return jsonify(_benchmarks().results(benchmark_id))


@api.post("/benchmarks/<benchmark_id>/cancel")
def cancel_benchmark(benchmark_id: str):
    if _json_object():
        raise SimulationError("Cancel does not accept request fields.")
    return jsonify(benchmark=_benchmarks().cancel(benchmark_id))


@api.post("/tasks/generate")
def generate_tasks():
    body = _json_object()
    if set(body) - {"count", "tasks"}:
        raise SimulationError("Only count and tasks are accepted.")
    if "tasks" in body:
        if "count" in body:
            raise SimulationError("Provide either count or tasks, not both.")
        task_values = body["tasks"]
        if not isinstance(task_values, list) or not 1 <= len(task_values) <= 1000:
            raise SimulationError("tasks must be a list containing 1 to 1000 tasks.")
        specifications = [_validate_task_specification(task) for task in task_values]
    else:
        count = body.get("count", 1)
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 1000:
            raise SimulationError("count must be an integer between 1 and 1000.")
        specifications = [
            _validate_task_specification(template)
            for template in _engine().template_specs(count)
        ]

    created = _engine().generate_tasks(specifications)
    return jsonify(run_id=_engine().run_id, generated=len(created), tasks=created), 201


@api.post("/simulation/start")
def start_simulation():
    body = _json_object()
    if set(body) - {
        "workload",
        "algorithm",
        "weights",
        "manual_tasks_per_minute",
    }:
        raise SimulationError(
            "Only workload, algorithm, weights, and manual_tasks_per_minute are accepted."
        )
    workload = body.get("workload", "normal")
    algorithm = body.get("algorithm", "adaptive_fog_cloud")
    weights = body.get("weights")
    manual_tasks_per_minute = body.get("manual_tasks_per_minute")
    if not isinstance(workload, str) or workload not in WORKLOAD_NAMES:
        raise SimulationError("workload must be light, normal, heavy, extreme, or manual.")
    if not isinstance(algorithm, str) or algorithm not in ALGORITHMS:
        raise SimulationError(
            "algorithm must be one of the supported Group 1 or Group 2 scheduler IDs."
        )
    if weights is not None and not isinstance(weights, dict):
        raise SimulationError("weights must be a JSON object.")
    try:
        result = _engine().start(
            workload, algorithm, weights, manual_tasks_per_minute
        )
    except SchedulingError as exc:
        raise SimulationError(str(exc)) from exc
    return jsonify(simulation=result)


@api.post("/simulation/pause")
def pause_simulation():
    if _json_object():
        raise SimulationError("Pause does not accept request fields.")
    return jsonify(simulation=_engine().pause())


@api.post("/simulation/resume")
def resume_simulation():
    if _json_object():
        raise SimulationError("Resume does not accept request fields.")
    return jsonify(simulation=_engine().resume())


@api.post("/simulation/reset")
def reset_simulation():
    if _json_object():
        raise SimulationError("Reset does not accept request fields.")
    return jsonify(simulation=_engine().reset())
