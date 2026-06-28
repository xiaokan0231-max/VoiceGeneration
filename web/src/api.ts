import type {
  ClusterInfo, ConnectInfo, GenerationDraft, HistoryItem, HistoryResponse, ModelInfo,
  ProjectDetail, SettingsInfo, SynthesisResult, SystemInfo, VoiceDetail, VoiceInfo,
} from './types'

async function readError(response: Response): Promise<string> {
  try {
    const data = await response.json()
    return data.detail || data.error || `请求失败 (${response.status})`
  } catch {
    return `请求失败 (${response.status})`
  }
}

export async function jsonRequest<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) throw new Error(await readError(response))
  return response.json() as Promise<T>
}

export const api = {
  models: () => jsonRequest<ModelInfo[]>('/v1/models'),
  voices: (model: string) => jsonRequest<VoiceInfo[]>(`/v1/voices?model=${encodeURIComponent(model)}`),
  voiceLibrary: () => jsonRequest<VoiceDetail[]>('/v1/voice-library'),
  history: (params = '') => jsonRequest<HistoryResponse>(`/v1/history${params ? `?${params}` : ''}`),
  system: () => jsonRequest<SystemInfo>('/v1/system'),
  settings: () => jsonRequest<SettingsInfo>('/v1/settings'),
  synthesize: async (draft: GenerationDraft): Promise<SynthesisResult> => {
    const response = await fetch('/v1/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(draft),
    })
    if (!response.ok) throw new Error(await readError(response))
    const blob = await response.blob()
    return {
      blob,
      url: URL.createObjectURL(blob),
      generationId: response.headers.get('X-Generation-Id') || '',
      cache: response.headers.get('X-Cache') || '',
    }
  },
  deleteHistory: (id: string) => jsonRequest<{ ok: boolean }>(`/v1/history/${id}`, { method: 'DELETE' }),
  saveSettings: (body: Record<string, unknown>) => jsonRequest<SettingsInfo>('/v1/settings', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }),
  saveModel: (id: string, body: Record<string, unknown>) => jsonRequest<unknown>(`/v1/models/${id}/config`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }),
  modelAction: (id: string, action: 'start' | 'stop' | 'restart') =>
    jsonRequest<{ ok: boolean }>(`/v1/models/${id}/${action}`, { method: 'POST' }),
  shutdown: () => jsonRequest<{ ok: boolean }>('/v1/service/shutdown', { method: 'POST' }),
  saveVoice: (form: FormData, id?: string) => jsonRequest<VoiceDetail>(id ? `/v1/voices/${id}` : '/v1/voices', {
    method: id ? 'PUT' : 'POST', body: form,
  }),
  deleteVoice: (id: string) => jsonRequest<{ ok: boolean }>(`/v1/voices/${id}`, { method: 'DELETE' }),
  projects: () => jsonRequest<ProjectDetail[]>('/v1/projects'),
  saveProject: (body: Record<string, unknown>, id?: string) =>
    jsonRequest<ProjectDetail>(id ? `/v1/projects/${id}` : '/v1/projects', {
      method: id ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }),
  deleteProject: (id: string) => jsonRequest<{ ok: boolean }>(`/v1/projects/${id}`, { method: 'DELETE' }),
  setHistoryProject: (id: string, project_id: string | null) =>
    jsonRequest<HistoryItem>(`/v1/history/${id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ project_id }),
    }),
  cluster: () => jsonRequest<ClusterInfo>('/v1/cluster/nodes'),
  connectInfo: () => jsonRequest<ConnectInfo>('/v1/cluster/connect-info'),
}
