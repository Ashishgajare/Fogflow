import { useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import type { FeatureCollection, Point } from 'geojson'
import type { GeoJSONSource } from 'maplibre-gl'
import type { Algorithm, MetricsResponse, NodeTelemetry, TaskRecord } from './api'
import {
  Map as MapCN,
  MapControls,
  MapMarker,
  MapRoute,
  MarkerContent,
  MarkerTooltip,
  RouteMarker,
  RouteProgress,
  useMap,
  type MapRef,
} from './components/ui/map'
import {
  CITY_CENTER,
  CITY_SENSORS,
  getCityComputeNodes,
  type CityDevice,
  type CitySensor,
} from './sceneData'
import './SmartCityMap.css'

export interface MapAllocation {
  task: TaskRecord
  algorithm: Algorithm
}

interface SmartCityMapProps {
  nodes: NodeTelemetry[]
  metrics: MetricsResponse | null
  selectedDevice: CityDevice | null
  allocation: MapAllocation | null
  taskFailure: { taskId: string | null; message: string } | null
  busy: boolean
  canGenerate: boolean
  onSelectDevice: (device: CityDevice) => void
  onClearSelection: () => void
  onGenerateTask: (sensor: CitySensor) => void
  onClearRoute: () => void
}

type HeatPointProperties = {
  request_count: number
}

const CITY_ZOOM = 9.2
const SENSOR_HEAT_SOURCE = 'fogflow-sensor-request-heat'
const SENSOR_HEAT_LAYER = 'fogflow-sensor-request-heat-layer'
const MAP_STYLES = {
  default: undefined,
  openstreetmap: 'https://tiles.openfreemap.org/styles/bright',
  openstreetmap3d: 'https://tiles.openfreemap.org/styles/liberty',
} as const
type MapStyle = keyof typeof MAP_STYLES

const sensorById = new Map(CITY_SENSORS.map((sensor) => [sensor.id, sensor]))

function buildHeatmapData(
  counts: Record<string, number>,
): FeatureCollection<Point, HeatPointProperties> {
  return {
    type: 'FeatureCollection',
    features: CITY_SENSORS.flatMap((sensor) => {
      const requestCount = counts[sensor.id] ?? 0
      if (requestCount <= 0) return []
      return [{
        type: 'Feature' as const,
        properties: { request_count: requestCount },
        geometry: {
          type: 'Point' as const,
          coordinates: [sensor.longitude, sensor.latitude],
        },
      }]
    }),
  }
}

function RequestHeatmap({
  counts,
  visible,
}: {
  counts: Record<string, number>
  visible: boolean
}) {
  const { map, isLoaded } = useMap()
  const data = useMemo(() => buildHeatmapData(counts), [counts])

  useEffect(() => {
    if (!map || !isLoaded) return

    const syncLayer = () => {
      if (!map.isStyleLoaded()) return

      if (!map.getSource(SENSOR_HEAT_SOURCE)) {
        map.addSource(SENSOR_HEAT_SOURCE, { type: 'geojson', data })
      }
      if (!map.getLayer(SENSOR_HEAT_LAYER)) {
        map.addLayer({
          id: SENSOR_HEAT_LAYER,
          type: 'heatmap',
          source: SENSOR_HEAT_SOURCE,
          maxzoom: 17,
          paint: {
            'heatmap-weight': [
              'interpolate',
              ['linear'],
              ['get', 'request_count'],
              0, 0,
              1, 0.45,
              5, 0.7,
              20, 1,
            ],
            'heatmap-intensity': [
              'interpolate',
              ['linear'],
              ['zoom'],
              9, 0.7,
              15, 1.25,
            ],
            'heatmap-radius': [
              'interpolate',
              ['linear'],
              ['zoom'],
              9, 22,
              15, 38,
            ],
            'heatmap-color': [
              'interpolate',
              ['linear'],
              ['heatmap-density'],
              0, 'rgba(34, 211, 238, 0)',
              0.2, 'rgba(34, 211, 238, 0.38)',
              0.45, 'rgba(59, 130, 246, 0.55)',
              0.68, 'rgba(245, 158, 11, 0.72)',
              1, 'rgba(239, 68, 68, 0.86)',
            ],
            'heatmap-opacity': 0.8,
          },
        })
      }

      const source = map.getSource(SENSOR_HEAT_SOURCE) as GeoJSONSource | undefined
      source?.setData(data)
      map.setLayoutProperty(
        SENSOR_HEAT_LAYER,
        'visibility',
        visible ? 'visible' : 'none',
      )
    }

    map.on('style.load', syncLayer)
    syncLayer()
    return () => {
      map.off('style.load', syncLayer)
    }
  }, [data, isLoaded, map, visible])

  return null
}

function routeProgressFor(animationStart: number) {
  return Math.min(1, (performance.now() - animationStart) / 2600)
}

function SmartCityMap({
  nodes,
  metrics,
  selectedDevice,
  allocation,
  taskFailure,
  busy,
  canGenerate,
  onSelectDevice,
  onClearSelection,
  onGenerateTask,
  onClearRoute,
}: SmartCityMapProps) {
  const mapRef = useRef<MapRef>(null)
  const [style, setStyle] = useState<MapStyle>('default')
  const [sensorsVisible, setSensorsVisible] = useState(true)
  const [fogVisible, setFogVisible] = useState(true)
  const [cloudVisible, setCloudVisible] = useState(true)
  const [routesVisible, setRoutesVisible] = useState(true)
  const [heatmapVisible, setHeatmapVisible] = useState(true)
  const [routeAnimation, setRouteAnimation] = useState<{
    taskId: string | null
    progress: number
  }>({ taskId: null, progress: 0 })
  const selectedStyle = MAP_STYLES[style]
  const isTilted = style === 'openstreetmap3d'
  const taskCounts = metrics?.task_counts_by_sensor ?? {}
  const computeNodes = getCityComputeNodes(nodes)
  const computeNodeById = new Map(
    computeNodes.map((node) => [node.backendNodeId, node]),
  )
  const sensor = allocation
    ? sensorById.get(allocation.task.source_sensor_id)
    : undefined
  const routeNode = allocation?.task.assigned_node_id
    ? computeNodeById.get(allocation.task.assigned_node_id)
    : undefined
  const routeCoordinates: [number, number][] = sensor && routeNode
    ? [
        [sensor.longitude, sensor.latitude],
        [routeNode.longitude, routeNode.latitude],
      ]
    : []
  const routeAnimationId = allocation?.task.task_id
  const routeProgress =
    routeAnimation.taskId === routeAnimationId ? routeAnimation.progress : 0
  const routeStartedAt = useRef(0)

  useEffect(() => {
    mapRef.current?.easeTo({ pitch: isTilted ? 60 : 0, duration: 500 })
  }, [isTilted])

  useEffect(() => {
    if (!routeAnimationId) {
      routeStartedAt.current = 0
      return
    }

    routeStartedAt.current = performance.now()
    let frame = 0
    const animate = () => {
      const progress = routeProgressFor(routeStartedAt.current)
      setRouteAnimation({ taskId: routeAnimationId, progress })
      if (progress < 1) frame = requestAnimationFrame(animate)
    }
    frame = requestAnimationFrame(animate)
    return () => cancelAnimationFrame(frame)
  }, [routeAnimationId])

  const selectedNode = selectedDevice && selectedDevice.kind !== 'sensor'
    ? nodes.find((node) => node.id === selectedDevice.backendNodeId)
    : undefined
  const routeTarget = routeNode
    ? nodes.find((node) => node.id === routeNode.backendNodeId)
    : undefined
  const hasHeatData = Object.values(taskCounts).some((count) => count > 0)

  const resetView = () => {
    mapRef.current?.flyTo({
      center: CITY_CENTER,
      zoom: CITY_ZOOM,
      bearing: 0,
      pitch: isTilted ? 60 : 0,
      duration: 700,
    })
  }

  return (
    <div className={`smart-city-map${allocation ? ' smart-city-map--allocated' : ''}`}>
      <MapCN
        ref={mapRef}
        center={CITY_CENTER}
        zoom={CITY_ZOOM}
        pitch={0}
        theme="dark"
        className="smart-city-map-canvas"
        styles={selectedStyle ? { light: selectedStyle, dark: selectedStyle } : undefined}
      >
        <RequestHeatmap counts={taskCounts} visible={heatmapVisible} />
        {sensorsVisible && CITY_SENSORS.map((device) => (
          <MapMarker
            key={device.id}
            longitude={device.longitude}
            latitude={device.latitude}
            onClick={() => onSelectDevice(device)}
          >
            <MarkerContent>
              <button
                type="button"
                className={`smart-map-marker smart-map-marker--sensor${selectedDevice?.id === device.id ? ' smart-map-marker--selected' : ''}`}
                aria-label={`Select ${device.name}, ${device.id}`}
                onClick={() => onSelectDevice(device)}
              >
                <span />
              </button>
            </MarkerContent>
            <MarkerTooltip>
              <div className="map-marker-tooltip">
                <strong>{device.name}</strong>
                <span>{device.id} · {device.category}</span>
                <small>Payload {device.payload} · Sample {device.samplingPeriod}</small>
              </div>
            </MarkerTooltip>
          </MapMarker>
        ))}
        {computeNodes.filter((device) =>
          device.kind === 'fog' ? fogVisible : cloudVisible,
        ).map((device) => {
          const telemetry = nodes.find((node) => node.id === device.backendNodeId)
          return (
            <MapMarker
              key={device.backendNodeId}
              longitude={device.longitude}
              latitude={device.latitude}
              onClick={() => onSelectDevice(device)}
            >
              <MarkerContent>
                <button
                  type="button"
                  className={`smart-map-marker smart-map-marker--node${device.kind === 'cloud' ? ' smart-map-marker--cloud' : ''}${selectedDevice?.id === device.id ? ' smart-map-marker--selected' : ''}`}
                  style={{ '--marker-color': device.colorHex } as CSSProperties}
                  aria-label={`Select ${device.name} (${device.kind})`}
                  onClick={() => onSelectDevice(device)}
                >
                  <span />
                </button>
              </MarkerContent>
              <MarkerTooltip>
                <div className="map-marker-tooltip">
                  <strong>{telemetry?.name ?? device.name}</strong>
                  <span>{device.backendNodeId} · {device.kind} node</span>
                  {telemetry ? (
                    <small>
                      {telemetry.online
                        ? 'Online'
                        : telemetry.faulted
                          ? 'Repairing after simulated outage'
                          : 'Offline'} · {telemetry.projected_utilization.toFixed(1)}% utilization · Queue {telemetry.queue_length} · {telemetry.network_latency_ms} ms latency
                    </small>
                  ) : (
                    <small>Waiting for backend telemetry</small>
                  )}
                </div>
              </MarkerTooltip>
            </MapMarker>
          )
        })}
        {routesVisible && allocation && routeCoordinates.length === 2 && (
          <MapRoute
            id={`allocation-${allocation.task.task_id}`}
            coordinates={routeCoordinates}
            progress={routeProgress}
            color="#94a3b8"
            width={4}
            opacity={0.72}
            active
            activeColor="#22D3EE"
            activeWidth={5}
            activeOpacity={0.96}
            dashArray={[1.2, 1.2]}
            activeDashArray={[1.2, 1.2]}
            interactive={false}
          >
            <RouteProgress color="#22D3EE" width={5} opacity={1} />
            {routeProgress < 1 && (
              <RouteMarker at="progress">
                <MarkerContent>
                  <span className="smart-map-route-packet" aria-label="Task packet in transit" />
                </MarkerContent>
              </RouteMarker>
            )}
          </MapRoute>
        )}
        <MapControls position="bottom-right" showZoom showCompass />
      </MapCN>

      <div className="smart-map-toolbar" role="group" aria-label="Smart city map controls">
        <label className="smart-map-style">
          <span>Map style</span>
          <select
            aria-label="Map style"
            value={style}
            onChange={(event) => setStyle(event.target.value as MapStyle)}
          >
            <option value="default">Default (Carto)</option>
            <option value="openstreetmap">OpenStreetMap</option>
            <option value="openstreetmap3d">OpenStreetMap 3D</option>
          </select>
        </label>
        <button type="button" onClick={resetView}>Reset view</button>
        <button type="button" aria-pressed={sensorsVisible} onClick={() => setSensorsVisible((visible) => !visible)}>
          Sensors {sensorsVisible ? 'On' : 'Off'}
        </button>
        <button type="button" aria-pressed={fogVisible} onClick={() => setFogVisible((visible) => !visible)}>
          Fog {fogVisible ? 'On' : 'Off'}
        </button>
        <button type="button" aria-pressed={cloudVisible} onClick={() => setCloudVisible((visible) => !visible)}>
          Cloud {cloudVisible ? 'On' : 'Off'}
        </button>
        <button type="button" aria-pressed={routesVisible} onClick={() => setRoutesVisible((visible) => !visible)}>
          Routes {routesVisible ? 'On' : 'Off'}
        </button>
        <button type="button" aria-pressed={heatmapVisible} onClick={() => setHeatmapVisible((visible) => !visible)}>
          Heatmap {heatmapVisible ? 'On' : 'Off'}
        </button>
        {allocation && (
          <button type="button" onClick={onClearRoute}>Clear route</button>
        )}
      </div>

      {!hasHeatData && (
        <div className="smart-map-empty-heat" role="status">
          No current-run sensor requests to map yet.
        </div>
      )}
      <div className="smart-map-legend" aria-label="Request density legend">
        <strong>Current-run task count per sensor</strong>
        <span><i className="heat-low" />Low</span>
        <span><i className="heat-medium" />Medium</span>
        <span><i className="heat-high" />High</span>
      </div>
      <div className="smart-map-caption">
        Mumbai demonstration area · fictional device and node positions
      </div>

      {selectedDevice && (
        <aside className="smart-map-selection" aria-label="Selected map device">
          <button
            type="button"
            className="smart-map-selection-close"
            aria-label="Clear selected device"
            onClick={onClearSelection}
          >
            ×
          </button>
          {selectedDevice.kind === 'sensor' ? (
            <>
              <span className="map-popup-kicker">IoT sensor</span>
              <strong>{selectedDevice.name}</strong>
              <span>{selectedDevice.id} · {selectedDevice.category}</span>
              <small>Payload {selectedDevice.payload} · Sample {selectedDevice.samplingPeriod}</small>
              <small>{taskCounts[selectedDevice.id] ?? 0} tasks in this simulation run</small>
              <button
                type="button"
                className="smart-map-action"
                onClick={() => onGenerateTask(selectedDevice)}
                disabled={busy || !canGenerate}
              >
                {busy ? 'Generating…' : 'Generate task from sensor'}
              </button>
            </>
          ) : (
            <>
              <span className="map-popup-kicker">{selectedDevice.kind} compute node</span>
              <strong>{selectedNode?.name ?? selectedDevice.name}</strong>
              <span>{selectedDevice.backendNodeId}</span>
              {selectedNode ? (
                <>
                  <small>
                    {selectedNode.online
                      ? 'Online'
                      : selectedNode.faulted
                        ? 'Repairing after simulated outage'
                        : 'Offline'} · {selectedNode.projected_utilization.toFixed(1)}% utilization
                  </small>
                  <small>Queue {selectedNode.queue_length} · Load {selectedNode.projected_workload}/{selectedNode.processing_capacity} CPU</small>
                  <small>Network {selectedNode.network_latency_ms} ms · Rate {selectedNode.processing_rate} CPU/s</small>
                </>
              ) : (
                <small>Waiting for backend telemetry</small>
              )}
            </>
          )}
        </aside>
      )}

      {allocation && (
        <div className="smart-map-allocation" role="status">
          <span className="map-popup-kicker">Backend allocation · {algorithmLabel(allocation.algorithm)}</span>
          <strong>{allocation.task.task_id}</strong>
          <span>{allocation.task.source_sensor_id} → {routeTarget?.name ?? allocation.task.assigned_node_id}</span>
          <small>{allocation.task.allocation_explanation}</small>
          {routeProgress >= 1 && <small>Route animation complete.</small>}
        </div>
      )}
      {taskFailure && (
        <div className="smart-map-failure" role="alert">
          <span className="map-popup-kicker">Allocation not routed</span>
          {taskFailure.taskId && <strong>{taskFailure.taskId}</strong>}
          <span>{taskFailure.message}</span>
        </div>
      )}
    </div>
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

export default SmartCityMap
