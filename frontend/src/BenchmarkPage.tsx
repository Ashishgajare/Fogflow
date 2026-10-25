import { useCallback, useEffect, useState } from 'react'
import {
  cancelBenchmark,
  getBenchmark,
  getBenchmarks,
  startBenchmark,
  type BenchmarkAlgorithm,
  type BenchmarkRecord,
  type NodeTelemetry,
  type Workload,
} from './api'

const algorithmNames: Record<BenchmarkAlgorithm, string> = {
  round_robin: 'Round Robin',
  weighted_round_robin: 'Weighted Round Robin',
  least_loaded: 'Least Loaded',
  min_min: 'Min-Min Scheduling',
  adaptive_fog_cloud: 'Weighted Sum Model (WSM)',
  fuzzy_logic: 'Fuzzy Logic',
  topsis: 'TOPSIS',
  ant_colony: 'Ant Colony Optimization (ACO)',
  particle_swarm: 'Particle Swarm Optimization (PSO)',
}
const algorithmGroups: Array<{
  label: string
  algorithms: BenchmarkAlgorithm[]
}> = [
  {
    label: 'Group 1 · Current algorithms',
    algorithms: [
      'round_robin',
      'weighted_round_robin',
      'least_loaded',
      'adaptive_fog_cloud',
    ],
  },
  {
    label: 'Benchmark-only baseline',
    algorithms: ['min_min'],
  },
  {
    label: 'Group 2 · Advanced algorithms',
    algorithms: ['fuzzy_logic', 'topsis', 'ant_colony', 'particle_swarm'],
  },
]
const MANUAL_TASKS_PER_MINUTE_MAX = 10_000

interface BenchmarkPageProps {
  connected: boolean
  loadingNodes: boolean
  nodes: NodeTelemetry[]
  onViewComparison: (benchmarkId: string) => void
}

function describeNodeCounts(nodeConfigs: BenchmarkRecord['configuration']['node_configs']): string {
  const fogCount = nodeConfigs.filter((node) => node.node_type === 'fog').length
  const cloudCount = nodeConfigs.filter((node) => node.node_type === 'cloud').length
  return `${fogCount} fog · ${cloudCount} cloud · ${nodeConfigs.length} total`
}

function describeWorkload(run: BenchmarkRecord): string {
  return run.workload === 'manual'
    ? `manual · ${run.configuration.manual_tasks_per_minute?.toLocaleString() ?? 'unknown'} tasks/min`
    : `${run.workload} workload`
}

function formatElapsed(seconds: number): string {
  return `${seconds.toFixed(1)} s`
}

function mergeBenchmarkHistory(
  current: BenchmarkRecord[],
  incoming: BenchmarkRecord[],
): BenchmarkRecord[] {
  const runs = new Map(current.map((run) => [run.benchmark_id, run]))
  for (const run of incoming) {
    const existing = runs.get(run.benchmark_id)
    if (!existing || Date.parse(run.updated_at) >= Date.parse(existing.updated_at)) {
      runs.set(run.benchmark_id, run)
    }
  }
  return [...runs.values()].sort((first, second) =>
    second.created_at.localeCompare(first.created_at),
  )
}

