# FogFlow

FogFlow is a Semester V project on intelligent load balancing in fog-cloud computing. It contains a separate React/TypeScript frontend and Flask backend, with a React implementation of the exported Stitch dashboard design.

The dashboard is connected to a fog/cloud simulation. Its metrics, node utilization, and task allocations are derived from generated or submitted tasks and configured node capacities, not placeholder values. Live runs also model independently seeded transient node outages; these are simulation assumptions, not calibrated Mumbai infrastructure reliability data. The Smart City Digital Twin uses the MapCN MapLibre map centered on Mumbai, with fictional demonstration locations for nine IoT sensors and one to 75 configurable fog nodes plus a cloud server. Starting the simulation automatically generates workload tasks; the dashboard also supports manual task creation and sensor-specific submissions. Light, normal, heavy, and extreme workloads generate 60, 240, 600, and 1,200 tasks per simulated minute respectively; the Manual workload accepts a custom rate from 1 to 10,000 tasks per minute. Sensor heat intensity comes from real current-run task counts returned by the backend, and allocation routes use the actual scheduler destination.

## Requirements

- Node.js and npm
- Python 3.10 or newer

## Install

Run these commands in Windows PowerShell from the FogFlow project root:

```powershell
py -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
npm --prefix frontend install
Copy-Item frontend\.env.example frontend\.env.local
```

`VITE_API_BASE_URL` in `frontend\.env.local` points to the local Flask API by default. Update it if the backend runs at a different URL.

## Start the application

Open two PowerShell terminals in the project root.

In the first terminal, start Flask:

```powershell
backend\.venv\Scripts\python.exe backend\app.py
```

In the second terminal, start Vite:

```powershell
npm --prefix frontend run dev
```

Open the local URL printed by Vite (usually `http://localhost:5173`). The dashboard displays **Connected** when `GET /api/health` responds successfully, otherwise it displays **Disconnected**. Dashboard telemetry remains empty until real backend data is available.

The Flask development server allows CORS requests from `http://localhost:5173` and `http://127.0.0.1:5173`.

In the Smart City Digital Twin, drag to pan, scroll to zoom, and select a marker to inspect it. Use the viewport controls to reset the map, switch between the Carto, OpenStreetMap, and tilted OpenStreetMap 3D styles, and toggle sensors, fog nodes, the cloud marker, allocation routes, and the request heatmap. Map coordinates are fictional demo positions in Mumbai and do not indicate real deployed equipment. Starting a simulation produces tasks automatically according to its selected workload; use **Add task manually** or select a sensor to submit an explicit task. The heatmap shows actual task counts grouped by sensor for the current run, and a route is shown only when Flask returns a valid assigned node.

## Simulation API

- `GET /api/health` — health check.
- `GET /api/nodes` — configured nodes and calculated workload.
- `POST /api/nodes/configure` — set `{"fog_node_count": 1}` through `{"fog_node_count": 75}` while the live simulation is idle and has no tasks; the single cloud server remains configured.
- `GET /api/tasks?limit=50&offset=0&status=completed` — current run's persisted tasks.
- `GET /api/metrics` — current run state, task counts (including outage-failed tasks and capacity/deadline rejections separately), observed response time, node workload, `manual_tasks_per_minute` when Manual is selected, and current-run `task_counts_by_sensor` for the request heatmap.
- `POST /api/tasks/generate` — generate deterministic template tasks with `{"count": 3}`, or submit explicit tasks with `{"tasks": [...]}`. A task has `source_sensor_id`, `task_type`, `cpu_demand`, and `data_size_mb`, with optional `priority` (1–10) and `deadline_seconds`.
- `POST /api/simulation/start` — optional JSON `{"workload":"normal","algorithm":"adaptive_fog_cloud"}`. Preset workloads are `light`, `normal`, `heavy`, or `extreme`; their arrival rates are 1, 4, 10, and 20 tasks per simulated second. For a custom rate, use `{"workload":"manual","manual_tasks_per_minute":1200}`; values must be integers from 1 to 10,000. Group 1 live algorithms are `round_robin`, `weighted_round_robin`, `least_loaded`, and `adaptive_fog_cloud`. Group 2 adds `fuzzy_logic`, `topsis`, `ant_colony`, and `particle_swarm`. The `adaptive_fog_cloud` identifier is displayed as **Weighted Sum Model (WSM)**: it minimizes a normalized weighted sum of response time, projected utilization, and estimated completion time. Its weights can be overridden with a `weights` object whose `response_time`, `utilization`, and `completion_time` values sum to 1. Fuzzy Logic uses rules over latency, utilization, and deadline urgency; TOPSIS ranks nodes against ideal and anti-ideal alternatives; ACO uses pheromone-guided candidate exploration and reinforcement; PSO searches the discrete eligible-node choices using the WSM cost objective.
- `POST /api/simulation/pause`, `POST /api/simulation/resume`, and `POST /api/simulation/reset` — control simulation state. Pause/resume/reset accept an empty body. Reset clears live state while preserving the previous run, task records, allocation decisions, and experiment summary in SQLite.

