import { useEffect, useMemo, useState } from 'react'
import {
  getBenchmarkResults,
  getBenchmarks,
  type BenchmarkRecord,
  type BenchmarkResult,
} from './api'

type MetricKey =
  | 'average_response_time_ms'
  | 'median_response_time_ms'
  | 'throughput_tasks_per_second'
  | 'average_node_utilization_percent'
  | 'load_imbalance_percent'
  | 'completed_count'
  | 'failed_count'
  | 'deadline_misses'
  | 'cloud_offloading_rate_percent'
  | 'makespan_seconds'

type Direction = 'higher' | 'lower' | 'informational'

interface MetricDefinition {
  key: MetricKey
  label: string
  unit: string
  direction: Direction
  decimals?: number
}

interface AlgorithmComparisonProps {
  benchmarkId: string | null
  onBack: () => void
  onViewBenchmarks: () => void
}

const METRICS: MetricDefinition[] = [
  { key: 'average_response_time_ms', label: 'Average response time', unit: 'ms', direction: 'lower' },
  { key: 'median_response_time_ms', label: 'Median response time', unit: 'ms', direction: 'lower' },
  { key: 'throughput_tasks_per_second', label: 'Throughput', unit: 'tasks/s', direction: 'higher', decimals: 3 },
  { key: 'average_node_utilization_percent', label: 'Average node utilization', unit: '%', direction: 'higher' },
  { key: 'load_imbalance_percent', label: 'Load imbalance', unit: 'percentage points', direction: 'lower' },
  { key: 'completed_count', label: 'Tasks completed', unit: 'tasks', direction: 'higher', decimals: 0 },
  { key: 'failed_count', label: 'Tasks failed or rejected', unit: 'tasks', direction: 'lower', decimals: 0 },
  { key: 'deadline_misses', label: 'Deadline misses', unit: 'tasks', direction: 'lower', decimals: 0 },
  { key: 'cloud_offloading_rate_percent', label: 'Cloud offloading rate', unit: '% of generated tasks', direction: 'lower' },
  { key: 'makespan_seconds', label: 'Makespan', unit: 's', direction: 'lower' },
]

const SCORE_METRICS: MetricKey[] = [
  'completed_count',
  'failed_count',
  'deadline_misses',
  'average_response_time_ms',
  'throughput_tasks_per_second',
  'load_imbalance_percent',
  'makespan_seconds',
]

const seriesColors = ['#22d3ee', '#4edea3', '#ffb95f', '#a78bfa', '#77a9ff']

function formatValue(
  value: number | null,
  unit: string,
  digits = 1,
): string {
  if (value === null || !Number.isFinite(value)) return '—'
  const formatted = value.toLocaleString(undefined, {
    maximumFractionDigits: digits,
  })
  return unit ? `${formatted} ${unit}` : formatted
}

function shortAlgorithmLabel(algorithm: BenchmarkResult['algorithm']): string {
  switch (algorithm) {
    case 'round_robin':
      return 'RR'
    case 'weighted_round_robin':
      return 'WRR'
    case 'least_loaded':
      return 'Least'
    case 'adaptive_fog_cloud':
      return 'WSM'
    case 'min_min':
      return 'Min-Min'
    case 'fuzzy_logic':
      return 'Fuzzy'
    case 'topsis':
      return 'TOPSIS'
    case 'ant_colony':
      return 'ACO'
    case 'particle_swarm':
      return 'PSO'
  }
}

function rankMetric(
  results: BenchmarkResult[],
  key: MetricKey,
  direction: Direction,
): Map<string, number> {
  const values: { algorithm: string; value: number }[] = results.flatMap(
    (result) => {
      const value = result[key]
      return typeof value === 'number' && Number.isFinite(value)
        ? [{ algorithm: result.algorithm, value }]
        : []
    },
  )
  values.sort((first, second) =>
    direction === 'higher'
      ? second.value - first.value
      : first.value - second.value,
  )
  const ranks = new Map<string, number>()
  for (let start = 0; start < values.length; ) {
    let end = start
    while (end + 1 < values.length && values[end + 1].value === values[start].value) {
      end += 1
    }
    const averageRank = (start + end) / 2 + 1
    for (let index = start; index <= end; index += 1) {
      ranks.set(values[index].algorithm, averageRank)
    }
    start = end + 1
  }
  return ranks
}