function BenchmarkPage({
  connected,
  loadingNodes,
  nodes,
  onViewComparison,
}: BenchmarkPageProps) {
  const [duration, setDuration] = useState(60)
  const [workload, setWorkload] = useState<Workload>('normal')
  const [manualTasksPerMinute, setManualTasksPerMinute] = useState(1200)
  const [history, setHistory] = useState<BenchmarkRecord[]>([])
  const [benchmark, setBenchmark] = useState<BenchmarkRecord | null>(null)
  const [busy, setBusy] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [historyLoading, setHistoryLoading] = useState(true)
  const [showProgress, setShowProgress] = useState(false)
  const [dismissedBenchmarkId, setDismissedBenchmarkId] = useState<string | null>(
    null,
  )
  const activeHistoryRun = history.find((run) => run.status === 'running') ?? null
  const trackedBenchmark = benchmark ?? activeHistoryRun
  const trackedBenchmarkId = trackedBenchmark?.benchmark_id
  const trackedBenchmarkStatus = trackedBenchmark?.status
  const benchmarkRunning = trackedBenchmarkStatus === 'running'
  const progressVisible =
    showProgress ||
    (!benchmark &&
      activeHistoryRun !== null &&
      dismissedBenchmarkId !== activeHistoryRun.benchmark_id)
  const validManualRate =
    Number.isInteger(manualTasksPerMinute) &&
    manualTasksPerMinute >= 1 &&
    manualTasksPerMinute <= MANUAL_TASKS_PER_MINUTE_MAX

  const fetchHistory = useCallback(async (signal?: AbortSignal) => {
    const response = await getBenchmarks(signal)
    return response.benchmarks
  }, [])
  const storeHistory = useCallback((runs: BenchmarkRecord[]) => {
    setHistory((current) => mergeBenchmarkHistory(current, runs))
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    void fetchHistory(controller.signal)
      .then(storeHistory)
      .catch((requestError: unknown) => {
        if (!controller.signal.aborted) {
          setError(
            requestError instanceof Error
              ? requestError.message
              : 'Could not load saved benchmark runs.',
          )
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setHistoryLoading(false)
      })
    return () => controller.abort()
  }, [fetchHistory, storeHistory])

  useEffect(() => {
    if (!trackedBenchmarkId || trackedBenchmarkStatus !== 'running') return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    let cancelled = false

    const poll = async () => {
      try {
        const response = await getBenchmark(
          trackedBenchmarkId,
          controller.signal,
        )
        if (cancelled || controller.signal.aborted) return
        setBenchmark(response.benchmark)
        if (response.benchmark.status === 'running') {
          timer = setTimeout(poll, 600)
        } else {
          void fetchHistory(controller.signal)
            .then(storeHistory)
            .catch((requestError: unknown) => {
              if (!controller.signal.aborted) {
                setError(
                  requestError instanceof Error
                    ? requestError.message
                    : 'Could not refresh saved benchmark runs.',
                )
              }
            })
        }
      } catch (requestError) {
        if (!controller.signal.aborted) {
          setError(
            requestError instanceof Error
              ? requestError.message
              : 'Could not refresh benchmark progress.',
          )
          timer = setTimeout(poll, 1200)
        }
      }
    }

    void poll()
    return () => {
      cancelled = true
      controller.abort()
      clearTimeout(timer)
    }
  }, [trackedBenchmarkId, trackedBenchmarkStatus, fetchHistory, storeHistory])

  const run = async () => {
    if (
      busy ||
      !connected ||
      benchmarkRunning ||
      (workload === 'manual' && !validManualRate)
    ) return
    setBusy(true)
    setError(null)
    try {
      const response = await startBenchmark(
        duration,
        workload,
        workload === 'manual' ? manualTasksPerMinute : undefined,
      )
      setBenchmark(response.benchmark)
      setShowProgress(true)
      setDismissedBenchmarkId(null)
      void fetchHistory()
        .then(storeHistory)
        .catch((requestError: unknown) => {
          setError(
            requestError instanceof Error
              ? requestError.message
              : 'The benchmark started, but history could not refresh.',
          )
        })
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : 'Could not start the benchmark.',
      )
    } finally {
      setBusy(false)
    }
  }

  const cancel = async () => {
    if (!benchmark || cancelling) return
    setCancelling(true)
    setError(null)
    try {
      const response = await cancelBenchmark(benchmark.benchmark_id)
      setBenchmark(response.benchmark)
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : 'The benchmark could not be cancelled.',
      )
    } finally {
      setCancelling(false)
    }
  }

  const openSavedRun = (run: BenchmarkRecord) => {
    setBenchmark(run)
    setShowProgress(run.status !== 'completed')
    setDismissedBenchmarkId(null)
    if (run.status === 'completed') {
      onViewComparison(run.benchmark_id)
    }
  }

  const closeProgress = () => {
    if (trackedBenchmark?.status === 'running') {
      setDismissedBenchmarkId(trackedBenchmark.benchmark_id)
    }
    setShowProgress(false)
  }

  const progressStatus = trackedBenchmark?.status ?? 'running'

  return (
    <>
      <div className="dashboard-content benchmark-content">
        <section className="workspace-heading">
          <div>
            <span className="section-label">Experiments · Benchmark lab</span>
            <h1>Benchmark &amp; Compare Algorithms</h1>
            <p>
              Run nine schedulers against the same deterministic task arrivals
              and equivalent fresh node configurations.
            </p>
          </div>
          <span className="future-tag">Isolated experiments · SQLite saved</span>
        </section>

        <section className="benchmark-config panel">
          <div className="benchmark-config-copy">
            <span className="section-label">New benchmark</span>
            <h2>Prepare a controlled workload</h2>
            <p>
              Task definitions and arrival times are generated once, then
              replayed across two groups: Group 1 includes the current four
              schedulers, and Group 2 adds Fuzzy Logic, TOPSIS, ACO, and PSO.
              Min-Min remains as a benchmark-only baseline.
            </p>
            <p>
              Simulated node outages use an independent exponential schedule
              (mean 300 seconds uptime, 15 seconds repair). Every algorithm
              receives the same seeded schedule; these are explicit simulation
              assumptions, not measured hardware reliability.
            </p>
            <p className="benchmark-node-count" role="status">
              {nodes.length
                ? `Current nodes for a new benchmark: ${describeNodeCounts(nodes)}`
                : loadingNodes
                  ? 'Loading the current node configuration…'
                  : connected
                    ? 'Node configuration is not available.'
                    : 'Connect to the backend to load the current node configuration.'}
            </p>
          </div>
          <div className="benchmark-config-controls">
            <label>
              <span className="section-label">Simulated duration</span>
              <select
                value={duration}
                onChange={(event) => setDuration(Number(event.target.value))}
                disabled={busy || benchmarkRunning}
              >
                <option value={15}>15 seconds</option>
                <option value={30}>30 seconds</option>
                <option value={60}>60 seconds (default)</option>
                <option value={120}>120 seconds</option>
                <option value={300}>5 minutes</option>
                <option value={600}>10 minutes</option>
              </select>
            </label>
            <label>
              <span className="section-label">Workload profile</span>
              <select
                value={workload}
                onChange={(event) => setWorkload(event.target.value as Workload)}
                disabled={busy || benchmarkRunning}
              >
                <option value="light">Light</option>
                <option value="normal">Normal</option>
                <option value="heavy">Heavy</option>
                <option value="extreme">Extreme</option>
                <option value="manual">Manual</option>
              </select>
            </label>
            {workload === 'manual' && (
              <label>
                <span className="section-label">Manual rate · tasks/min</span>
                <input
                  type="number"
                  min="1"
                  max={MANUAL_TASKS_PER_MINUTE_MAX}
                  step="1"
                  value={manualTasksPerMinute}
                  onChange={(event) => setManualTasksPerMinute(Number(event.target.value))}
                  disabled={busy || benchmarkRunning}
                  aria-invalid={!validManualRate}
                />
                <small>Enter a whole number from 1 to 10,000.</small>
              </label>
            )}
            <button
              type="button"
              className="control-button control-button--generate benchmark-run-button"
              onClick={() => void run()}
              disabled={busy || !connected || benchmarkRunning || (workload === 'manual' && !validManualRate)}
            >
              <span className="material-symbols-outlined" aria-hidden="true">
                science
              </span>
              {busy ? 'Starting…' : 'Run Benchmark'}
            </button>
          </div>
        </section>

        {error && (
          <p className="feedback-message feedback-message--error" role="alert">
            <span className="material-symbols-outlined" aria-hidden="true">error</span>
            {error}
          </p>
        )}

        <section className="benchmark-algorithm-grid" aria-label="Algorithms in this benchmark">
          {algorithmGroups.map((group) => (
            <div className="benchmark-algorithm-group" key={group.label}>
              <h2 className="section-label">{group.label}</h2>
              <div className="benchmark-algorithm-group-list">
                {group.algorithms.map((key) => (
                  <article className="panel benchmark-algorithm-card" key={key}>
                    <span className="benchmark-algorithm-index">
                      {String(
                        [...group.algorithms].indexOf(key) + 1,
                      ).padStart(2, '0')}
                    </span>
                    <div>
                      <span className="section-label">Scheduler</span>
                      <h3>{algorithmNames[key]}</h3>
                    </div>
                  </article>
                ))}
              </div>
            </div>
          ))}
        </section>

        <section className="panel benchmark-history">
          <div className="panel-heading">
            <div className="heading-with-icon">
              <span className="material-symbols-outlined heading-icon" aria-hidden="true">
                history
              </span>
              <div>
                <h2>Previous benchmark runs</h2>
                <p>Completed comparisons and interrupted runs saved in SQLite</p>
              </div>
            </div>
            <span className="panel-count">{history.length} saved</span>
          </div>
          {historyLoading ? (
            <p className="data-placeholder">Loading benchmark history…</p>
          ) : history.length === 0 ? (
            <div className="benchmark-empty">
              <span className="material-symbols-outlined" aria-hidden="true">query_stats</span>
              <strong>No saved benchmarks yet</strong>
              <span>Run a benchmark to see measured algorithm comparisons here.</span>
            </div>
          ) : (
            <div className="benchmark-history-list">
              {history.map((run) => (
                <article className="benchmark-history-row" key={run.benchmark_id}>
                  <div className="benchmark-history-main">
                    <strong>{new Date(run.created_at).toLocaleString()}</strong>
                    <span>
                      {run.configuration.task_count.toLocaleString()} tasks ·{' '}
                      {run.duration_seconds}s · {describeWorkload(run)} ·{' '}
                      {describeNodeCounts(run.configuration.node_configs)}
                    </span>
                  </div>
                  <div className="benchmark-history-meta">
                    <span className={`benchmark-status benchmark-status--${run.status}`}>
                      {run.status}
                    </span>
                    <span>
                      {run.completed_experiments}/
                      {run.configuration.algorithm_order.length} experiments
                    </span>
                    <button
                      type="button"
                      className="control-button control-button--secondary"
                      onClick={() => openSavedRun(run)}
                    >
                      {run.status === 'completed'
                        ? 'View Comparison'
                        : run.status === 'running'
                          ? 'View Progress'
                          : 'View Details'}
                    </button>
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>

        <p className="benchmark-note">
          Automatic benchmark tasks use the configured deterministic sensor
          templates. For individual manual tasks, return to Overview and use
          <strong> Add task manually</strong>; those tasks remain in the live
          simulation and are not mixed into a controlled benchmark.
        </p>
      </div>

      {progressVisible && trackedBenchmark && (
        <div className="benchmark-modal-backdrop">
          <section
            className="benchmark-modal panel"
            role="dialog"
            aria-modal="true"
            aria-labelledby="benchmark-progress-title"
          >
            <div className="benchmark-modal-heading">
              <div>
                <span className="section-label">Benchmark run</span>
                <h2 id="benchmark-progress-title">
                  {progressStatus === 'completed'
                    ? 'All experiments completed'
                    : progressStatus === 'failed'
                      ? 'Benchmark failed'
                      : progressStatus === 'cancelled'
                        ? 'Benchmark cancelled'
                        : 'Running algorithm comparison'}
                </h2>
              </div>
              <button
                type="button"
                className="modal-close"
                aria-label="Close progress dialog"
                onClick={closeProgress}
              >
                ×
              </button>
            </div>
            <div className="benchmark-progress-summary">
              <span className={`benchmark-status benchmark-status--${progressStatus}`}>
                {progressStatus}
              </span>
              <span>
                {trackedBenchmark.completed_experiments} of{' '}
                {trackedBenchmark.configuration.algorithm_order.length} experiments
                complete
              </span>
              <span>Elapsed {formatElapsed(trackedBenchmark.elapsed_seconds)}</span>
            </div>
            <p className="benchmark-node-count">
              Nodes used per algorithm:{' '}
              {describeNodeCounts(trackedBenchmark.configuration.node_configs)}
            </p>
            <p className="benchmark-node-count">
              Workload: {describeWorkload(trackedBenchmark)}
            </p>
            <div
              className="benchmark-progress-track"
              role="progressbar"
              aria-label="Overall benchmark progress"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(trackedBenchmark.progress_percent)}
            >
              <span style={{ width: `${trackedBenchmark.progress_percent}%` }} />
            </div>
            <div className="benchmark-current">
              <span className="section-label">
                {trackedBenchmark.status === 'running'
                  ? 'Current algorithm'
                  : 'Benchmark status'}
              </span>
              <strong>
                {trackedBenchmark.current_algorithm
                  ? algorithmNames[trackedBenchmark.current_algorithm]
                  : progressStatus === 'completed'
                    ? 'All nine results are persisted and ready.'
                    : progressStatus === 'failed'
                      ? 'The comparison is incomplete; partial results are not ranked.'
                      : 'No experiment is currently running.'}
              </strong>
              {trackedBenchmark.status === 'running' && (
                <span>
                  {trackedBenchmark.simulated_seconds.toFixed(1)} simulated seconds of{' '}
                  {trackedBenchmark.duration_seconds}
                </span>
              )}
            </div>
            {trackedBenchmark.error && (
              <p className="benchmark-modal-error" role="alert">{trackedBenchmark.error}</p>
            )}
            <div className="benchmark-modal-actions">
              {trackedBenchmark.status === 'running' ? (
                <button
                  type="button"
                  className="control-button control-button--secondary"
                  onClick={() => void cancel()}
                  disabled={cancelling}
                >
                  {cancelling ? 'Cancelling…' : 'Cancel benchmark'}
                </button>
              ) : trackedBenchmark.status === 'completed' ? (
                <button
                  type="button"
                  className="control-button control-button--generate"
                  onClick={() => onViewComparison(trackedBenchmark.benchmark_id)}
                >
                  View Comparison
                </button>
              ) : (
                <button
                  type="button"
                  className="control-button control-button--secondary"
                  onClick={closeProgress}
                >
                  Close details
                </button>
              )}
              <button
                type="button"
                className="control-button control-button--secondary"
                onClick={() => setShowProgress(false)}
              >
                {trackedBenchmark.status === 'running' ? 'Continue in background' : 'Close'}
              </button>
            </div>
          </section>
        </div>
      )}
    </>
  )
}

export default BenchmarkPage