The SQLite database is created at `backend\instance\fogflow.sqlite3`. To change node capacities, rates, latency, availability, database path, or adaptive weights, pass `NODE_CONFIGS`, `DATABASE`, or `ADAPTIVE_WEIGHTS` in the Flask app configuration before creating the app.

For fault modeling, each initially online node has an independently seeded exponential uptime and repair schedule: mean uptime is 300 simulated seconds and mean repair time is 15 simulated seconds. An outage interrupts and fails the node's active task, takes the node out of scheduler eligibility, and allows queued work to resume after recovery. The assumptions provide repeatable behavior for simulation; they are not measured hardware reliability statistics.

## Benchmark and algorithm comparison

The **Experiments (Benchmarks)** and **Algorithm Comparison** sidebar items are functional. A benchmark runs the eight live schedulers from Groups 1 and 2 plus the Min-Min benchmark-only baseline (nine experiments total). It prepares one deterministic cyclic-template workload and replays the exact same task IDs, arrivals, priorities, deadlines, and specifications for every experiment. Every experiment receives a fresh copy of the configured node state. Benchmark runs execute in a worker so the dashboard stays interactive; the regular live simulation is not paused, reset, or otherwise mutated.

The benchmark form defaults to 60 simulated seconds and a normal workload; durations are 15, 30, 60, 120, 300 (5 minutes), or 600 (10 minutes) seconds. Workload profiles are the same deterministic arrival rates as the live simulator. All algorithms in a benchmark replay the same seeded node-outage schedule for a fair comparison. Min-Min groups arrivals into one-simulated-second batches and repeatedly schedules the task-node pair with the smallest estimated completion time. Its batch window and the full workload, node configuration, failure assumptions and seed, WSM weights, per-task outcomes, progress, status, and metrics are persisted in SQLite. A running benchmark can be cancelled; failed or cancelled runs are not presented as complete comparisons.

- `POST /api/benchmarks` — start with optional JSON `{"duration_seconds":60,"workload":"normal"}`; manual workload runs also accept `manual_tasks_per_minute` (1–10,000). Returns `202` and the benchmark ID. A second concurrent request returns `409`.
- `GET /api/benchmarks` — list saved run summaries.
- `GET /api/benchmarks/<benchmark_id>` — fetch status, current algorithm, progress, elapsed time, and completion count.
- `GET /api/benchmarks/<benchmark_id>/results` — fetch persisted metrics after all experiments configured for that benchmark complete. Saved runs retain their own algorithm order, so historical results remain compatible.
- `POST /api/benchmarks/<benchmark_id>/cancel` — request safe cancellation of a running benchmark; accepts an empty body.

Benchmark result definitions use consistent units: response times are completion minus arrival for successfully completed tasks; throughput is completed tasks per configured simulated second; average node utilization is each node's task-processing seconds divided by experiment makespan, averaged across nodes; load imbalance is the population standard deviation of those node utilization percentages; failed tasks are interrupted by simulated node outages, while rejected tasks have no eligible node due to availability, capacity, or deadline constraints (the comparison's failure total includes both); deadline misses include deadline-based rejections and tardy completions; cloud offloading is cloud-assigned tasks divided by generated tasks; makespan ends at the last completion after the workload horizon. Zero or unavailable measurements are returned as `null` where appropriate. The comparison page ranks metrics individually and explains its equal-weight rank-based score; offloading and utilization are reported but excluded from that overall score because neither has a universally optimal value.

Ordinary automatic simulation generation and the **Add task manually** action on Overview remain separate from controlled benchmark inputs.

## Checks

```powershell
npm --prefix frontend run lint
npm --prefix frontend run build
Set-Location backend
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