function calculateScores(results: BenchmarkResult[]): Map<string, number> {
  const comparable = SCORE_METRICS.filter((key) =>
    results.every((result) => {
      const value = result[key]
      return typeof value === 'number' && Number.isFinite(value)
    }),
  )
  const totals = new Map(results.map((result) => [result.algorithm, 0]))
  if (!comparable.length || results.length < 2) return totals

  for (const key of comparable) {
    const direction: Direction =
      key === 'completed_count' || key === 'throughput_tasks_per_second'
        ? 'higher'
        : 'lower'
    const ranks = rankMetric(results, key, direction)
    for (const result of results) {
      const rank = ranks.get(result.algorithm)
      if (rank === undefined) continue
      const points =
        results.length === 1
          ? 100
          : ((results.length - rank) / (results.length - 1)) * 100
      totals.set(
        result.algorithm,
        (totals.get(result.algorithm) ?? 0) + points / comparable.length,
      )
    }
  }
  return totals
}

interface ChartSeries {
  key: MetricKey
  label: string
  color: string
  decimals?: number
}

function GroupedBarChart({
  title,
  description,
  results,
  series,
  unit,
}: {
  title: string
  description: string
  results: BenchmarkResult[]
  series: ChartSeries[]
  unit: string
}) {
  const maximum = Math.max(
    0,
    ...results.flatMap((result) =>
      series.map((item) => {
        const value = result[item.key]
        return typeof value === 'number' && Number.isFinite(value) ? value : 0
      }),
    ),
  )

  return (
    <article className="panel comparison-chart-card">
      <div className="panel-heading">
        <div>
          <h2>{title}</h2>
          <p>{description}</p>
        </div>
        <span className="chart-unit">{unit}</span>
      </div>
      <div
        className="grouped-chart"
        role="img"
        aria-label={`${title} comparison across benchmark algorithms`}
      >
        {results.map((result) => (
          <div className="grouped-chart-category" key={result.algorithm}>
            <div className="grouped-chart-bars">
              {series.map((item) => {
                const rawValue = result[item.key]
                const value =
                  typeof rawValue === 'number' && Number.isFinite(rawValue)
                    ? rawValue
                    : null
                const height =
                  value === null || maximum === 0
                    ? 0
                    : Math.max((value / maximum) * 100, 2)
                return (
                  <span
                    className="grouped-chart-bar"
                    key={item.key}
                    title={`${item.label}: ${formatValue(value, unit, item.decimals)}`}
                    style={{
                      height: `${height}%`,
                      backgroundColor: item.color,
                    }}
                  />
                )
              })}
            </div>
            <span className="grouped-chart-label">{result.algorithm_label}</span>
          </div>
        ))}
      </div>
      <div className="comparison-chart-legend">
        {series.map((item) => (
          <span key={item.key}>
            <i style={{ backgroundColor: item.color }} />
            {item.label}
          </span>
        ))}
      </div>
      <div className="comparison-chart-values">
        {results.map((result) => (
          <div key={result.algorithm}>
            <strong>{result.algorithm_label}</strong>
            {series.map((item) => (
              <span key={item.key}>
                {item.label}:{' '}
                {formatValue(
                  typeof result[item.key] === 'number'
                    ? result[item.key] as number
                    : null,
                  unit,
                  item.decimals,
                )}
              </span>
            ))}
          </div>
        ))}
      </div>
    </article>
  )
}

