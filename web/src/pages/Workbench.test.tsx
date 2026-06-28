import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Workbench from './Workbench'

const response = (value: unknown) => Promise.resolve({ ok: true, json: () => Promise.resolve(value) } as Response)

describe('Workbench', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.startsWith('/v1/models')) return response([{ id: 'cosyvoice3', description: 'CosyVoice 3', enabled: true, languages: ['zh'], supports_cloning: true, loaded: false }])
      if (url.startsWith('/v1/voices')) return response([{ id: 'narrator_zh', name: '中文旁白', language: 'zh', kind: 'clone', model: 'cosyvoice3' }])
      if (url.startsWith('/v1/history')) return response({ items: [], total: 0, page: 1, page_size: 4 })
      if (url.startsWith('/v1/settings')) return response({ default_model: 'cosyvoice3', default_format: 'wav', models: [] })
      if (url.startsWith('/v1/projects')) return response([])
      return response({})
    }))
  })
  afterEach(() => vi.unstubAllGlobals())

  it('switches to instruct mode and enforces a style instruction', async () => {
    render(<Workbench historyVersion={0} projects={[]} onProjectsChange={() => undefined} onGenerated={() => undefined} />)
    await waitFor(() => expect(screen.getByText('中文旁白')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: '指令' }))
    expect(screen.getByText('风格指令（必填）')).toBeTruthy()
    fireEvent.change(screen.getByPlaceholderText('例如：沉稳、克制、有纪录片质感'), { target: { value: '' } })
    expect(screen.getByRole('button', { name: '生成语音' })).toBeDisabled()
  })
})
