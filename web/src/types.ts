export type PageKey = 'workbench' | 'voices' | 'projects' | 'history' | 'settings'
export type GenerationMode = 'clone' | 'instruct' | 'cross_lingual'

export interface ProjectDetail {
  id: string
  name: string
  description?: string | null
  color?: string | null
  generation_count: number
  created_at: string
  updated_at?: string | null
}

export interface ModelInfo {
  id: string
  description: string
  enabled: boolean
  languages: string[]
  supports_cloning: boolean
  loaded: boolean
}

export interface VoiceInfo {
  id: string
  name: string
  language: string | null
  kind: 'clone' | 'builtin'
  model: string
}

export interface VoiceDetail {
  id: string
  name: string
  language: string
  ref_text: string
  models: string[]
  audio_url: string
}

export interface GenerationDraft {
  text: string
  model: string
  voice: string
  mode: GenerationMode
  language: string
  speed: number
  format: string
  instruct_text: string
  project_id: string
}

export interface HistoryItem extends GenerationDraft {
  id: string
  voice_name: string
  project_name: string | null
  assigned_node: string | null
  node_name: string | null
  status: 'queued' | 'leased' | 'running' | 'completed' | 'failed'
  duration_seconds: number | null
  byte_size: number | null
  cache_hit: boolean
  elapsed_seconds: number | null
  error_message: string | null
  audio_available: boolean
  created_at: string
  completed_at: string | null
}

export interface HistoryResponse {
  items: HistoryItem[]
  total: number
  page: number
  page_size: number
}

export interface RuntimeModel extends ModelInfo {
  python: string
  host: string
  port: number
  options: Record<string, unknown>
}

export interface SystemInfo {
  service: string
  version: string
  platform: string
  apple_silicon: boolean
  mps: boolean
  database: string
  cache_bytes: number
  cache_limit_bytes: number
  models: RuntimeModel[]
}

export interface SettingsInfo {
  default_model: string
  default_format: string
  worker_idle_timeout: number
  worker_start_timeout: number
  cache_max_gb: number
  models: RuntimeModel[]
}

export interface SynthesisResult {
  url: string
  blob: Blob
  generationId: string
  cache: string
}

export interface ClusterNodeInfo {
  node_id: string
  name: string
  role: string
  models: string[]
  max_concurrency: number
  status: string
  version: string | null
  last_seen: string | null
}

export interface ClusterInfo {
  self: { node_id: string; node_name: string; role: string; coordinator_runs_jobs: boolean }
  nodes: ClusterNodeInfo[]
  queue_depth: number
}

export interface ConnectInfo {
  host: string
  port: number
  reachable: boolean
  token: string
  hostname: string
  candidate_urls: string[]
}