function AlgorithmComparison({
  benchmarkId,
  onBack,
  onViewBenchmarks,
}: AlgorithmComparisonProps) {
  const [history, setHistory] = useState<BenchmarkRecord[]>([])
  const [selectedId, setSelectedId] = useState(benchmarkId ?? '')
  const [loadedComparison, setLoadedComparison] = useState<{
    benchmarkId: string
    results: BenchmarkResult[]
    configuration: BenchmarkRecord['configuration']
  } | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const selectedRun = history.find((run) => run.benchmark_id === selectedId)

  useEffect(() => {
    const controller = new AbortController()
    void getBenchmarks(controller.signal)
      .then((response) => {
        setHistory(response.benchmarks)
        const initial = benchmarkId
          ? response.benchmarks.find((run) => run.benchmark_id === benchmarkId)
          : response.benchmarks.find((run) => run.status === 'completed') ??
            response.benchmarks[0]
        setSelectedId(initial?.benchmark_id ?? '')
      })
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
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [benchmarkId])

  useEffect(() => {
    if (!selectedId || selectedRun?.status !== 'completed') return
    const controller = new AbortController()
    void getBenchmarkResults(selectedId, controller.signal)
      .then((response) => {
        setLoadedComparison({
          benchmarkId: selectedId,
          results: response.results,
          configuration: response.configuration,
        })
        setError(null)
      })
      .catch((requestError: unknown) => {
        if (!controller.signal.aborted) {
          setError(
            requestError instanceof Error
              ? requestError.message
              : 'Could not load persisted comparison results.',
          )
        }
      })
    return () => controller.abort()
  }, [selectedId, selectedRun?.status])

  const results =
    loadedComparison?.benchmarkId === selectedId
      ? loadedComparison.results
      : null
  const configuration =
    loadedComparison?.benchmarkId === selectedId
      ? loadedComparison.configuration
      : null
  const scores = useMemo(
    () => (results ? calculateScores(results) : new Map<string, number>()),
    [results],
  )

  return (
    <div className="dashboard-content comparison-content">
      <section className="workspace-heading">
        <div>
          <span className="section-label">Algorithm comparison</span>
          <h1>Measured Benchmark Results</h1>
          <p>
            All charts and rankings below are loaded from persisted Flask
            benchmark results. No sample or placeholder measurements are used.
          </p>
        </div>
        <div className="comparison-actions">
          <button
            type="button"
            className="control-button control-button--secondary"
            onClick={onBack}
          >
            ← Overview
          </button>
          <button
            type="button"
            className="control-button control-button--generate"
            onClick={onViewBenchmarks}
          >
            <span className="material-symbols-outlined" aria-hidden="true">
              science
            </span>
            Benchmark lab
          </button>
        </div>
      </section>

      {history.length > 0 && (
        <section className="panel comparison-run-picker">
          <label>
            <span className="section-label">Saved benchmark run</span>
            <select
              value={selectedId}
              onChange={(event) => setSelectedId(event.target.value)}
            >
              {history.map((run) => (
                <option key={run.benchmark_id} value={run.benchmark_id}>
                  {new Date(run.created_at).toLocaleString()} · {run.status} ·{' '}
                  {run.duration_seconds}s {run.workload}
                </option>
              ))}
            </select>
          </label>
          {selectedRun && (
            <span className={`benchmark-status benchmark-status--${selectedRun.status}`}>
              {selectedRun.completed_experiments}/
              {selectedRun.configuration.algorithm_order.length} · {selectedRun.status}
            </span>
          )}
        </section>
      )}

      {error && (
        <p className="feedback-message feedback-message--error" role="alert">
          <span className="material-symbols-outlined" aria-hidden="true">error</span>
          {error}
        </p>
      )}

      {loading ? (
        <section className="panel comparison-empty">
          <span className="material-symbols-outlined" aria-hidden="true">hourglass_empty</span>
          Loading saved benchmark runs…
        </section>
      ) : history.length === 0 ? (
        <section className="panel comparison-empty">
          <span className="material-symbols-outlined" aria-hidden="true">query_stats</span>
          <h2>No benchmark results yet</h2>
          <p>Run a benchmark across both algorithm groups to populate this comparison page.</p>
          <button
            type="button"
            className="control-button control-button--generate"
            onClick={onViewBenchmarks}
          >
            Go to Experiments
          </button>
        </section>
      ) : selectedRun?.status !== 'completed' ? (
        <section className="panel comparison-empty">
          <span className="material-symbols-outlined" aria-hidden="true">
            {selectedRun?.status === 'failed' ? 'error' : 'hourglass_empty'}
          </span>
          <h2>
            {selectedRun?.status === 'failed'
              ? 'This benchmark did not complete'
              : selectedRun?.status === 'cancelled'
                ? 'This benchmark was cancelled'
                : 'This benchmark is still running'}
          </h2>
          <p>
            {selectedRun?.status === 'failed'
              ? 'Partial results are retained for diagnosis but are not shown as a complete algorithm comparison.'
              : 'Select a completed benchmark run to view its metrics and rankings.'}
          </p>
          <button
            type="button"
            className="control-button control-button--secondary"
            onClick={onViewBenchmarks}
          >
            View benchmark history
          </button>
        </section>
      ) : !results && !error ? (
        <section className="panel comparison-empty" role="status">
          <span className="material-symbols-outlined" aria-hidden="true">
            hourglass_empty
          </span>
          Loading persisted metrics for this completed benchmark…
        </section>
      ) : error && !results && selectedRun?.status === 'completed' ? (
        <section className="panel comparison-empty">
          Could not retrieve this run’s persisted results.
        </section>
      ) : results && configuration ? (
        <>
          <section className="comparison-summary-grid" aria-label="Benchmark summary">
            <SummaryCard
              icon="task_alt"
              label="Prepared workload"
              value={configuration.task_count.toLocaleString()}
              note={
                selectedRun.workload === 'manual'
                  ? `tasks · manual rate ${configuration.manual_tasks_per_minute?.toLocaleString() ?? 'unknown'}/min`
                  : `tasks · ${selectedRun.workload} profile`
              }
            />
            <SummaryCard
              icon="schedule"
              label="Simulation duration"
              value={`${selectedRun.duration_seconds}s`}
              note="configured simulated horizon"
            />
            <SummaryCard
              icon="checklist"
              label="Completed experiments"
              value={`${selectedRun.completed_experiments} / ${configuration.algorithm_order.length}`}
              note="all schedulers used the same arrivals"
            />
            <SummaryCard
              icon="hub"
              label="Compute nodes"
              value={`${configuration.node_configs.length}`}
              note={`${configuration.node_configs.filter((node) => node.node_type === 'fog').length} fog · ${configuration.node_configs.filter((node) => node.node_type === 'cloud').length} cloud`}
            />
            <SummaryCard
              icon="timer"
              label="Benchmark elapsed"
              value={`${selectedRun.elapsed_seconds.toFixed(2)}s`}
              note="wall-clock execution time"
            />
          </section>

          <section className="comparison-chart-grid" aria-label="Algorithm metric charts">
            <GroupedBarChart
              title="Response time"
              description="Average and median response among successfully completed tasks"
              results={results}
              unit="ms"
              series={[
                { key: 'average_response_time_ms', label: 'Average', color: seriesColors[0] },
                { key: 'median_response_time_ms', label: 'Median', color: seriesColors[1] },
              ]}
            />
            <GroupedBarChart
              title="Throughput"
              description="Tasks completed per configured simulated second"
              results={results}
              unit="tasks/s"
              series={[
                {
                  key: 'throughput_tasks_per_second',
                  label: 'Completed throughput',
                  color: seriesColors[0],
                  decimals: 3,
                },
              ]}
            />
            <GroupedBarChart
              title="Load distribution"
              description="Average processing utilization and population standard deviation across nodes"
              results={results}
              unit="%"
              series={[
                {
                  key: 'average_node_utilization_percent',
                  label: 'Average utilization',
                  color: seriesColors[0],
                },
                {
                  key: 'load_imbalance_percent',
                  label: 'Load imbalance',
                  color: seriesColors[2],
                },
              ]}
            />
            <GroupedBarChart
              title="Task outcomes"
              description="Counts from the same generated workload"
              results={results}
              unit="tasks"
              series={[
                { key: 'completed_count', label: 'Completed', color: seriesColors[1], decimals: 0 },
                { key: 'failed_count', label: 'Failed or rejected', color: '#ff858b', decimals: 0 },
                { key: 'deadline_misses', label: 'Deadline misses', color: seriesColors[2], decimals: 0 },
              ]}
            />
            <GroupedBarChart
              title="Cloud offloading"
              description="Tasks assigned to the cloud as a share of all generated tasks"
              results={results}
              unit="%"
              series={[
                {
                  key: 'cloud_offloading_rate_percent',
                  label: 'Cloud offloading',
                  color: seriesColors[3],
                },
              ]}
            />
            <GroupedBarChart
              title="Makespan"
              description="Simulated time from clock zero to the final task completion"
              results={results}
              unit="s"
              series={[
                { key: 'makespan_seconds', label: 'Makespan', color: seriesColors[4] },
              ]}
            />
          </section>

          <section className="panel metric-table-panel">
            <div className="panel-heading">
              <div>
                <h2>Metric comparison and per-metric ranking</h2>
                <p>Every value uses the same definition and unit across algorithms.</p>
              </div>
            </div>
            <div className="table-wrap comparison-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Metric</th>
                    <th>Unit</th>
                    <th>Preferred direction</th>
                    {results.map((result) => (
                      <th key={result.algorithm}>{result.algorithm_label}</th>
                    ))}
                    <th>Ranking</th>
                  </tr>
                </thead>
                <tbody>
                  {METRICS.map((metric) => {
                    const ranks = rankMetric(results, metric.key, metric.direction)
                    const rankedAlgorithms = [...ranks.entries()]
                      .sort((first, second) => first[1] - second[1])
                    const ranking = rankedAlgorithms
                      .map(([algorithm, rank]) => {
                        const result = results.find((item) => item.algorithm === algorithm)
                        return `#${rank % 1 ? rank.toFixed(1) : rank} ${result?.algorithm_label ?? algorithm}`
                      })
                      .join(' · ')
                    const rankingSummary = rankedAlgorithms
                      .slice(0, 3)
                      .map(([algorithm, rank]) => {
                        const result = results.find((item) => item.algorithm === algorithm)
                        return `#${rank % 1 ? rank.toFixed(1) : rank} ${result ? shortAlgorithmLabel(result.algorithm) : algorithm}`
                      })
                      .join(' · ')
                    const rankingCell =
                      rankedAlgorithms.length > 3
                        ? `${rankingSummary} · +${rankedAlgorithms.length - 3}`
                        : rankingSummary
                    const direction =
                      metric.direction === 'higher'
                        ? 'Higher'
                        : metric.direction === 'lower'
                          ? 'Lower'
                          : 'Informational'
                    return (
                      <tr key={metric.key}>
                        <th scope="row">{metric.label}</th>
                        <td>{metric.unit}</td>
                        <td>{direction}</td>
                        {results.map((result) => (
                          <td key={result.algorithm}>
                            {formatValue(
                              result[metric.key] as number | null,
                              '',
                              metric.decimals ?? 1,
                            )}
                          </td>
                        ))}
                        <td title={ranking || undefined}>
                          {rankingCell || 'Not measurable'}
                        </td>
                      </tr>
                    )
                  })}
                  <tr className="overall-score-row">
                    <th scope="row">Transparent overall rank score</th>
                    <td>0–100 points</td>
                    <td>Higher</td>
                    {results.map((result) => (
                      <td key={result.algorithm}>
                        {scores.get(result.algorithm)?.toFixed(1) ?? '—'}
                      </td>
                    ))}
                    <td title={[...results]
                      .sort(
                        (first, second) =>
                          (scores.get(second.algorithm) ?? 0) -
                          (scores.get(first.algorithm) ?? 0),
                      )
                      .map(
                        (result, index) =>
                          `#${index + 1} ${result.algorithm_label}`,
                      )
                      .join(' · ')}>
                      {[...results]
                        .sort(
                          (first, second) =>
                            (scores.get(second.algorithm) ?? 0) -
                            (scores.get(first.algorithm) ?? 0),
                        )
                        .slice(0, 3)
                        .map(
                          (result, index) =>
                            `#${index + 1} ${shortAlgorithmLabel(result.algorithm)}`,
                        )
                        .join(' · ')}
                      {results.length > 3 ? ` · +${results.length - 3}` : ''}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div className="metric-definitions">
              <h3>Definitions and scoring</h3>
              <ul>
                <li>
                  Response time is completion minus arrival; mean and median
                  use completed tasks only. Throughput is completed tasks
                  divided by the configured benchmark duration, not the drain
                  time.
                </li>
                <li>
                  Average node utilization is the mean of each node’s task
                  processing seconds divided by makespan. Load imbalance is the
                  population standard deviation of those per-node percentages.
                  Makespan includes queue drain after the final scheduled
                  arrival.
                </li>
                <li>
                  A deadline miss is either a task rejected for deadline
                  infeasibility or a completed task whose response exceeded
                  its deadline. Failed or rejected tasks include node-outage
                  interruptions and capacity or deadline rejections. Cloud
                  offloading is cloud-assigned tasks divided by generated
                  tasks; lower is only a fog-locality preference and is not
                  included in the overall score.
                </li>
                <li>
                  Overall score is the equally weighted mean of rank points
                  across completed count (higher), failed or rejected tasks (lower),
                  deadline misses (lower), average response time (lower),
                  throughput (higher), load imbalance (lower), and makespan
                  (lower). Best rank earns 100 and worst earns 0; ties share
                  rank points. Average utilization is reported but is not
                  scored because it is not universally better at higher
                  levels.
                </li>
                <li>
                  Min-Min batches tasks arriving within each one-simulated-
                  second window and repeatedly chooses the task-node pair
                  with the minimum estimated completion time.
                </li>
              </ul>
            </div>
          </section>
        </>
      ) : null}
    </div>
  )
}

function SummaryCard({
  icon,
  label,
  value,
  note,
}: {
  icon: string
  label: string
  value: string
  note: string
}) {
  return (
    <article className="panel comparison-summary-card">
      <span className="material-symbols-outlined" aria-hidden="true">{icon}</span>
      <span className="section-label">{label}</span>
      <strong>{value}</strong>
      <small>{note}</small>
    </article>
  )
}

export default AlgorithmComparison
