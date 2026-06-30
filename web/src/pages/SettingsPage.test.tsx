import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SettingsPage from './SettingsPage'
import { ToastProvider } from '../components/Feedback'

const response = (value: unknown) => Promise.resolve({ ok: true, json: () => Promise.resolve(value) } as Response)

const runtimeModel = (id: string, enabled: boolean, port: number) => ({
  id, description: id, enabled, python: '/usr/bin/python3', host: '127.0.0.1',
  port, replicas: 1, languages: ['zh'], supports_cloning: true,
  options: { device: 'auto' }, loaded: false,
})

describe('SettingsPage model scheduling toggle', () => {
  let f5Enabled = true
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    f5Enabled = true
    fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/v1/settings' && (!init?.method || init.method === 'GET')) {
        return response({
          default_model: 'cosyvoice3', default_format: 'wav', worker_idle_timeout: 300,
          worker_start_timeout: 180, cache_max_gb: 30,
          models: [runtimeModel('cosyvoice3', true, 8110), runtimeModel('f5_tts', f5Enabled, 8120)],
        })
      }
      if (url === '/v1/models/f5_tts/config' && init?.method === 'PUT') {
        f5Enabled = Boolean(JSON.parse(String(init.body)).enabled)
        return response(runtimeModel('f5_tts', f5Enabled, 8120))
      }
      if (url === '/v1/cluster/nodes') {
        const workers = [
          { id: 'cosyvoice3#1', model: 'cosyvoice3', index: 1, port: 8110, started: true, active: false, job_id: null, text: '', elapsed_seconds: null, speed: .42, speed_30m: .5, samples_30m: 4, audio_seconds: 10, inference_seconds: 23.8, error: null },
          ...(f5Enabled ? [{ id: 'f5_tts#1', model: 'f5_tts', index: 1, port: 8120, started: false, active: false, job_id: null, text: '', elapsed_seconds: null, speed: null, speed_30m: null, samples_30m: 0, audio_seconds: null, inference_seconds: null, error: null }] : []),
        ]
        return response({
          self: { node_id: 'mac-main', node_name: 'Mac 主机', role: 'coordinator', coordinator_runs_jobs: true },
          queue_depth: 0,
          nodes: [{ node_id: 'mac-main', name: 'Mac 主机', role: 'coordinator', models: f5Enabled ? ['cosyvoice3', 'f5_tts'] : ['cosyvoice3'], max_concurrency: workers.length, status: 'online', version: '1', last_seen: null, started_workers: 1, working_workers: 0, total_speed: null, latest_speed: .42, average_speed_30m: .5, samples_30m: 4, metrics_updated_at: null, workers }],
        })
      }
      if (url === '/v1/cluster/connect-info') return response({ host: '127.0.0.1', port: 8080, reachable: true, token: '', hostname: 'mac', candidate_urls: [] })
      return response({})
    })
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => { cleanup(); vi.unstubAllGlobals() })

  it('shows the 30-minute average and latest completed speed together', async () => {
    render(<ToastProvider><SettingsPage system={null} onSystemChange={vi.fn()} /></ToastProvider>)

    expect(await screen.findByText('集群运行状态')).toBeInTheDocument()
    expect(screen.getByText('最近一次总速度')).toBeInTheDocument()
    expect(screen.getAllByText('近 30 分钟平均')).toHaveLength(3)
    expect(screen.getAllByText('0.50×')).toHaveLength(2)
    expect(screen.getAllByText('0.42×')).toHaveLength(2)
  })

  it('immediately disables scheduling and refreshes cluster capacity', async () => {
    const onSystemChange = vi.fn()
    render(<ToastProvider><SettingsPage system={null} onSystemChange={onSystemChange} /></ToastProvider>)
    const toggle = await screen.findByRole('checkbox', { name: '停用 f5_tts' })
    expect(screen.queryByRole('button', { name: '停止' })).not.toBeInTheDocument()

    fireEvent.click(toggle)

    await waitFor(() => expect(screen.getByText('f5_tts 已停用并退出调度')).toBeInTheDocument())
    expect(screen.getByRole('checkbox', { name: '启用 f5_tts' })).not.toBeChecked()
    expect(screen.getByText(/总槽位 1 · cosyvoice3 1槽/)).toBeInTheDocument()
    await waitFor(() => expect(onSystemChange).toHaveBeenCalled())
    const put = fetchMock.mock.calls.find(([url, init]) => String(url) === '/v1/models/f5_tts/config' && init?.method === 'PUT')
    expect(JSON.parse(String(put?.[1]?.body))).toEqual({ enabled: false })
  })
})
