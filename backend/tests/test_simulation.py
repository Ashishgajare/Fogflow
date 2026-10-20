import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import create_app
from simulation.database import SimulationDatabase
from simulation.engine import SimulationEngine
from simulation.models import DEFAULT_NODE_CONFIGS, Task
from simulation.scheduler import (
    SchedulingError,
    choose_candidate,
    eligible_candidates,
    validate_weights,
)


TASK = {
    "source_sensor_id": "sensor-test-01",
    "task_type": "test_inference",
    "cpu_demand": 10.0,
    "data_size_mb": 1.25,
    "priority": 2,
    "deadline_seconds": None,
}


class SimulationEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database = SimulationDatabase(
            str(Path(self.temp_dir.name) / "simulation.sqlite3")
        )
        self.engine = SimulationEngine(
            database,
            background_simulation=False,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_round_robin_cycles_across_eligible_nodes(self):
        self.engine.start(workload="light", algorithm="round_robin")
        tasks = self.engine.generate_tasks([TASK, TASK, TASK])
        self.assertEqual(
            [task["assigned_node_id"] for task in tasks],
            ["fog-1", "fog-2", "fog-3"],
        )
        self.assertIn("Round Robin", tasks[0]["allocation_explanation"])

    def test_weighted_round_robin_distributes_by_processing_capacity(self):
        task = Task(
            task_id="weighted-test",
            source_sensor_id="sensor-test-01",
            task_type="test",
            cpu_demand=1.0,
            data_size_mb=1.0,
            priority=1,
            deadline_seconds=None,
            arrival_time="2025-01-01T00:00:00+00:00",
            arrival_clock=0.0,
        )
        candidates, excluded = eligible_candidates(
            self.engine.nodes, {}, task, 0.0
        )
        self.assertEqual(excluded, {})

        allocations = {node.node_id: 0 for node in self.engine.nodes}
        for offset in range(40):
            selected, explanation = choose_candidate(
                "weighted_round_robin",
                candidates,
                offset,
                self.engine.weights,
            )
            allocations[selected.node.node_id] += 1
            self.assertIn("capacity weights", explanation)

        self.assertEqual(
            allocations,
            {"fog-1": 5, "fog-2": 6, "fog-3": 4, "cloud-1": 25},
        )

    def test_weighted_round_robin_is_supported_by_live_simulation_api(self):
        self.engine.start(workload="light", algorithm="weighted_round_robin")
        task = self.engine.generate_tasks([TASK])[0]

        self.assertIn("Weighted Round Robin", task["allocation_explanation"])

    def test_least_loaded_uses_projected_capacity_ratio(self):
        self.engine.start(workload="light", algorithm="least_loaded")
        task = self.engine.generate_tasks([TASK])[0]
        self.assertEqual(task["assigned_node_id"], "cloud-1")
        cloud = next(
            node for node in self.engine.nodes_snapshot() if node["id"] == "cloud-1"
        )
        self.assertAlmostEqual(cloud["projected_utilization"], 2.0)

    def test_adaptive_scheduler_scores_response_utilization_and_completion(self):
        self.engine.start(workload="light", algorithm="adaptive_fog_cloud")
        task = self.engine.generate_tasks([TASK])[0]
        self.assertEqual(task["assigned_node_id"], "fog-2")
        self.assertIn("normalized score", task["allocation_explanation"])

    def test_rejects_task_that_exceeds_every_node_capacity(self):
        self.engine.start(workload="light", algorithm="least_loaded")
        too_large = {**TASK, "cpu_demand": 501.0}
        task = self.engine.generate_tasks([too_large])[0]
        self.assertEqual(task["status"], "rejected")
        self.assertIsNone(task["assigned_node_id"])
        self.assertIn("capacity", task["allocation_explanation"])

    def test_rejects_task_that_cannot_meet_its_deadline(self):
        self.engine.start(workload="light", algorithm="adaptive_fog_cloud")
        task = self.engine.generate_tasks(
            [{**TASK, "deadline_seconds": 0.00001}]
        )[0]
        self.assertEqual(task["status"], "rejected")
        self.assertIn("deadline missed", task["allocation_explanation"])

    def test_offline_nodes_are_not_eligible(self):
        configs = [dict(config, online=False) for config in DEFAULT_NODE_CONFIGS]
        configs[-1]["online"] = True
        database = SimulationDatabase(
            str(Path(self.temp_dir.name) / "offline.sqlite3")
        )
        engine = SimulationEngine(
            database,
            node_configs=configs,
            background_simulation=False,
        )
        engine.start(workload="light", algorithm="round_robin")
        task = engine.generate_tasks([TASK])[0]
        self.assertEqual(task["assigned_node_id"], "cloud-1")
        self.assertIn("node is offline", task["allocation_explanation"])

    def test_queued_tasks_complete_in_fifo_order(self):
        configs = [
            dict(config, online=config["id"] == "fog-1")
            for config in DEFAULT_NODE_CONFIGS
        ]
        database = SimulationDatabase(
            str(Path(self.temp_dir.name) / "queue.sqlite3")
        )
        engine = SimulationEngine(
            database,
            node_configs=configs,
            background_simulation=False,
        )
        engine.start(workload="light", algorithm="round_robin")
        tasks = engine.generate_tasks([TASK, {**TASK, "priority": 1}])
        self.assertEqual(tasks[0]["status"], "running")
        self.assertEqual(tasks[1]["status"], "queued")
        nodes = engine.nodes_snapshot()
        fog = next(node for node in nodes if node["id"] == "fog-1")
        self.assertEqual(fog["queue_length"], 1)

        engine.advance(1.0)
        statuses = [task["status"] for task in engine.tasks_snapshot(10, 0)["tasks"]]
        self.assertEqual(statuses.count("completed"), 2)
        self.assertEqual(engine.metrics_snapshot()["tasks_completed"], 2)

    def test_reset_clears_live_state_and_persists_experiment(self):
        self.engine.start(workload="light", algorithm="least_loaded")
        self.engine.generate_tasks([TASK])
        old_run_id = self.engine.run_id
        reset = self.engine.reset()
        self.assertNotEqual(reset["run_id"], old_run_id)
        self.assertEqual(reset["state"], "idle")
        self.assertEqual(self.engine.metrics_snapshot()["task_count"], 0)

        connection = sqlite3.connect(self.engine.database.database_path)
        try:
            run_state = connection.execute(
                "SELECT state FROM simulation_runs WHERE run_id = ?", (old_run_id,)
            ).fetchone()[0]
            experiment_count = connection.execute(
                "SELECT COUNT(*) FROM experiment_results WHERE run_id = ?",
                (old_run_id,),
            ).fetchone()[0]
            decision_count = connection.execute(
                "SELECT COUNT(*) FROM allocation_decisions WHERE run_id = ?",
                (old_run_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(run_state, "reset")
        self.assertEqual(experiment_count, 1)
        self.assertEqual(decision_count, 1)

    def test_workload_profile_generates_and_completes_deterministically(self):
        self.engine.start(workload="light", algorithm="least_loaded")
        self.engine.advance(3.5)
        metrics = self.engine.metrics_snapshot()
        self.assertGreaterEqual(metrics["task_count"], 1)
        self.assertGreaterEqual(metrics["tasks_completed"], 1)
        self.assertIsNotNone(metrics["average_response_time_ms"])
        self.assertTrue(
            set(metrics["task_counts_by_sensor"]).issubset(
                {
                    "IOT-CAM-01",
                    "IOT-MED-02",
                    "IOT-GRD-03",
                    "IOT-AIR-04",
                    "IOT-FLW-05",
                    "IOT-BUS-06",
                    "IOT-EV-07",
                    "IOT-STR-08",
                    "IOT-LOG-09",
                }
            )
        )

    def test_automatic_workload_cycles_through_all_mapped_city_sensors(self):
        self.engine.start(workload="light", algorithm="round_robin")
        self.engine.advance(18.0)

        self.assertEqual(
            self.engine.metrics_snapshot()["task_counts_by_sensor"],
            {
                "IOT-CAM-01": 1,
                "IOT-MED-02": 1,
                "IOT-GRD-03": 1,
                "IOT-AIR-04": 1,
                "IOT-FLW-05": 1,
                "IOT-BUS-06": 1,
                "IOT-EV-07": 1,
                "IOT-STR-08": 1,
                "IOT-LOG-09": 1,
            },
        )

    def test_adaptive_weights_must_sum_to_one(self):
        with self.assertRaises(SchedulingError):
            validate_weights(
                {"response_time": 0.5, "utilization": 0.5, "completion_time": 0.5}
            )


class SimulationApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE": str(Path(self.temp_dir.name) / "api.sqlite3"),
                "BACKGROUND_SIMULATION": False,
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_health_endpoint_and_cors_are_preserved(self):
        response = self.client.get(
            "/api/health", headers={"Origin": "http://localhost:5173"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})
        self.assertEqual(
            response.headers["Access-Control-Allow-Origin"],
            "http://localhost:5173",
        )

    def test_nodes_tasks_and_metrics_endpoints_return_json(self):
        self.assertEqual(self.client.get("/api/nodes").status_code, 200)
        self.assertEqual(self.client.get("/api/tasks").get_json()["total"], 0)
        metrics = self.client.get("/api/metrics").get_json()
        self.assertEqual(metrics["state"], "idle")
        self.assertIsNone(metrics["average_response_time_ms"])
        self.assertEqual(metrics["task_counts_by_sensor"], {})

    def test_metrics_aggregate_real_task_counts_by_sensor_and_reset_them(self):
        tasks = [
            {**TASK, "source_sensor_id": "IOT-CAM-01"},
            {**TASK, "source_sensor_id": "IOT-CAM-01"},
            {**TASK, "source_sensor_id": "IOT-MED-02"},
        ]
        self.client.post("/api/tasks/generate", json={"tasks": tasks})

        metrics = self.client.get("/api/metrics").get_json()
        self.assertEqual(
            metrics["task_counts_by_sensor"],
            {"IOT-CAM-01": 2, "IOT-MED-02": 1},
        )

        self.client.post("/api/simulation/reset")
        reset_metrics = self.client.get("/api/metrics").get_json()
        self.assertEqual(reset_metrics["task_counts_by_sensor"], {})

    def test_generate_tasks_and_simulation_controls(self):
        generated = self.client.post(
            "/api/tasks/generate", json={"tasks": [TASK]}
        )
        self.assertEqual(generated.status_code, 201)
        self.assertEqual(generated.get_json()["generated"], 1)

        started = self.client.post(
            "/api/simulation/start",
            json={"workload": "light", "algorithm": "round_robin"},
        )
        self.assertEqual(started.status_code, 200)
        self.assertEqual(
            self.client.post("/api/simulation/pause").get_json()["simulation"]["state"],
            "paused",
        )
        self.assertEqual(
            self.client.post("/api/simulation/resume").get_json()["simulation"]["state"],
            "running",
        )
        reset = self.client.post("/api/simulation/reset")
        self.assertEqual(reset.status_code, 200)
        self.assertEqual(reset.get_json()["simulation"]["state"], "idle")

    def test_sensor_task_request_returns_actual_allocation_for_scene_route(self):
        self.client.post(
            "/api/simulation/start",
            json={"workload": "light", "algorithm": "round_robin"},
        )
        sensor_task = {
            **TASK,
            "source_sensor_id": "IOT-CAM-01",
            "task_type": "computer_vision",
            "data_size_mb": 12.4,
        }

        response = self.client.post(
            "/api/tasks/generate", json={"tasks": [sensor_task]}
        )

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload["generated"], 1)
        task = payload["tasks"][0]
        self.assertEqual(task["source_sensor_id"], "IOT-CAM-01")
        self.assertIsInstance(task["task_id"], str)
        self.assertEqual(task["assigned_node_id"], "fog-1")
        self.assertIn("Round Robin", task["allocation_explanation"])
        self.assertNotEqual(task["status"], "rejected")

    def test_invalid_requests_return_validated_json_errors(self):
        invalid_task = self.client.post(
            "/api/tasks/generate",
            json={"tasks": [{**TASK, "cpu_demand": -1}]},
        )
        self.assertEqual(invalid_task.status_code, 400)
        self.assertIn("error", invalid_task.get_json())

        invalid_weights = self.client.post(
            "/api/simulation/start",
            json={
                "weights": {
                    "response_time": 0.4,
                    "utilization": 0.4,
                    "completion_time": 0.4,
                }
            },
        )
        self.assertEqual(invalid_weights.status_code, 400)
        self.assertIn("sum to 1", invalid_weights.get_json()["error"]["message"])

        invalid_query = self.client.get("/api/tasks?limit=not-an-int")
        self.assertEqual(invalid_query.status_code, 400)
        self.assertTrue(json.loads(invalid_query.data)["error"]["message"])

    def test_invalid_state_transition_returns_conflict(self):
        response = self.client.post("/api/simulation/pause")
        self.assertEqual(response.status_code, 409)
        self.assertIn("error", response.get_json())


if __name__ == "__main__":
    unittest.main()
