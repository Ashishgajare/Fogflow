import type { NodeTelemetry, TaskSpecification } from './api'

export interface CitySensor {
  kind: 'sensor'
  id: string
  name: string
  category: string
  payload: string
  samplingPeriod: string
  longitude: number
  latitude: number
  task: TaskSpecification
}

export interface CityComputeNode {
  kind: 'fog' | 'cloud'
  id: string
  name: string
  backendNodeId: string
  longitude: number
  latitude: number
  colorHex: string
}

export type CityDevice = CitySensor | CityComputeNode

export const CITY_CENTER: [number, number] = [72.87, 19.06]

export const CITY_SENSORS: CitySensor[] = [
  {
    kind: 'sensor',
    id: 'IOT-CAM-01',
    name: 'Fort Traffic Camera',
    category: 'Traffic camera',
    payload: '12.4 MB',
    samplingPeriod: '150 ms',
    longitude: 72.8354,
    latitude: 18.9322,
    task: {
      source_sensor_id: 'IOT-CAM-01',
      task_type: 'computer_vision',
      cpu_demand: 18,
      data_size_mb: 12.4,
      priority: 3,
      deadline_seconds: 2,
    },
  },
  {
    kind: 'sensor',
    id: 'IOT-MED-02',
    name: 'Parel Hospital Telemetry',
    category: 'Hospital telemetry',
    payload: '820 KB',
    samplingPeriod: '10 ms',
    longitude: 72.8421,
    latitude: 19.0052,
    task: {
      source_sensor_id: 'IOT-MED-02',
      task_type: 'vitals',
      cpu_demand: 10,
      data_size_mb: 0.82,
      priority: 3,
      deadline_seconds: 1.5,
    },
  },
  {
    kind: 'sensor',
    id: 'IOT-GRD-03',
    name: 'BKC Power Substation',
    category: 'Smart power substation',
    payload: '45 KB',
    samplingPeriod: '20 ms',
    longitude: 72.8662,
    latitude: 19.0607,
    task: {
      source_sensor_id: 'IOT-GRD-03',
      task_type: 'anomaly_detection',
      cpu_demand: 8,
      data_size_mb: 0.045,
      priority: 2,
      deadline_seconds: 1,
    },
  },
  {
    kind: 'sensor',
    id: 'IOT-AIR-04',
    name: 'Powai Air Quality Monitor',
    category: 'Air-quality monitor',
    payload: '12 KB',
    samplingPeriod: '300 ms',
    longitude: 72.9068,
    latitude: 19.1176,
    task: {
      source_sensor_id: 'IOT-AIR-04',
      task_type: 'aggregation',
      cpu_demand: 5,
      data_size_mb: 0.012,
      priority: 1,
      deadline_seconds: 3,
    },
  },
  {
    kind: 'sensor',
    id: 'IOT-FLW-05',
    name: 'Chembur Water Pump',
    category: 'Water-pump telemetry',
    payload: '3.4 MB',
    samplingPeriod: '250 ms',
    longitude: 72.8991,
    latitude: 19.0527,
    task: {
      source_sensor_id: 'IOT-FLW-05',
      task_type: 'flow_analysis',
      cpu_demand: 14,
      data_size_mb: 3.4,
      priority: 2,
      deadline_seconds: 2.5,
    },
  },
  {
    kind: 'sensor',
    id: 'IOT-BUS-06',
    name: 'Andheri Transit Sensor',
    category: 'Public transport sensor',
    payload: '2.1 MB',
    samplingPeriod: '80 ms',
    longitude: 72.8462,
    latitude: 19.1198,
    task: {
      source_sensor_id: 'IOT-BUS-06',
      task_type: 'telematics',
      cpu_demand: 12,
      data_size_mb: 2.1,
      priority: 2,
      deadline_seconds: null,
    },
  },
  {
    kind: 'sensor',
    id: 'IOT-EV-07',
    name: 'Lower Parel EV Hub',
    category: 'EV charging station',
    payload: '320 KB',
    samplingPeriod: '50 ms',
    longitude: 72.8307,
    latitude: 18.9964,
    task: {
      source_sensor_id: 'IOT-EV-07',
      task_type: 'power_telemetry',
      cpu_demand: 8,
      data_size_mb: 0.32,
      priority: 2,
      deadline_seconds: 1.5,
    },
  },
  {
    kind: 'sensor',
    id: 'IOT-STR-08',
    name: 'Dadar Smart Streetlights',
    category: 'Smart streetlights',
    payload: '18 KB',
    samplingPeriod: '500 ms',
    longitude: 72.8442,
    latitude: 19.0178,
    task: {
      source_sensor_id: 'IOT-STR-08',
      task_type: 'aggregation',
      cpu_demand: 5,
      data_size_mb: 0.018,
      priority: 1,
      deadline_seconds: 3,
    },
  },
  {
    kind: 'sensor',
    id: 'IOT-LOG-09',
    name: 'Vashi Industrial Gate',
    category: 'Industrial gate sensor',
    payload: '1.8 MB',
    samplingPeriod: '120 ms',
    longitude: 72.9982,
    latitude: 19.0772,
    task: {
      source_sensor_id: 'IOT-LOG-09',
      task_type: 'telematics',
      cpu_demand: 12,
      data_size_mb: 1.8,
      priority: 2,
      deadline_seconds: null,
    },
  },
]

const FOG_NODE_COLORS = ['#22D3EE', '#3B82F6', '#6366F1']

export function getCityComputeNodes(nodes: NodeTelemetry[]): CityComputeNode[] {
  let fogIndex = 0
  return nodes.map((node) => {
    if (node.node_type === 'cloud') {
      return {
        kind: 'cloud',
        id: 'CLOUD-DC',
        name: node.name,
        backendNodeId: node.id,
        longitude: 72.8628,
        latitude: 19.0226,
        colorHex: '#A855F7',
      }
    }

    const index = fogIndex
    fogIndex += 1
    const angle = index * 2.399963229728653
    const radius = 0.025 + 0.065 * Math.sqrt((index + 1) / 75)
    return {
      kind: 'fog',
      id: `FOG-${String(index + 1).padStart(2, '0')}`,
      name: node.name,
      backendNodeId: node.id,
      longitude: CITY_CENTER[0] + Math.cos(angle) * radius,
      latitude: CITY_CENTER[1] + Math.sin(angle) * radius * 0.72,
      colorHex: FOG_NODE_COLORS[index % FOG_NODE_COLORS.length],
    }
  })
}
