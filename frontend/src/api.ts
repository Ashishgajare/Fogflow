export type Algorithm =
  | 'round_robin'
  | 'weighted_round_robin'
  | 'least_loaded'
  | 'adaptive_fog_cloud'
export type Workload = 'light' | 'normal' | 'heavy'
export type SimulationState = 'idle' | 'running' | 'paused' | 'reset'
export type TaskStatus = 'pending' | 'queued' | 'running' | 'completed' | 'rejected'
export type BenchmarkStatus = 'running' | 'completed' | 'failed' | 'cancelled'

export type BenchmarkAlgorithm =
  | 'round_robin'
  | 'weighted_round_robin'
  | 'least_loaded'
  | 'min_min'
  | 'adaptive_fog_cloud'

export interface BenchmarkConfiguration {
  duration_seconds: number
  workload: Workload
  task_count: number
  algorithm_order: BenchmarkAlgorithm[]
  adaptive_weights: Record<string, number>
  min_min_batch_seconds: number
  workload_generator: string
  node_configs: Array<{
    id: string
    name: string
    node_type: 'fog' | 'cloud'
    processing_capacity: number
    processing_rate: number
    network_latency_ms: number
    online: boolean
  }>
}

export interface BenchmarkTaskOutcome {
  task_id: string
  source_sensor_id: string
  assigned_node_id: string | null
  status: TaskStatus
  arrival_clock: number
  completion_clock: number | null
  actual_response_time_ms: number | null
  deadline_seconds: number | null
  allocation_explanation: string
}

export interface BenchmarkResult {
  algorithm: BenchmarkAlgorithm
  algorithm_label: string
  duration_seconds: number
  task_count: number
  completed_count: number
  failed_count: number
  deadline_misses: number
  average_response_time_ms: number | null
  median_response_time_ms: number | null
  throughput_tasks_per_second: number
  average_node_utilization_percent: number | null
  load_imbalance_percent: number | null
  cloud_offloading_rate_percent: number | null
  makespan_seconds: number | null
  node_utilization_percent: Record<string, number | null>
  task_outcomes: BenchmarkTaskOutcome[]
}

export interface BenchmarkRecord {
  benchmark_id: string
  status: BenchmarkStatus
  duration_seconds: number
  workload: Workload
  configuration: BenchmarkConfiguration
  current_algorithm: BenchmarkAlgorithm | null
  completed_experiments: number
  progress_percent: number
  simulated_seconds: number
  elapsed_seconds: number
  result_count: number
  error: string | null
  created_at: string
  updated_at: string
}

export interface BenchmarkResultsResponse {
  benchmark_id: string
  configuration: BenchmarkConfiguration
  results: BenchmarkResult[]
}

export interface HealthResponse {
  status: string
}

export interface NodeTelemetry {
  id: string
  name: string
  node_type: 'fog' | 'cloud'
  processing_capacity: number
  processing_rate: number
  network_latency_ms: number
  current_workload: number
  projected_workload: number
  projected_utilization: number
  queue_length: number
  online: boolean
  active_task_id: string | null
}

export interface TaskRecord {
  task_id: string
  source_sensor_id: string
  task_type: string
  cpu_demand: number
  data_size_mb: number
  priority: number
  deadline_seconds: number | null
  arrival_time: string
  assigned_node_id: string | null
  estimated_network_latency_ms: number | null
  estimated_processing_time_ms: number | null
  estimated_response_time_ms: number | null
  actual_response_time_ms: number | null
  completion_time: string | null
  status: TaskStatus
  allocation_explanation: string
}

export interface NodesResponse {
  nodes: NodeTelemetry[]
}

export interface TasksResponse {
  run_id: string
  tasks: TaskRecord[]
  total: number
  limit: number
  offset: number
}

export interface MetricsResponse {
  run_id: string
  state: SimulationState
  algorithm: Algorithm
  workload: Workload
  simulation_clock_seconds: number
  task_count: number
  tasks_completed: number
  tasks_running: number
  tasks_queued: number
  tasks_rejected: number
  average_response_time_ms: number | null
  task_counts_by_sensor: Record<string, number>
  nodes: NodeTelemetry[]
}

