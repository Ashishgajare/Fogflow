import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from 'react'
import {
  configureFogNodes,
  generateTask,
  generateTasks,
  getHealth,
  getMetrics,
  getNodes,
  getTasks,
  pauseSimulation,
  resetSimulation,
  resumeSimulation,
  startSimulation,
  type Algorithm,
  type MetricsResponse,
  type NodeTelemetry,
  type TaskRecord,
  type TaskSpecification,
  type Workload,
} from './api'
import SmartCityMap, { type MapAllocation } from './SmartCityMap'
import AlgorithmComparison from './AlgorithmComparison'
import BenchmarkPage from './BenchmarkPage'
import {
  CITY_SENSORS,
  getCityComputeNodes,
  type CityDevice,
  type CitySensor,
} from './sceneData'
import './App.css'

export type ConnectionState = 'checking' | 'connected' | 'disconnected'

type WorkspacePage = 'overview' | 'comparison' | 'benchmarks' | 'settings'

interface NavigationItem {
  icon: string
  label: string
  target?: string
  page?: Exclude<WorkspacePage, 'overview'>
}

const navigation: NavigationItem[] = [
  { icon: 'grid_view', label: 'Overview', target: 'overview' },
  { icon: 'location_city', label: 'Smart City (Digital Twin)', target: 'digital-twin' },
  { icon: 'hub', label: 'Nodes (Fog & Cloud)', target: 'nodes' },
  { icon: 'queue', label: 'Tasks (Telemetry Queue)', target: 'tasks' },
  { icon: 'compare_arrows', label: 'Algorithm Comparison', page: 'comparison' },
  { icon: 'science', label: 'Experiments (Benchmarks)', page: 'benchmarks' },
  { icon: 'tune', label: 'Settings', page: 'settings' },
]

const nodeTones: Record<NodeTelemetry['node_type'], string> = {
  fog: 'emerald',
  cloud: 'blue',
}

const workloadProfiles: { name: Workload; tasksPerMinute: number | null }[] = [
  { name: 'light', tasksPerMinute: 60 },
  { name: 'normal', tasksPerMinute: 240 },
  { name: 'heavy', tasksPerMinute: 600 },
  { name: 'extreme', tasksPerMinute: 1200 },
  { name: 'manual', tasksPerMinute: null },
]
const MANUAL_TASKS_PER_MINUTE_MAX = 10_000

function Icon({ name, className = '' }: { name: string; className?: string }) {
  return (
    <span className={`material-symbols-outlined ${className}`} aria-hidden="true">
      {name}
    </span>
  )
}

function ConnectionBadge({ state }: { state: ConnectionState }) {
  const label =
    state === 'checking'
      ? 'Checking API'
      : state === 'connected'
        ? 'Connected'
        : 'Disconnected'

  return (
    <span
      className={`connection-badge connection-badge--${state}`}
      role="status"
      aria-live="polite"
    >
      <span className="status-dot" aria-hidden="true" />
      {label}
    </span>
  )
}

function formatNumber(value: number | null, digits = 1): string {
  return value === null ? '—' : value.toLocaleString(undefined, { maximumFractionDigits: digits })
}

function formatLatency(task: TaskRecord): string {
  const latency = task.actual_response_time_ms ?? task.estimated_response_time_ms
  if (latency !== null && latency !== undefined) {
    return `${formatNumber(latency)} ms${task.actual_response_time_ms === null ? ' est.' : ''}`
  }
  return '—'
}

function getNodeName(nodeId: string | null, nodes: NodeTelemetry[]): string {
  if (!nodeId) return 'Not assigned'
  return nodes.find((node) => node.id === nodeId)?.name ?? nodeId
}

function describeNodeCounts(nodes: NodeTelemetry[]): string {
  const fogCount = nodes.filter((node) => node.node_type === 'fog').length
  const cloudCount = nodes.filter((node) => node.node_type === 'cloud').length
  return `${fogCount} fog · ${cloudCount} cloud · ${nodes.length} total`
}

