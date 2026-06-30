import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api } from '../api'
import { useToast } from '../components/Feedback'
import type { GenerationDraft, GenerationTask } from '../types'

const DRAFT_KEY = 'voicegeneration.draft.v1'
const ACTIVE_KEY = 'voicegeneration.active-jobs.v1'

export const DEFAULT_DRAFT: GenerationDraft = {
  text: '九一八事变后，东北局势急剧变化。', model: 'cosyvoice3', voice: 'narrator_zh',
  mode: 'clone', language: 'zh', speed: 1, format: 'wav',
  instruct_text: '沉稳克制、有历史厚重感的纪录片旁白，避免播音腔。', project_id: '',
}

function readDraft(): GenerationDraft {
  try {
    const parsed = JSON.parse(localStorage.getItem(DRAFT_KEY) || '') as Partial<GenerationDraft>
    return { ...DEFAULT_DRAFT, ...parsed }
  } catch { return DEFAULT_DRAFT }
}

function readActiveIds(): string[] {
  try {
    const value = JSON.parse(localStorage.getItem(ACTIVE_KEY) || '[]')
    return Array.isArray(value) ? value.filter(id => typeof id === 'string').slice(0, 24) : []
  } catch { return [] }
}

const terminal = new Set(['completed', 'failed', 'cancelled'])

interface GenerationState {
  draft: GenerationDraft
  setDraft: React.Dispatch<React.SetStateAction<GenerationDraft>>
  replaceDraft: (draft: GenerationDraft) => void
  resetDraft: () => void
  savedAt: number | null
  tasks: GenerationTask[]
  activeCount: number
  latestTask: GenerationTask | null
  historyVersion: number
  startGeneration: (draft?: GenerationDraft) => Promise<GenerationTask>
  cancelGeneration: (id: string) => Promise<void>
  retryGeneration: (task: GenerationTask) => Promise<GenerationTask>
}

const GenerationContext = createContext<GenerationState | null>(null)

export function GenerationProvider({ children }: { children: ReactNode }) {
  const [draft, setDraft] = useState<GenerationDraft>(readDraft)
  const [savedAt, setSavedAt] = useState<number | null>(null)
  const [tasks, setTasks] = useState<GenerationTask[]>([])
  const tasksRef = useRef<GenerationTask[]>([])
  const [historyVersion, setHistoryVersion] = useState(0)
  const { notify } = useToast()
  useEffect(() => { tasksRef.current = tasks }, [tasks])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      localStorage.setItem(DRAFT_KEY, JSON.stringify(draft))
      setSavedAt(Date.now())
    }, 350)
    return () => window.clearTimeout(timer)
  }, [draft])

  useEffect(() => {
    const ids = readActiveIds()
    if (!ids.length) return
    const controller = new AbortController()
    Promise.allSettled(ids.map(id => api.generation(id, controller.signal))).then(results => {
      const restored = results.flatMap(result => result.status === 'fulfilled' ? [result.value] : [])
      setTasks(restored)
    })
    return () => controller.abort()
  }, [])

  const activeIds = useMemo(
    () => tasks.filter(task => !terminal.has(task.status)).map(task => task.id),
    [tasks],
  )
  const activeKey = activeIds.join('|')
  useEffect(() => { localStorage.setItem(ACTIVE_KEY, JSON.stringify(activeIds)) }, [activeIds])

  useEffect(() => {
    if (!activeKey) return
    const ids = activeKey.split('|')
    let stopped = false
    let timer = 0
    let controller: AbortController | null = null
    const poll = async () => {
      controller?.abort(); controller = new AbortController()
      const results = await Promise.allSettled(ids.map(id => api.generation(id, controller!.signal)))
      if (stopped) return
      const previous = new Map(tasksRef.current.map(task => [task.id, task]))
      const updates = new Map(results.flatMap(result => result.status === 'fulfilled' ? [[result.value.id, result.value] as const] : []))
      const finished = [...updates].flatMap(([id, next]) => {
        const before = previous.get(id)
        return before && !terminal.has(before.status) && terminal.has(next.status) ? [next] : []
      })
      setTasks(current => current.map(task => updates.get(task.id) || task))
      if (finished.length) setHistoryVersion(value => value + finished.length)
      for (const next of finished) {
        if (next.status === 'completed') notify(`“${next.text.slice(0, 18)}”生成完成`, 'success')
        else if (next.status === 'failed') notify(next.error_message || '语音生成失败', 'error')
        else notify('排队任务已取消', 'info')
      }
      timer = window.setTimeout(poll, document.hidden ? 5000 : 2000)
    }
    void poll()
    const onVisibility = () => { window.clearTimeout(timer); if (!stopped) void poll() }
    document.addEventListener('visibilitychange', onVisibility)
    return () => { stopped = true; controller?.abort(); window.clearTimeout(timer); document.removeEventListener('visibilitychange', onVisibility) }
  }, [activeKey, notify])

  const startGeneration = useCallback(async (value?: GenerationDraft) => {
    const task = await api.submitGeneration(value || draft)
    setTasks(current => [task, ...current.filter(item => item.id !== task.id)].slice(0, 24))
    if (terminal.has(task.status)) {
      setHistoryVersion(version => version + 1)
      notify('音频已从缓存生成', 'success')
    } else notify('任务已加入生成队列', 'info')
    return task
  }, [draft, notify])

  const cancelGeneration = useCallback(async (id: string) => {
    const task = await api.cancelGeneration(id)
    setTasks(current => current.map(item => item.id === id ? task : item))
    setHistoryVersion(version => version + 1)
    notify('排队任务已取消', 'info')
  }, [notify])

  const retryGeneration = useCallback((task: GenerationTask) => startGeneration({
    text: task.text, model: task.model, voice: task.voice, mode: task.mode,
    language: task.language, speed: task.speed, format: task.format,
    instruct_text: task.instruct_text, project_id: task.project_id,
  }), [startGeneration])

  const replaceDraft = useCallback((value: GenerationDraft) => setDraft({ ...DEFAULT_DRAFT, ...value }), [])
  const resetDraft = useCallback(() => { localStorage.removeItem(DRAFT_KEY); setDraft(DEFAULT_DRAFT) }, [])
  const value = useMemo<GenerationState>(() => ({
    draft, setDraft, replaceDraft, resetDraft, savedAt, tasks,
    activeCount: activeIds.length, latestTask: tasks[0] || null, historyVersion,
    startGeneration, cancelGeneration, retryGeneration,
  }), [draft, savedAt, tasks, activeIds.length, historyVersion, startGeneration, cancelGeneration, retryGeneration, replaceDraft, resetDraft])

  return <GenerationContext.Provider value={value}>{children}</GenerationContext.Provider>
}

export function useGeneration() {
  const value = useContext(GenerationContext)
  if (!value) throw new Error('useGeneration must be used inside GenerationProvider')
  return value
}