export interface SimulationResponse {
  simulation: {
    run_id: string
    state: SimulationState
    algorithm: Algorithm
    workload: Workload
    simulation_clock_seconds: number
  }
}

export interface GenerateTasksResponse {
  run_id: string
  generated: number
  tasks: TaskRecord[]
}

export interface TaskSpecification {
  source_sensor_id: string
  task_type: string
  cpu_demand: number
  data_size_mb: number
  priority: number
  deadline_seconds: number | null
}

const apiBaseUrl = (
  import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:5000'
).replace(/\/+$/, '')

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    headers: {
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...init.headers,
    },
  })

  const body: unknown = await response.json()
  if (!response.ok) {
    const message =
      typeof body === 'object' &&
      body !== null &&
      'error' in body &&
      typeof body.error === 'object' &&
      body.error !== null &&
      'message' in body.error &&
      typeof body.error.message === 'string'
        ? body.error.message
        : `Request failed with status ${response.status}`
    throw new Error(message)
  }

  return body as T
}

export async function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const health = await request<HealthResponse>('/api/health', { signal })
  if (health.status !== 'ok') {
    throw new Error('The API returned an unexpected health status')
  }
  return health
}

export function getNodes(signal?: AbortSignal): Promise<NodesResponse> {
  return request('/api/nodes', { signal })
}

export function getTasks(
  limit = 50,
  offset = 0,
  signal?: AbortSignal,
): Promise<TasksResponse> {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  return request(`/api/tasks?${query}`, { signal })
}

export function getMetrics(signal?: AbortSignal): Promise<MetricsResponse> {
  return request('/api/metrics', { signal })
}

export function generateTasks(
  count = 6,
  signal?: AbortSignal,
): Promise<GenerateTasksResponse> {
  return request('/api/tasks/generate', {
    method: 'POST',
    body: JSON.stringify({ count }),
    signal,
  })
}

export function generateTask(
  task: TaskSpecification,
): Promise<GenerateTasksResponse> {
  return request('/api/tasks/generate', {
    method: 'POST',
    body: JSON.stringify({ tasks: [task] }),
  })
}

export function startSimulation(
  workload: Workload,
  algorithm: Algorithm,
): Promise<SimulationResponse> {
  return request('/api/simulation/start', {
    method: 'POST',
    body: JSON.stringify({ workload, algorithm }),
  })
}

export function pauseSimulation(): Promise<SimulationResponse> {
  return request('/api/simulation/pause', { method: 'POST' })
}

export function resumeSimulation(): Promise<SimulationResponse> {
  return request('/api/simulation/resume', { method: 'POST' })
}

export function resetSimulation(): Promise<SimulationResponse> {
  return request('/api/simulation/reset', { method: 'POST' })
}

export function startBenchmark(
  durationSeconds: number,
  workload: Workload,
): Promise<{ benchmark: BenchmarkRecord }> {
  return request('/api/benchmarks', {
    method: 'POST',
    body: JSON.stringify({
      duration_seconds: durationSeconds,
      workload,
    }),
  })
}

export function getBenchmarks(
  signal?: AbortSignal,
): Promise<{ benchmarks: BenchmarkRecord[] }> {
  return request('/api/benchmarks', { signal })
}

export function getBenchmark(
  benchmarkId: string,
  signal?: AbortSignal,
): Promise<{ benchmark: BenchmarkRecord }> {
  return request(`/api/benchmarks/${encodeURIComponent(benchmarkId)}`, { signal })
}

export function getBenchmarkResults(
  benchmarkId: string,
  signal?: AbortSignal,
): Promise<BenchmarkResultsResponse> {
  return request(
    `/api/benchmarks/${encodeURIComponent(benchmarkId)}/results`,
    { signal },
  )
}

export function cancelBenchmark(
  benchmarkId: string,
): Promise<{ benchmark: BenchmarkRecord }> {
  return request(
    `/api/benchmarks/${encodeURIComponent(benchmarkId)}/cancel`,
    { method: 'POST' },
  )
}