function Dashboard({
  connection,
  onConnectionChange,
}: {
  connection: ConnectionState
  onConnectionChange: (state: ConnectionState) => void
}) {
  const [nodes, setNodes] = useState<NodeTelemetry[]>([])
  const [tasks, setTasks] = useState<TaskRecord[]>([])
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [algorithm, setAlgorithm] = useState<Algorithm>('adaptive_fog_cloud')
  const [workload, setWorkload] = useState<Workload>('normal')
  const [manualTasksPerMinute, setManualTasksPerMinute] = useState(1200)
  const [fogNodeCount, setFogNodeCount] = useState(3)
  const [configuredFogNodeCount, setConfiguredFogNodeCount] = useState(3)
  const [selectedDevice, setSelectedDevice] = useState<CityDevice | null>(null)
  const [allocation, setAllocation] = useState<MapAllocation | null>(null)
  const [taskFailure, setTaskFailure] = useState<{ taskId: string | null; message: string } | null>(null)
  const [manualTaskOpen, setManualTaskOpen] = useState(false)
  const [manualSensorId, setManualSensorId] = useState('IOT-CAM-01')
  const [workspacePage, setWorkspacePage] = useState<
    WorkspacePage
  >('overview')
  const [activeNavigation, setActiveNavigation] = useState('Overview')
  const [comparisonBenchmarkId, setComparisonBenchmarkId] = useState<string | null>(
    null,
  )
  const [scrollTarget, setScrollTarget] = useState('overview')
  const [scrollRequest, setScrollRequest] = useState(0)
  const busyRef = useRef(false)

  const navigate = (item: NavigationItem) => {
    setActiveNavigation(item.label)
    if (item.page) {
      setWorkspacePage(item.page)
      return
    }
    setWorkspacePage('overview')
    setScrollTarget(item.target ?? 'overview')
    setScrollRequest((current) => current + 1)
  }

  useEffect(() => {
    if (workspacePage !== 'overview') return
    const targetId = scrollTarget
    const timer = setTimeout(() => {
      document.getElementById(targetId)?.scrollIntoView({
        behavior: 'smooth',
        block: 'start',
      })
    }, 0)
    return () => clearTimeout(timer)
  }, [scrollRequest, scrollTarget, workspacePage])

  const showComparison = (benchmarkId: string) => {
    setComparisonBenchmarkId(benchmarkId)
    setWorkspacePage('comparison')
    setActiveNavigation('Algorithm Comparison')
  }

  const refreshData = useCallback(
    async (signal?: AbortSignal): Promise<boolean> => {
      const [healthResult, nodesResult, tasksResult, metricsResult] =
        await Promise.allSettled([
          getHealth(signal),
          getNodes(signal),
          getTasks(50, 0, signal),
          getMetrics(signal),
        ])

      if (signal?.aborted) return false

      onConnectionChange(healthResult.status === 'fulfilled' ? 'connected' : 'disconnected')

      const failedRequest = [nodesResult, tasksResult, metricsResult].find(
        (result) => result.status === 'rejected',
      )
      if (failedRequest?.status === 'rejected') {
        setError(
          failedRequest.reason instanceof Error
            ? failedRequest.reason.message
            : 'Could not load simulation telemetry.',
        )
        setLoading(false)
        return false
      }

      if (
        nodesResult.status === 'fulfilled' &&
        tasksResult.status === 'fulfilled' &&
        metricsResult.status === 'fulfilled'
      ) {
        setNodes(nodesResult.value.nodes)
        const configuredFogCount = nodesResult.value.nodes.filter(
          (node) => node.node_type === 'fog',
        ).length
        setFogNodeCount(configuredFogCount)
        setConfiguredFogNodeCount(configuredFogCount)
        setTasks(tasksResult.value.tasks)
        setMetrics(metricsResult.value)
        setWorkload(metricsResult.value.workload)
        if (metricsResult.value.manual_tasks_per_minute !== null) {
          setManualTasksPerMinute(metricsResult.value.manual_tasks_per_minute)
        }
        setError(null)
      }

      if (healthResult.status === 'rejected' && !signal?.aborted) {
        setError(
          healthResult.reason instanceof Error
            ? healthResult.reason.message
            : 'The backend health check failed.',
        )
      }
      setLoading(false)
      return healthResult.status === 'fulfilled'
    },
    [onConnectionChange],
  )

  useEffect(() => {
    const controller = new AbortController()
    const initialLoad = setTimeout(() => {
      void refreshData(controller.signal)
    }, 0)
    return () => {
      clearTimeout(initialLoad)
      controller.abort()
    }
  }, [refreshData])

  useEffect(() => {
    if (metrics?.state !== 'running') return

    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    let cancelled = false
    const poll = async () => {
      await refreshData(controller.signal)
      if (!cancelled && !controller.signal.aborted) {
        timer = setTimeout(poll, 1500)
      }
    }
    timer = setTimeout(poll, 1500)

    return () => {
      cancelled = true
      controller.abort()
      clearTimeout(timer)
    }
  }, [metrics?.state, refreshData])

  const runAction = useCallback(
    async (action: () => Promise<string>) => {
      if (busyRef.current) return
      busyRef.current = true
      setBusy(true)
      setError(null)
      setNotice(null)
      try {
        const message = await action()
        setNotice(message)
        await refreshData()
      } catch (actionError) {
        setError(
          actionError instanceof Error ? actionError.message : 'The request failed.',
        )
      } finally {
        busyRef.current = false
        setBusy(false)
      }
    },
    [refreshData],
  )

  const start = () =>
    runAction(async () => {
      const response = await startSimulation(
        workload,
        algorithm,
        workload === 'manual' ? manualTasksPerMinute : undefined,
      )
      setMetrics((current) =>
        current
          ? {
              ...current,
              state: response.simulation.state,
              algorithm: response.simulation.algorithm,
              workload: response.simulation.workload,
              manual_tasks_per_minute: response.simulation.manual_tasks_per_minute,
            }
          : current,
      )
      return `Simulation started: ${algorithmLabel(algorithm)} · ${workloadLabel(workload)} workload.`
    })

  const pause = () =>
    runAction(async () => {
      const response = await pauseSimulation()
      setMetrics((current) =>
        current ? { ...current, state: response.simulation.state } : current,
      )
      return 'Simulation paused.'
    })

  const resume = () =>
    runAction(async () => {
      const response = await resumeSimulation()
      setMetrics((current) =>
        current ? { ...current, state: response.simulation.state } : current,
      )
      return 'Simulation resumed.'
    })

  const reset = () =>
    runAction(async () => {
      const response = await resetSimulation()
      setAllocation(null)
      setTasks([])
      setTaskFailure(null)
      setSelectedDevice(null)
      setMetrics((current) =>
        current
          ? {
              ...current,
              state: response.simulation.state,
              algorithm: response.simulation.algorithm,
              workload: response.simulation.workload,
              simulation_clock_seconds: 0,
              task_count: 0,
              tasks_completed: 0,
              tasks_running: 0,
              tasks_queued: 0,
              tasks_failed: 0,
              tasks_rejected: 0,
              average_response_time_ms: null,
              task_counts_by_sensor: {},
              manual_tasks_per_minute: response.simulation.manual_tasks_per_minute,
            }
          : current,
      )
      setAlgorithm(response.simulation.algorithm)
      setWorkload(response.simulation.workload)
      setManualTasksPerMinute(response.simulation.manual_tasks_per_minute ?? 1200)
      return 'Simulation reset. The previous run remains stored in SQLite.'
    })

  const resetFromSettings = () => {
    if (
      !window.confirm(
        'Reset the live simulation and start a fresh run? The previous live run will remain in history. Benchmark history will not be changed.',
      )
    ) {
      return
    }
    void reset()
  }

  const applyFogNodeCount = () =>
    runAction(async () => {
      const response = await configureFogNodes(fogNodeCount)
      setNodes(response.nodes)
      setConfiguredFogNodeCount(fogNodeCount)
      return `Configured ${fogNodeCount} fog nodes for the Mumbai simulation.`
    })

  const generate = () =>
    runAction(async () => {
      if (metrics?.state === 'idle' || metrics?.state === 'reset' || !metrics) {
        const started = await startSimulation(
          workload,
          algorithm,
          workload === 'manual' ? manualTasksPerMinute : undefined,
        )
        setMetrics((current) =>
          current
            ? {
                ...current,
                state: started.simulation.state,
                algorithm: started.simulation.algorithm,
                workload: started.simulation.workload,
                manual_tasks_per_minute: started.simulation.manual_tasks_per_minute,
              }
            : current,
        )
      }
      const result = await generateTasks(6)
      return `Generated ${result.generated} tasks using ${algorithmLabel(algorithm)}.`
    })

  const generateFromSensor = useCallback(
    async (
      sensor: CitySensor,
      taskSpecification: TaskSpecification = sensor.task,
    ) => {
      if (busyRef.current) return
      busyRef.current = true
      setBusy(true)
      setError(null)
      setNotice(null)
      setTaskFailure(null)
      setAllocation(null)
      let selectedAlgorithm = metrics?.algorithm ?? algorithm
      try {
        if (
          !metrics ||
          metrics.state === 'idle' ||
          metrics.state === 'reset'
        ) {
          const started = await startSimulation(
            workload,
            algorithm,
            workload === 'manual' ? manualTasksPerMinute : undefined,
          )
          selectedAlgorithm = started.simulation.algorithm
          setMetrics((current) =>
            current
              ? {
                  ...current,
                  state: started.simulation.state,
                  algorithm: started.simulation.algorithm,
                  workload: started.simulation.workload,
                  manual_tasks_per_minute: started.simulation.manual_tasks_per_minute,
                }
              : current,
          )
        }

        const response = await generateTask(taskSpecification)
        const task = response.tasks?.[0]
        if (
          response.generated !== 1 ||
          !task ||
          typeof task.task_id !== 'string' ||
          typeof task.source_sensor_id !== 'string' ||
          typeof task.allocation_explanation !== 'string' ||
          !['pending', 'queued', 'running', 'completed', 'failed', 'rejected'].includes(task.status)
        ) {
          throw new Error('The backend returned an invalid task allocation response.')
        }

        const destination = task.assigned_node_id
          ? getCityComputeNodes(nodes).find((node) => node.backendNodeId === task.assigned_node_id)
          : undefined
        if (
          task.status === 'failed' ||
          task.status === 'rejected' ||
          !task.assigned_node_id ||
          !destination
        ) {
          const message =
            task.allocation_explanation || 'The scheduler did not assign this task to a node.'
          setTaskFailure({ taskId: task.task_id, message })
          setError(message)
          await refreshData()
          return
        }

        setAllocation({ task, algorithm: selectedAlgorithm })
        setNotice(
          `Task ${task.task_id} allocated to ${destination.name} by ${algorithmLabel(selectedAlgorithm)}.`,
        )
        await refreshData()
      } catch (taskError) {
        const message =
          taskError instanceof Error ? taskError.message : 'The sensor task request failed.'
        setTaskFailure({ taskId: null, message })
        setError(message)
      } finally {
        busyRef.current = false
        setBusy(false)
      }
    },
    [algorithm, manualTasksPerMinute, metrics, nodes, refreshData, workload],
  )

  const generateManualTask = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const sensor = CITY_SENSORS.find((entry) => entry.id === manualSensorId)
    if (!sensor) {
      setError('Select one of the configured Mumbai demonstration sensors.')
      return
    }

    const formData = new FormData(event.currentTarget)
    const deadlineValue = String(formData.get('deadline_seconds') ?? '').trim()
    const specification: TaskSpecification = {
      source_sensor_id: sensor.id,
      task_type: String(formData.get('task_type') ?? '').trim(),
      cpu_demand: Number(formData.get('cpu_demand')),
      data_size_mb: Number(formData.get('data_size_mb')),
      priority: Number(formData.get('priority')),
      deadline_seconds: deadlineValue ? Number(deadlineValue) : null,
    }
    setSelectedDevice(sensor)
    setManualTaskOpen(false)
    void generateFromSensor(sensor, specification)
  }

  const clearSceneResult = () => {
    setAllocation(null)
    setTaskFailure(null)
  }

  const state = metrics?.state ?? 'idle'
  const tasksGenerated = metrics?.task_count ?? 0
  const tasksPending = metrics?.tasks_queued ?? 0
  const manualSensor =
    CITY_SENSORS.find((sensor) => sensor.id === manualSensorId) ?? CITY_SENSORS[0]
  const activeNodes = nodes.filter((node) => node.online && node.active_task_id !== null).length
  const onlineNodes = nodes.filter((node) => node.online).length
  const fogNodeConfigurationLocked =
    busy || state !== 'idle' || tasksGenerated > 0 || connection !== 'connected'
  const validManualWorkload =
    Number.isInteger(manualTasksPerMinute) &&
    manualTasksPerMinute >= 1 &&
    manualTasksPerMinute <= MANUAL_TASKS_PER_MINUTE_MAX
  const events = tasks
    .filter((task) => task.assigned_node_id || task.status === 'rejected')
    .slice(0, 5)

  return (
    <div className="dashboard">
      <header className="topbar">
        <a
          className="brand"
          href="#overview"
          aria-label="FogFlow overview"
          onClick={(event) => {
            event.preventDefault()
            navigate(navigation[0])
          }}
        >
          <span className="brand-mark"><Icon name="cloud_sync" /></span>
          <span className="brand-copy">
            <span className="brand-title">
              FogFlow <span className="version-tag">v2.4</span>
            </span>
            <span className="brand-subtitle">Intelligent Fog–Cloud Load Balancing System</span>
          </span>
        </a>
        <div className="topbar-right">
          <span className="lab-tag"><Icon name="school" />IoTLab</span>
          <ConnectionBadge state={connection} />
        </div>
      </header>

      <div className="dashboard-layout">
        <aside className="sidebar" aria-label="Main navigation">
          <div className="sidebar-content">
            <p className="section-label">Telemetry Core</p>
            <nav className="navigation">
              {navigation.map((item) => (
                <button
                  type="button"
                  className={`navigation-link${activeNavigation === item.label ? ' navigation-link--active' : ''}`}
                  key={item.label}
                  aria-current={activeNavigation === item.label ? 'page' : undefined}
                  onClick={() => navigate(item)}
                >
                  <Icon name={item.icon} /><span>{item.label}</span>
                </button>
              ))}
            </nav>
          </div>
          <div className="sidebar-footer">
            <div className="cluster-card">
              <span className="section-label">Backend API</span>
              <ConnectionBadge state={connection} />
              <span className="cluster-caption">Health endpoint status</span>
            </div>
          </div>
        </aside>

        <main className="main-content" id="overview">
          {workspacePage === 'benchmarks' ? (
            <BenchmarkPage
              connected={connection === 'connected'}
              loadingNodes={loading}
              nodes={nodes}
              onViewComparison={showComparison}
            />
          ) : workspacePage === 'comparison' ? (
            <AlgorithmComparison
              benchmarkId={comparisonBenchmarkId}
              onBack={() => navigate(navigation[0])}
              onViewBenchmarks={() => navigate(navigation[5])}
            />
          ) : workspacePage === 'settings' ? (
            <div className="dashboard-content settings-content">
              <section className="workspace-heading">
                <div>
                  <span className="section-label">FogFlow configuration</span>
                  <h1>Settings</h1>
                  <p>Configure the next live simulation. Current run settings are not changed retroactively.</p>
                </div>
              </section>
              <section className="panel settings-panel">
                <div className="settings-field">
                  <label htmlFor="settings-algorithm">Live scheduling algorithm</label>
                  <select
                    id="settings-algorithm"
                    value={algorithm}
                    onChange={(event) => setAlgorithm(event.target.value as Algorithm)}
                    disabled={busy || metrics?.state === 'running' || metrics?.state === 'paused'}
                  >
                    <optgroup label="Group 1 · Current algorithms">
                    <option value="round_robin">Round Robin</option>
                    <option value="weighted_round_robin">Weighted Round Robin</option>
                    <option value="least_loaded">Least Loaded</option>
                    <option value="adaptive_fog_cloud">Weighted Sum Model (WSM)</option>
                    </optgroup>
                    <optgroup label="Group 2 · Advanced algorithms">
                      <option value="fuzzy_logic">Fuzzy Logic</option>
                      <option value="topsis">TOPSIS</option>
                      <option value="ant_colony">Ant Colony Optimization (ACO)</option>
                      <option value="particle_swarm">Particle Swarm Optimization (PSO)</option>
                    </optgroup>
                  </select>
                  <small>Used when starting the next ordinary simulation run.</small>
                  <small>
                    WSM minimizes a normalized weighted sum of response time,
                    projected utilization, and estimated completion time.
                  </small>
                </div>
                <div className="settings-field">
                  <label htmlFor="settings-workload">Automatic live workload</label>
                  <select
                    id="settings-workload"
                    value={workload}
                    onChange={(event) => setWorkload(event.target.value as Workload)}
                    disabled={busy || metrics?.state === 'running' || metrics?.state === 'paused'}
                  >
                    <option value="light">Light</option>
                    <option value="normal">Normal</option>
                    <option value="heavy">Heavy</option>
                    <option value="extreme">Extreme</option>
                    <option value="manual">Manual</option>
                  </select>
                  <small>Tasks are generated automatically while a live simulation runs.</small>
                  {workload === 'manual' && (
                    <label>
                      <span>Manual arrival rate · tasks per minute</span>
                      <input
                        type="number"
                        min="1"
                        max={MANUAL_TASKS_PER_MINUTE_MAX}
                        step="1"
                        value={manualTasksPerMinute}
                        onChange={(event) => setManualTasksPerMinute(Number(event.target.value))}
                        disabled={busy || metrics?.state === 'running' || metrics?.state === 'paused'}
                      />
                    </label>
                  )}
                </div>
                <div className="settings-field">
                  <span>Backend API health</span>
                  <ConnectionBadge state={connection} />
                  <small>The indicator reflects the real Flask /api/health endpoint.</small>
                </div>
                <div className="settings-field">
                  <span>Benchmark defaults</span>
                  <strong>60 simulated seconds · Normal deterministic workload</strong>
                  <small>Duration and workload can be changed before each benchmark.</small>
                </div>
                <div className="settings-field settings-field--recovery">
                  <span>Simulation recovery</span>
                  <small>
                    If the live dashboard or simulation gets stuck, reset the active
                    live run and start fresh. The previous live run and benchmark
                    history are preserved.
                  </small>
                  <button
                    className="control-button control-button--secondary"
                    type="button"
                    onClick={resetFromSettings}
                    disabled={busy}
                  >
                    <Icon name="restart_alt" />Reset live simulation
                  </button>
                </div>
              </section>
            </div>
          ) : (
          <div className="dashboard-content">
            <section className="status-strip" aria-label="Simulation controls">
              <div className="control-status-row">
                <div className="status-strip-title">
                  <span className={`status-pulse status-pulse--${connection}`} aria-hidden="true" />
                  <span className="section-label">Simulation</span>
                  <span className={`simulation-state simulation-state--${state}`}>
                    {state === 'idle' ? 'Idle' : state}
                  </span>
                  <span className="section-label control-divider">· API</span>
                  <ConnectionBadge state={connection} />
                </div>
                <div className="control-selectors">
                  <label>
                    <span className="section-label">Algorithm</span>
                    <select
                      value={algorithm}
                      onChange={(event) => setAlgorithm(event.target.value as Algorithm)}
                      disabled={busy || state !== 'idle'}
                    >
                      <optgroup label="Group 1 · Current algorithms">
                      <option value="round_robin">Round Robin</option>
                      <option value="weighted_round_robin">Weighted Round Robin</option>
                      <option value="least_loaded">Least Loaded</option>
                      <option value="adaptive_fog_cloud">Weighted Sum Model (WSM)</option>
                      </optgroup>
                      <optgroup label="Group 2 · Advanced algorithms">
                        <option value="fuzzy_logic">Fuzzy Logic</option>
                        <option value="topsis">TOPSIS</option>
                        <option value="ant_colony">Ant Colony Optimization (ACO)</option>
                        <option value="particle_swarm">Particle Swarm Optimization (PSO)</option>
                      </optgroup>
                    </select>
                  </label>
                  <label>
                    <span className="section-label">Workload</span>
                    <select
                      value={workload}
                      onChange={(event) => setWorkload(event.target.value as Workload)}
                      disabled={busy || state !== 'idle'}
                    >
                      <option value="light">Light</option>
                      <option value="normal">Normal</option>
                      <option value="heavy">Heavy</option>
                      <option value="extreme">Extreme</option>
                      <option value="manual">Manual</option>
                    </select>
                  </label>
                  {state === 'running' ? (
                    <button className="control-button control-button--secondary" onClick={pause} disabled={busy}>
                      <Icon name="pause" />Pause
                    </button>
                  ) : state === 'paused' ? (
                    <button className="control-button control-button--primary" onClick={resume} disabled={busy}>
                      <Icon name="play_arrow" />Resume
                    </button>
                  ) : (
                    <button className="control-button control-button--primary" onClick={start} disabled={busy || connection !== 'connected' || (workload === 'manual' && !validManualWorkload)}>
                      <Icon name="play_arrow" />Start
                    </button>
                  )}
                  <button className="control-button control-button--secondary" onClick={reset} disabled={busy}>
                    <Icon name="restart_alt" />Reset
                  </button>
                  <button className="control-button control-button--generate" onClick={generate} disabled={busy || connection !== 'connected' || (workload === 'manual' && !validManualWorkload)}>
                    <Icon name="add" />Generate Tasks
                  </button>
                  <button
                    className="control-button control-button--secondary"
                    type="button"
                    onClick={() => setManualTaskOpen((open) => !open)}
                    disabled={busy || connection !== 'connected'}
                    aria-expanded={manualTaskOpen}
                    aria-controls="manual-task-form"
                  >
                    <Icon name="edit_note" />Add task manually
                  </button>
                </div>
              </div>
              <p className="auto-generation-note">
                Automatic sensor requests are generated while the simulation is running.
              </p>
              <div className="simulation-config">
                <div className="fog-node-slider">
                  <label htmlFor="fog-node-count">
                    <span> Mumbai fog nodes</span>
                    <strong>{fogNodeCount} / 75</strong>
                  </label>
                  <input
                    id="fog-node-count"
                    type="range"
                    min="1"
                    max="75"
                    step="1"
                    value={fogNodeCount}
                    onChange={(event) => setFogNodeCount(Number(event.target.value))}
                    disabled={fogNodeConfigurationLocked}
                  />
                  <small>
                    {fogNodeConfigurationLocked
                      ? 'Reset the run to change the node count.'
                      : 'Choose 1–75 fog nodes; one cloud server is included.'}
                  </small>
                  <button
                    type="button"
                    className="control-button control-button--secondary"
                    onClick={applyFogNodeCount}
                    disabled={fogNodeConfigurationLocked || fogNodeCount === configuredFogNodeCount}
                  >
                    Apply fog nodes
                  </button>
                </div>
                <div className="workload-rates" aria-label="Automatic task arrival rates">
                  <span className="section-label">Mumbai task arrivals · per minute</span>
                  <div className="workload-rate-list">
                    {workloadProfiles.map((profile) => (
                      <span
                        className={workload === profile.name ? 'workload-rate workload-rate--selected' : 'workload-rate'}
                        key={profile.name}
                      >
                        <span>{workloadLabel(profile.name)}</span>
                        <strong>{profile.tasksPerMinute ?? manualTasksPerMinute}</strong>
                      </span>
                    ))}
                  </div>
                  {workload === 'manual' && (
                    <label className="manual-workload-input">
                      <span>Manual rate · tasks per minute (1–10,000)</span>
                      <input
                        type="number"
                        min="1"
                        max={MANUAL_TASKS_PER_MINUTE_MAX}
                        step="1"
                        value={manualTasksPerMinute}
                        onChange={(event) => setManualTasksPerMinute(Number(event.target.value))}
                        disabled={busy || state !== 'idle'}
                        aria-invalid={!validManualWorkload}
                      />
                      {!validManualWorkload && (
                        <small role="alert">Enter a whole number from 1 to 10,000.</small>
                      )}
                    </label>
                  )}
                  {workload !== 'manual' && (
                    <small>Extreme sends 20 tasks/second (1,200/minute), the highest preset.</small>
                  )}
                </div>
              </div>
              {(error || notice) && (
                <p className={`feedback-message${error ? ' feedback-message--error' : ''}`} role={error ? 'alert' : 'status'}>
                  <Icon name={error ? 'error' : 'check_circle'} />
                  {error ?? notice}
                  {error && (
                    <button className="feedback-retry" onClick={() => void refreshData()}>
                      Retry
                    </button>
                  )}
                </p>
              )}
              {manualTaskOpen && (
                <form
                  id="manual-task-form"
                  className="manual-task-form"
                  key={manualSensorId}
                  onSubmit={generateManualTask}
                >
                  <label>
                    <span>Source sensor</span>
                    <select
                      name="source_sensor_id"
                      value={manualSensorId}
                      onChange={(event) => setManualSensorId(event.target.value)}
                    >
                      {CITY_SENSORS.map((sensor) => (
                        <option key={sensor.id} value={sensor.id}>
                          {sensor.id} · {sensor.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>Task type</span>
                    <input
                      name="task_type"
                      defaultValue={manualSensor?.task.task_type}
                      required
                    />
                  </label>
                  <label>
                    <span>CPU demand</span>
                    <input
                      name="cpu_demand"
                      type="number"
                      min="0.01"
                      step="any"
                      defaultValue={manualSensor?.task.cpu_demand}
                      required
                    />
                  </label>
                  <label>
                    <span>Data size (MB)</span>
                    <input
                      name="data_size_mb"
                      type="number"
                      min="0"
                      step="any"
                      defaultValue={manualSensor?.task.data_size_mb}
                      required
                    />
                  </label>
                  <label>
                    <span>Priority</span>
                    <input
                      name="priority"
                      type="number"
                      min="1"
                      max="10"
                      step="1"
                      defaultValue={manualSensor?.task.priority}
                      required
                    />
                  </label>
                  <label>
                    <span>Deadline (seconds, optional)</span>
                    <input
                      name="deadline_seconds"
                      type="number"
                      min="0.001"
                      step="any"
                      defaultValue={manualSensor?.task.deadline_seconds ?? ''}
                    />
                  </label>
                  <div className="manual-task-actions">
                    <button type="submit" className="control-button control-button--generate" disabled={busy || connection !== 'connected'}>
                      <Icon name="add_task" />Create task
                    </button>
                    <button type="button" className="control-button control-button--secondary" onClick={() => setManualTaskOpen(false)}>
                      Cancel
                    </button>
                  </div>
                </form>
              )}
            </section>

            <section className="kpi-grid" aria-label="Telemetry summary">
              <Kpi icon="add_task" label="Tasks Generated" value={loading ? '…' : tasksGenerated.toLocaleString()} note={tasksGenerated ? 'Tasks in this simulation run' : 'Generate tasks to begin'} />
              <Kpi icon="task_alt" label="Tasks Completed" value={loading ? '…' : (metrics?.tasks_completed ?? 0).toLocaleString()} note="Completed by simulated nodes" />
              <Kpi icon="pending_actions" label="Tasks Pending" value={loading ? '…' : tasksPending.toLocaleString()} note={`${metrics?.tasks_running ?? 0} currently running`} />
              <Kpi icon="error_outline" label="Tasks Failed" value={loading ? '…' : (metrics?.tasks_failed ?? 0).toLocaleString()} note="Interrupted by simulated node outages" />
              <Kpi icon="block" label="Tasks Rejected" value={loading ? '…' : (metrics?.tasks_rejected ?? 0).toLocaleString()} note="No node met availability, capacity, or deadline checks" />
            </section>

            <section className="primary-grid" aria-label="City twin and compute nodes">
              <article className="panel twin-panel" id="digital-twin">
                <div className="panel-heading twin-heading">
                  <div className="heading-with-icon">
                    <Icon name="location_city" className="heading-icon" />
                    <div><h2>Smart City Digital Twin</h2><p>MapCN geographic telemetry · Mumbai</p></div>
                  </div>
                  <span className="future-tag">
                    9 sensors · {nodes.length ? describeNodeCounts(nodes) : 'Loading node configuration'}
                  </span>
                </div>
                <div className="digital-twin-viewport" id="city-map-viewport" role="region" aria-label="Interactive Mumbai smart city map">
                  <SmartCityMap
                    nodes={nodes}
                    metrics={metrics}
                    selectedDevice={selectedDevice}
                    allocation={allocation}
                    taskFailure={taskFailure}
                    busy={busy}
                    canGenerate={connection === 'connected' && (workload !== 'manual' || validManualWorkload)}
                    onSelectDevice={setSelectedDevice}
                    onClearSelection={() => setSelectedDevice(null)}
                    onGenerateTask={generateFromSensor}
                    onClearRoute={clearSceneResult}
                  />
                </div>
              </article>

              <article className="panel nodes-panel" id="nodes">
                <div className="panel-heading">
                  <div className="heading-with-icon">
                    <Icon name="hub" className="heading-icon" />
                    <div>
                      <h2>Compute Nodes</h2>
                      <p>
                        {nodes.length
                          ? `${describeNodeCounts(nodes)} · ${onlineNodes} online · ${activeNodes} processing`
                          : 'Loading configured nodes…'}
                      </p>
                    </div>
                  </div>
                  <span className="panel-count" aria-label={`${activeNodes} active nodes`}>{activeNodes} active</span>
                </div>
                {loading && nodes.length === 0 ? (
                  <p className="data-placeholder">Loading configured nodes…</p>
                ) : (
                  <div className="node-list">
                    {nodes.map((node) => (
                      <NodeCard key={node.id} node={node} />
                    ))}
                  </div>
                )}
              </article>
            </section>

            <section className="secondary-grid" aria-label="Performance and task allocations">
              <article className="panel performance-panel">
                <div className="panel-heading">
                  <div className="heading-with-icon">
                    <Icon name="show_chart" className="heading-icon" />
                    <div><h2>Simulation Performance</h2><p>Measured values from completed tasks</p></div>
                  </div>
                </div>
                <div className="performance-stats">
                  <div>
                    <span className="section-label">Average response</span>
                    <strong>{formatNumber(metrics?.average_response_time_ms ?? null)}{metrics?.average_response_time_ms == null ? '' : ' ms'}</strong>
                  </div>
                  <div>
                    <span className="section-label">Simulation clock</span>
                    <strong>{formatNumber(metrics?.simulation_clock_seconds ?? 0, 2)} s</strong>
                  </div>
                </div>
                <div className="events-heading">
                  <span className="section-label">Recent scheduling events</span>
                  <span className="awaiting-label">{events.length ? `${events.length} task decisions` : 'No events yet'}</span>
                </div>
                {events.length ? (
                  <ul className="event-list">
                    {events.slice(0, 4).map((task) => (
                      <li key={task.task_id}>
                        <span className={`event-mark event-mark--${task.status}`} aria-hidden="true" />
                        <span className="event-copy">
                          <strong>{task.task_id}</strong>
                          <span>{task.allocation_explanation}</span>
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="events-empty"><Icon name="event_note" />Scheduling decisions will appear here.</div>
                )}
              </article>

              <article className="panel tasks-panel" id="tasks">
                <div className="panel-heading">
                  <div className="heading-with-icon">
                    <Icon name="queue" className="heading-icon" />
                    <div><h2>Recent Task Allocations</h2><p>{metrics?.task_count ?? 0} task records · latest 50</p></div>
                  </div>
                  <span className="awaiting-label">{state === 'running' ? 'Refreshing live' : 'On demand'}</span>
                </div>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr><th>Task ID</th><th>Source Sensor</th><th>Type</th><th>Target Node</th><th>Latency</th><th>Status</th></tr>
                    </thead>
                    <tbody>
                      {tasks.length ? tasks.map((task) => (
                        <tr key={task.task_id}>
                          <td className="task-id">{task.task_id}</td>
                          <td>{task.source_sensor_id}</td>
                          <td>{task.task_type}</td>
                          <td>{getNodeName(task.assigned_node_id, nodes)}</td>
                          <td>{formatLatency(task)}</td>
                          <td><span className={`task-status task-status--${task.status}`}>{task.status}</span></td>
                        </tr>
                      )) : (
                        <tr>
                          <td colSpan={6}>
                            <div className="table-empty">
                              <Icon name={loading ? 'hourglass_empty' : 'inbox'} />
                              <span>{loading ? 'Loading task records…' : 'No task allocations received'}</span>
                              <small>{loading ? 'Fetching this run from the backend.' : 'Generate a workload to see real scheduler decisions.'}</small>
                              {!loading && <button className="empty-generate" onClick={generate} disabled={busy || connection !== 'connected'}><Icon name="add" />Generate Tasks</button>}
                            </div>
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
                <p className="table-caption">Latency is measured response time for completed tasks and estimated response time for active or queued tasks.</p>
              </article>
            </section>
          </div>
          )}
        </main>
      </div>
    </div>
  )
}

function Kpi({
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
    <article className="panel kpi-card">
      <div className="kpi-heading"><span className="section-label">{label}</span><Icon name={icon} className="kpi-icon" /></div>
      <div className="kpi-value-row"><span className="kpi-value">{value}</span></div>
      <p className="kpi-note">{note}</p>
    </article>
  )
}

function NodeCard({ node }: { node: NodeTelemetry }) {
  const tone = nodeTones[node.node_type]
  const status = !node.online
    ? node.faulted ? 'REPAIRING' : 'OFFLINE'
    : node.active_task_id ? 'PROCESSING' : 'IDLE'
  return (
    <article className={`node-card${node.online ? '' : ' node-card--offline'}`}>
      <div className={`node-indicator node-indicator--${tone}`} aria-hidden="true" />
      <div className="node-main">
        <div className="node-title-row">
          <h3>{node.name}</h3>
          <span className={`node-status${node.online ? ' node-status--online' : ' node-status--offline'}`}>{status}</span>
        </div>
        <p>{node.node_type === 'fog' ? 'Fog edge unit' : 'Cloud server'} · {formatNumber(node.network_latency_ms)} ms network</p>
        <div className="node-metrics">
          <span>Utilization <strong>{formatNumber(node.projected_utilization)}%</strong></span>
          <span>Queue <strong>{node.queue_length}</strong></span>
          <span>Load <strong>{formatNumber(node.projected_workload)}/{formatNumber(node.processing_capacity)} CPU</strong></span>
        </div>
        <div className="load-track" role="progressbar" aria-label={`${node.name} projected utilization`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.min(node.projected_utilization, 100)}>
          <span className={node.projected_utilization >= 85 ? 'load-fill--high' : ''} style={{ width: `${Math.min(node.projected_utilization, 100)}%` }} />
        </div>
      </div>
    </article>
  )
}

function algorithmLabel(algorithm: Algorithm): string {
  switch (algorithm) {
    case 'round_robin':
      return 'Round Robin'
    case 'weighted_round_robin':
      return 'Weighted Round Robin'
    case 'least_loaded':
      return 'Least Loaded'
    case 'adaptive_fog_cloud':
      return 'Weighted Sum Model (WSM)'
    case 'fuzzy_logic':
      return 'Fuzzy Logic'
    case 'topsis':
      return 'TOPSIS'
    case 'ant_colony':
      return 'Ant Colony Optimization (ACO)'
    case 'particle_swarm':
      return 'Particle Swarm Optimization (PSO)'
  }
}

function workloadLabel(workload: Workload): string {
  return workload[0].toUpperCase() + workload.slice(1)
}

export default Dashboard
