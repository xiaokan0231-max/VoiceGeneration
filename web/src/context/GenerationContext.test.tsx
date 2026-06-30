import { act, fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ToastProvider } from '../components/Feedback'
import { GenerationProvider, useGeneration } from './GenerationContext'

function Fixture() {
  const { draft, setDraft } = useGeneration()
  return <><span>{draft.text}</span><button onClick={() => setDraft(value => ({ ...value, text: '自动保存后的文本' }))}>修改</button></>
}

describe('GenerationProvider draft persistence', () => {
  beforeEach(() => {
    localStorage.setItem('voicegeneration.draft.v1', JSON.stringify({ text: '恢复的草稿', speed: 1.2 }))
    vi.useFakeTimers()
  })
  afterEach(() => { vi.useRealTimers(); localStorage.clear() })

  it('restores and version-saves the complete workbench draft', async () => {
    render(<ToastProvider><GenerationProvider><Fixture /></GenerationProvider></ToastProvider>)
    expect(screen.getByText('恢复的草稿')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '修改' }))
    await act(async () => { vi.advanceTimersByTime(400) })
    expect(JSON.parse(localStorage.getItem('voicegeneration.draft.v1') || '{}').text).toBe('自动保存后的文本')
  })
})
