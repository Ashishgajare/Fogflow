import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator


class SimulationDatabase:
    def __init__(self, database_path: str):
        self.database_path = database_path
        if database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS simulation_runs (
                    run_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    algorithm TEXT NOT NULL,
                    workload TEXT NOT NULL,
                    weights_json TEXT NOT NULL,
                    simulation_clock REAL NOT NULL DEFAULT 0,
                    started_at TEXT,
                    ended_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES simulation_runs(run_id),
                    status TEXT NOT NULL,
                    assigned_node_id TEXT,
                    arrival_clock REAL NOT NULL,
                    completion_clock REAL,
                    record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS tasks_run_arrival
                    ON tasks(run_id, arrival_clock DESC);
                CREATE INDEX IF NOT EXISTS tasks_run_status
                    ON tasks(run_id, status);
                CREATE TABLE IF NOT EXISTS allocation_decisions (
                    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES simulation_runs(run_id),
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    algorithm TEXT NOT NULL,
                    selected_node_id TEXT,
                    explanation TEXT NOT NULL,
                    candidates_json TEXT NOT NULL,
                    decision_clock REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS experiment_results (
                    experiment_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL UNIQUE REFERENCES simulation_runs(run_id),
                    algorithm TEXT NOT NULL,
                    workload TEXT NOT NULL,
                    task_count INTEGER NOT NULL,
                    completed_count INTEGER NOT NULL,
                    rejected_count INTEGER NOT NULL,
                    average_response_time_ms REAL,
                    results_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS benchmark_runs (
                    benchmark_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    duration_seconds INTEGER NOT NULL,
                    workload TEXT NOT NULL,
                    configuration_json TEXT NOT NULL,
                    workload_json TEXT NOT NULL,
                    current_algorithm TEXT,
                    completed_experiments INTEGER NOT NULL DEFAULT 0,
                    progress_percent REAL NOT NULL DEFAULT 0,
                    simulated_seconds REAL NOT NULL DEFAULT 0,
                    elapsed_seconds REAL NOT NULL DEFAULT 0,
                    results_json TEXT NOT NULL DEFAULT '[]',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS benchmark_runs_created
                    ON benchmark_runs(created_at DESC);
                """
            )

    def create_benchmark(
        self,
        benchmark_id: str,
        duration_seconds: int,
        workload: str,
        configuration: dict[str, Any],
        task_definitions: list[dict[str, Any]],
        created_at: str,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO benchmark_runs
                    (benchmark_id, status, duration_seconds, workload,
                     configuration_json, workload_json, created_at, updated_at)
                VALUES (?, 'running', ?, ?, ?, ?, ?, ?)
                """,
                (
                    benchmark_id,
                    duration_seconds,
                    workload,
                    json.dumps(configuration),
                    json.dumps(task_definitions),
                    created_at,
                    created_at,
                ),
            )

    def update_benchmark(
        self,
        benchmark_id: str,
        *,
        status: str,
        current_algorithm: str | None,
        completed_experiments: int,
        progress_percent: float,
        simulated_seconds: float,
        elapsed_seconds: float,
        results: list[dict[str, Any]],
        error: str | None,
        updated_at: str,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE benchmark_runs
                SET status = ?, current_algorithm = ?, completed_experiments = ?,
                    progress_percent = ?, simulated_seconds = ?, elapsed_seconds = ?,
                    results_json = ?, error = ?, updated_at = ?
                WHERE benchmark_id = ?
                """,
                (
                    status,
                    current_algorithm,
                    completed_experiments,
                    progress_percent,
                    simulated_seconds,
                    elapsed_seconds,
                    json.dumps(results),
                    error,
                    updated_at,
                    benchmark_id,
                ),
            )

    def get_benchmark(
        self,
        benchmark_id: str,
        *,
        include_workload: bool = False,
        include_results: bool = False,
    ) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM benchmark_runs WHERE benchmark_id = ?",
                (benchmark_id,),
            ).fetchone()
        return (
            self._benchmark_record(
                row,
                include_workload=include_workload,
                include_results=include_results,
            )
            if row is not None
            else None
        )

    def list_benchmarks(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM benchmark_runs
                ORDER BY created_at DESC, benchmark_id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._benchmark_record(row) for row in rows]

    def mark_interrupted_benchmarks(self, updated_at: str) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE benchmark_runs
                SET status = 'failed', current_algorithm = NULL,
                    error = 'Benchmark worker was interrupted by a server restart.',
                    updated_at = ?
                WHERE status = 'running'
                """,
                (updated_at,),
            )

    @staticmethod
    def _benchmark_record(
        row: sqlite3.Row,
        *,
        include_workload: bool = False,
        include_results: bool = False,
    ) -> dict[str, Any]:
        record = {
            "benchmark_id": row["benchmark_id"],
            "status": row["status"],
            "duration_seconds": row["duration_seconds"],
            "workload": row["workload"],
            "configuration": json.loads(row["configuration_json"]),
            "current_algorithm": row["current_algorithm"],
            "completed_experiments": row["completed_experiments"],
            "progress_percent": row["progress_percent"],
            "simulated_seconds": row["simulated_seconds"],
            "elapsed_seconds": row["elapsed_seconds"],
            "result_count": len(json.loads(row["results_json"])),
            "error": row["error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        if include_workload:
            record["workload_tasks"] = json.loads(row["workload_json"])
        if include_results:
            record["results"] = json.loads(row["results_json"])
        return record

    def create_run(
        self,
        run_id: str,
        algorithm: str,
        workload: str,
        weights: dict[str, float],
        created_at: str,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO simulation_runs
                    (run_id, state, algorithm, workload, weights_json, created_at)
                VALUES (?, 'idle', ?, ?, ?, ?)
                """,
                (run_id, algorithm, workload, json.dumps(weights), created_at),
            )

    def update_run(
        self,
        run_id: str,
        state: str,
        algorithm: str,
        workload: str,
        weights: dict[str, float],
        simulation_clock: float,
        started_at: str | None,
        ended_at: str | None = None,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE simulation_runs
                SET state = ?, algorithm = ?, workload = ?, weights_json = ?,
                    simulation_clock = ?, started_at = ?, ended_at = ?
                WHERE run_id = ?
                """,
                (
                    state,
                    algorithm,
                    workload,
                    json.dumps(weights),
                    simulation_clock,
                    started_at,
                    ended_at,
                    run_id,
                ),
            )

    def save_task(
        self,
        run_id: str,
        record: dict[str, Any],
        arrival_clock: float,
        completion_clock: float | None = None,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO tasks
                    (task_id, run_id, status, assigned_node_id, arrival_clock,
                     completion_clock, record_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status = excluded.status,
                    assigned_node_id = excluded.assigned_node_id,
                    completion_clock = excluded.completion_clock,
                    record_json = excluded.record_json
                """,
                (
                    record["task_id"],
                    run_id,
                    record["status"],
                    record["assigned_node_id"],
                    arrival_clock,
                    completion_clock,
                    json.dumps(record),
                ),
            )

    def save_decision(
        self,
        run_id: str,
        task_id: str,
        algorithm: str,
        selected_node_id: str | None,
        explanation: str,
        candidates: list[dict[str, Any]],
        decision_clock: float,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO allocation_decisions
                    (run_id, task_id, algorithm, selected_node_id, explanation,
                     candidates_json, decision_clock)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    task_id,
                    algorithm,
                    selected_node_id,
                    explanation,
                    json.dumps(candidates),
                    decision_clock,
                ),
            )

    def save_simulation_batch(
        self,
        run_id: str,
        tasks: list[tuple[dict[str, Any], float, float | None]],
        decisions: list[dict[str, Any]],
    ) -> None:
        with self.connection() as connection:
            connection.executemany(
                """
                INSERT INTO tasks
                    (task_id, run_id, status, assigned_node_id, arrival_clock,
                     completion_clock, record_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status = excluded.status,
                    assigned_node_id = excluded.assigned_node_id,
                    completion_clock = excluded.completion_clock,
                    record_json = excluded.record_json
                """,
                [
                    (
                        record["task_id"],
                        run_id,
                        record["status"],
                        record["assigned_node_id"],
                        arrival_clock,
                        completion_clock,
                        json.dumps(record),
                    )
                    for record, arrival_clock, completion_clock in tasks
                ],
            )
            connection.executemany(
                """
                INSERT INTO allocation_decisions
                    (run_id, task_id, algorithm, selected_node_id, explanation,
                     candidates_json, decision_clock)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        decision["task_id"],
                        decision["algorithm"],
                        decision["selected_node_id"],
                        decision["explanation"],
                        json.dumps(decision["candidates"]),
                        decision["decision_clock"],
                    )
                    for decision in decisions
                ],
            )

    def list_tasks(
        self,
        run_id: str,
        limit: int,
        offset: int,
        status: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        condition = "run_id = ?"
        parameters: list[Any] = [run_id]
        if status is not None:
            condition += " AND status = ?"
            parameters.append(status)
        with self.connection() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM tasks WHERE {condition}", parameters
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT record_json FROM tasks WHERE {condition}
                ORDER BY arrival_clock DESC, task_id DESC LIMIT ? OFFSET ?
                """,
                [*parameters, limit, offset],
            ).fetchall()
        return [json.loads(row["record_json"]) for row in rows], int(total)

    def finish_experiment(
        self,
        run_id: str,
        algorithm: str,
        workload: str,
        tasks: list[dict[str, Any]],
        created_at: str,
    ) -> None:
        completed = [task for task in tasks if task["status"] == "completed"]
        failed_count = sum(task["status"] == "failed" for task in tasks)
        rejected_count = sum(task["status"] == "rejected" for task in tasks)
        response_times = [
            task["actual_response_time_ms"]
            for task in completed
            if task.get("actual_response_time_ms") is not None
        ]
        average_response = (
            sum(response_times) / len(response_times) if response_times else None
        )
        result = {
            "task_count": len(tasks),
            "completed_count": len(completed),
            "failed_count": failed_count,
            "rejected_count": rejected_count,
            "average_response_time_ms": average_response,
        }
        with self.connection() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO experiment_results
                    (run_id, algorithm, workload, task_count, completed_count,
                     rejected_count, average_response_time_ms, results_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    algorithm,
                    workload,
                    len(tasks),
                    len(completed),
                    rejected_count,
                    average_response,
                    json.dumps(result),
                    created_at,
                ),
            )
