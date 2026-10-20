import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app import create_app
from simulation.benchmark import (
    BENCHMARK_ALGORITHMS,
    BenchmarkCancelled,
    BenchmarkManager,
    prepare_workload,
    run_experiment,
)
from simulation.database import SimulationDatabase
from simulation.engine import SimulationEngine, SimulationError
from simulation.models import DEFAULT_ADAPTIVE_WEIGHTS, DEFAULT_NODE_CONFIGS


class BenchmarkEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = SimulationDatabase(
            str(Path(self.temp_dir.name) / "benchmark.sqlite3")
        )
        self.epoch = datetime(2025, 1, 1, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_workload_is_reproducible_and_uses_one_shared_arrival_schedule(self):
        first = prepare_workload(30, "normal", epoch=self.epoch)
        second = prepare_workload(30, "normal", epoch=self.epoch)

        self.assertEqual(first, second)
        self.assertEqual(len(first), 60)
        self.assertEqual(
            [task["arrival_clock"] for task in first[:4]],
            [0.5, 1.0, 1.5, 2.0],
        )
        self.assertEqual(first[0]["task_id"], "benchmark-task-000001")

    def test_zero_duration_is_rejected_before_throughput_calculation(self):
        with self.assertRaises(SimulationError):
            run_experiment(
                "round_robin",
                [],
                list(DEFAULT_NODE_CONFIGS),
                DEFAULT_ADAPTIVE_WEIGHTS,
                0,
            )

    def test_each_algorithm_gets_fresh_node_state_and_live_simulation_is_preserved(self):
        engine = SimulationEngine(self.database, background_simulation=False)
        engine.start(workload="light", algorithm="round_robin")
        engine.generate_tasks(
            [
                {
                    "source_sensor_id": "IOT-CAM-01",
                    "task_type": "test",
                    "cpu_demand": 8.0,
                    "data_size_mb": 1.0,
                    "priority": 1,
                    "deadline_seconds": None,
                }
            ]
        )
        engine.pause()
        before = engine.metrics_snapshot()
        definitions = prepare_workload(30, "light", epoch=self.epoch)
        configs = [dict(config) for config in DEFAULT_NODE_CONFIGS]

        results = [
            run_experiment(
                algorithm,
                definitions,
                configs,
                DEFAULT_ADAPTIVE_WEIGHTS,
                30,
            )
            for algorithm in BENCHMARK_ALGORITHMS
        ]

        self.assertEqual([result["task_count"] for result in results], [15] * 5)
        self.assertEqual(engine.metrics_snapshot(), before)
        self.assertEqual(configs, list(DEFAULT_NODE_CONFIGS))
        self.assertEqual(
            [result["algorithm"] for result in results],
            list(BENCHMARK_ALGORITHMS),
        )

    def test_min_min_schedules_batches_using_estimated_completion_times(self):
        definitions = prepare_workload(30, "heavy", epoch=self.epoch)
        result = run_experiment(
            "min_min",
            definitions,
            list(DEFAULT_NODE_CONFIGS),
            DEFAULT_ADAPTIVE_WEIGHTS,
            30,
        )
        allocated = [
            task
            for task in result["task_outcomes"]
            if task["assigned_node_id"] is not None
        ]

        self.assertGreater(len(allocated), 1)
        self.assertTrue(
            all(
                "Min-Min batch scheduling" in task["allocation_explanation"]
                for task in allocated
            )
        )
        self.assertIn("smallest estimated completion time", allocated[0]["allocation_explanation"])

    def test_metric_definitions_and_empty_workload_are_safe(self):
        one_task = [
            {
                "task_id": "benchmark-task-000001",
                "source_sensor_id": "sensor-1",
                "task_type": "test",
                "cpu_demand": 10.0,
                "data_size_mb": 1.0,
                "priority": 1,
                "deadline_seconds": None,
                "arrival_clock": 0.0,
                "arrival_time": "2025-01-01T00:00:00+00:00",
            }
        ]
        result = run_experiment(
            "least_loaded",
            one_task,
            list(DEFAULT_NODE_CONFIGS),
            DEFAULT_ADAPTIVE_WEIGHTS,
            30,
        )
        empty = run_experiment(
            "round_robin",
            [],
            list(DEFAULT_NODE_CONFIGS),
            DEFAULT_ADAPTIVE_WEIGHTS,
            30,
        )

        self.assertEqual(result["completed_count"], 1)
        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(result["throughput_tasks_per_second"], 1 / 30)
        self.assertAlmostEqual(
            result["average_response_time_ms"],
            result["median_response_time_ms"],
        )
        self.assertEqual(result["cloud_offloading_rate_percent"], 100.0)
        self.assertIsNotNone(result["makespan_seconds"])
        self.assertEqual(empty["task_count"], 0)
        self.assertIsNone(empty["average_response_time_ms"])
        self.assertIsNone(empty["median_response_time_ms"])
        self.assertIsNone(empty["average_node_utilization_percent"])
        self.assertIsNone(empty["load_imbalance_percent"])
        self.assertIsNone(empty["cloud_offloading_rate_percent"])
        self.assertEqual(empty["throughput_tasks_per_second"], 0)
        self.assertIsNone(empty["makespan_seconds"])

    def test_benchmark_capacity_availability_and_deadlines_are_enforced(self):
        task = {
            "task_id": "benchmark-task-000001",
            "source_sensor_id": "sensor-1",
            "task_type": "test",
            "cpu_demand": 10.0,
            "data_size_mb": 1.0,
            "priority": 1,
            "deadline_seconds": None,
            "arrival_clock": 0.0,
            "arrival_time": "2025-01-01T00:00:00+00:00",
        }
        cloud_only = [
            dict(config, online=config["node_type"] == "cloud")
            for config in DEFAULT_NODE_CONFIGS
        ]
        no_capacity = run_experiment(
            "least_loaded",
            [{**task, "cpu_demand": 501.0}],
            list(DEFAULT_NODE_CONFIGS),
            DEFAULT_ADAPTIVE_WEIGHTS,
            30,
        )
        offline = run_experiment(
            "round_robin",
            [task],
            [dict(config, online=False) for config in DEFAULT_NODE_CONFIGS],
            DEFAULT_ADAPTIVE_WEIGHTS,
            30,
        )
        online_cloud = run_experiment(
            "round_robin",
            [task],
            cloud_only,
            DEFAULT_ADAPTIVE_WEIGHTS,
            30,
        )
        deadline = run_experiment(
            "least_loaded",
            [{**task, "deadline_seconds": 0.001}],
            list(DEFAULT_NODE_CONFIGS),
            DEFAULT_ADAPTIVE_WEIGHTS,
            30,
        )

        self.assertEqual(no_capacity["failed_count"], 1)
        self.assertEqual(no_capacity["completed_count"], 0)
        self.assertEqual(offline["failed_count"], 1)
        self.assertEqual(online_cloud["completed_count"], 1)
        self.assertEqual(online_cloud["task_outcomes"][0]["assigned_node_id"], "cloud-1")
        self.assertEqual(deadline["failed_count"], 1)
        self.assertEqual(deadline["deadline_misses"], 1)

    def _wait_for_status(self, manager, benchmark_id, expected, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            benchmark = manager.get(benchmark_id)
            if benchmark["status"] == expected:
                return benchmark
            time.sleep(0.01)
        self.fail(f"Benchmark did not reach status {expected!r}.")

    def test_benchmark_results_and_configuration_are_persisted(self):
        manager = BenchmarkManager(self.database)
        benchmark = manager.start(30, "light")
        finished = self._wait_for_status(
            manager, benchmark["benchmark_id"], "completed"
        )
        saved = manager.get(benchmark["benchmark_id"])

        self.assertEqual(finished["completed_experiments"], 5)
        self.assertEqual(finished["result_count"], 5)
        self.assertEqual(
            saved["configuration"]["algorithm_order"],
            list(BENCHMARK_ALGORITHMS),
        )
        persisted = self.database.get_benchmark(
            benchmark["benchmark_id"], include_workload=True
        )
        self.assertEqual(len(persisted["workload_tasks"]), 15)
        self.assertEqual(len(manager.list()), 1)

    def test_failed_experiment_is_not_reported_as_a_complete_comparison(self):
        def failing_runner(algorithm, *args, **kwargs):
            if algorithm == "least_loaded":
                raise RuntimeError("injected scheduler failure")
            return run_experiment(algorithm, *args, **kwargs)

        manager = BenchmarkManager(self.database, runner=failing_runner)
        benchmark = manager.start(30, "light")
        failed = self._wait_for_status(manager, benchmark["benchmark_id"], "failed")

        self.assertEqual(failed["completed_experiments"], 2)
        self.assertEqual(failed["result_count"], 2)
        self.assertIn("injected scheduler failure", failed["error"])
        self.assertNotEqual(failed["status"], "completed")

    def test_duplicate_start_and_cancellation_are_safe(self):
        entered_runner = threading.Event()

        def cancellable_runner(
            *args,
            cancellation,
            **kwargs,
        ):
            self.assertEqual(len(args), 5)
            self.assertIn("progress", kwargs)
            entered_runner.set()
            while not cancellation.wait(0.005):
                pass
            raise BenchmarkCancelled("Benchmark cancellation was requested.")

        manager = BenchmarkManager(self.database, runner=cancellable_runner)
        benchmark = manager.start(30, "light")
        self.assertTrue(entered_runner.wait(2.0))
        with self.assertRaises(SimulationError) as duplicate:
            manager.start(30, "light")
        self.assertEqual(duplicate.exception.status_code, 409)

        manager.cancel(benchmark["benchmark_id"])
        cancelled = self._wait_for_status(
            manager, benchmark["benchmark_id"], "cancelled"
        )
        self.assertEqual(cancelled["completed_experiments"], 0)


class BenchmarkApiTests(unittest.TestCase):
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

    def _wait_for_status(self, benchmark_id, expected, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/benchmarks/{benchmark_id}")
            self.assertEqual(response.status_code, 200)
            benchmark = response.get_json()["benchmark"]
            if benchmark["status"] == expected:
                return benchmark
            time.sleep(0.01)
        self.fail(f"Benchmark did not reach status {expected!r}.")

    def test_benchmark_api_validates_configuration_and_persists_results(self):
        invalid = self.client.post(
            "/api/benchmarks", json={"duration_seconds": 45}
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("error", invalid.get_json())

        started = self.client.post(
            "/api/benchmarks",
            json={"duration_seconds": 30, "workload": "light"},
        )
        self.assertEqual(started.status_code, 202)
        benchmark_id = started.get_json()["benchmark"]["benchmark_id"]
        finished = self._wait_for_status(benchmark_id, "completed")
        self.assertEqual(finished["completed_experiments"], 5)

        results = self.client.get(f"/api/benchmarks/{benchmark_id}/results")
        self.assertEqual(results.status_code, 200)
        result_body = results.get_json()
        self.assertEqual(len(result_body["results"]), 5)
        self.assertEqual(
            [result["algorithm"] for result in result_body["results"]],
            list(BENCHMARK_ALGORITHMS),
        )
        history = self.client.get("/api/benchmarks")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(len(history.get_json()["benchmarks"]), 1)

    def test_results_require_a_completed_benchmark_and_missing_id_is_not_found(self):
        not_found = self.client.get("/api/benchmarks/missing")
        self.assertEqual(not_found.status_code, 404)
        missing_results = self.client.get("/api/benchmarks/missing/results")
        self.assertEqual(missing_results.status_code, 404)

    def test_api_cancel_and_duplicate_submission_are_reported_safely(self):
        entered_runner = threading.Event()

        def cancellable_runner(
            *args,
            cancellation,
            **kwargs,
        ):
            self.assertEqual(len(args), 5)
            self.assertIn("progress", kwargs)
            entered_runner.set()
            while not cancellation.wait(0.005):
                pass
            raise BenchmarkCancelled("Benchmark cancellation was requested.")

        manager = self.app.extensions["fogflow_benchmarks"]
        manager.runner = cancellable_runner
        started = self.client.post(
            "/api/benchmarks",
            json={"duration_seconds": 30, "workload": "light"},
        )
        self.assertEqual(started.status_code, 202)
        benchmark_id = started.get_json()["benchmark"]["benchmark_id"]
        self.assertTrue(entered_runner.wait(2.0))

        duplicate = self.client.post(
            "/api/benchmarks",
            json={"duration_seconds": 30, "workload": "light"},
        )
        self.assertEqual(duplicate.status_code, 409)

        cancelled = self.client.post(f"/api/benchmarks/{benchmark_id}/cancel")
        self.assertEqual(cancelled.status_code, 200)
        finished = self._wait_for_status(benchmark_id, "cancelled")
        self.assertEqual(finished["completed_experiments"], 0)
        self.assertEqual(
            self.client.get(f"/api/benchmarks/{benchmark_id}/results").status_code,
            409,
        )
