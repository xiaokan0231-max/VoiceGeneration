import { useCallback, useEffect, useMemo, useState } from 'react'
import { Download, History, LoaderCircle, RefreshCw, Search, Trash2, X } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api'
import AudioPlayer from '../components/AudioPlayer'
import { ConfirmDialog, useToast } from '../components/Feedback'
import type { GenerationDraft, HistoryItem, ModelInfo, ProjectDetail } from '../types'

const statusLabel: Record<HistoryItem['status'], string> = {
  completed: '已完成', running: '生成中', leased: '生成中', queued: '排队中', failed: '失败', cancelled: '已取消',
}

export default function HistoryPage({ version, projects, onReuse }: { version: number; projects: ProjectDetail[]; onReuse: (draft: GenerationDraft) => void }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const [items, setItems] = useState<HistoryItem[]>([])
  const [models, setModels] = useState<ModelInfo[]>([])
  const [total, setTotal] = useState(0)
  const [queryInput, setQueryInput] = useState(() => searchParams.get('q') || '')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [moving, setMoving] = useState('')
  const [deleting, setDeleting] = useState<HistoryItem | null>(null)
  const [deleteBusy, setDeleteBusy] = useState(false)
  const { notify } = useToast()

  const queryString = searchParams.toString()
  const page = Math.max(1, Number(searchParams.get('page') || 1))
  const model = searchParams.get('model') || ''
  const status = searchParams.get('status') || ''
  const project = searchParams.get('project') || ''
  const query = searchParams.get('q') || ''
  const filtered = Boolean(query || model || status || project)

  const updateParams = useCallback((updates: Record<string, string>) => {
    const next = new URLSearchParams(searchParams)
    for (const [key, value] of Object.entries(updates)) value ? next.set(key, value) : next.delete(key)
    setSearchParams(next, { replace: true })
  }, [searchParams, setSearchParams])
  useEffect(() => { api.models().then(setModels).catch(() => undefined) }, [])
  useEffect(() => { setQueryInput(query) }, [query])
  useEffect(() => {
    if (queryInput === query) return
    const timer = window.setTimeout(() => updateParams({ q: queryInput.trim(), page: '1' }), 300)
    return () => window.clearTimeout(timer)
  }, [queryInput, query, updateParams])
  useEffect(() => {
    const controller = new AbortController()
    const params = new URLSearchParams(queryString); params.set('page', String(page)); params.set('page_size', '10')
    setLoading(true); setError('')
    api.history(params.toString(), controller.signal).then(result => {
      setItems(result.items); setTotal(result.total)
    }).catch(value => {
      if (!(value instanceof DOMException && value.name === 'AbortError')) setError(value instanceof Error ? value.message : '历史记录加载失败')
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [queryString, version, page])

  const remove = async () => {
    if (!deleting) return
    setDeleteBusy(true)
    try {
      await api.deleteHistory(deleting.id); setItems(current => current.filter(item => item.id !== deleting.id)); setTotal(value => Math.max(0, value - 1)); setDeleting(null)
      notify('历史记录已删除', 'success')
    } catch (value) { notify(value instanceof Error ? value.message : '删除失败', 'error') }
    finally { setDeleteBusy(false) }
  }
  const moveToProject = async (id: string, projectId: string) => {
    const previous = items.find(item => item.id === id)
    if (!previous) return
    const target = projects.find(value => value.id === projectId)
    setMoving(id); setItems(current => current.map(item => item.id === id ? { ...item, project_id: projectId, project_name: target?.name || null } : item))
    try {
      const updated = await api.setHistoryProject(id, projectId || null)
      setItems(current => current.map(item => item.id === id ? updated : item)); notify('项目归类已更新', 'success')
    } catch (value) {
      setItems(current => current.map(item => item.id === id ? previous : item)); notify(value instanceof Error ? value.message : '移动失败', 'error')
    } finally { setMoving('') }
  }
  const reuse = (item: HistoryItem) => onReuse({
    text: item.text, model: item.model, voice: item.voice, mode: item.mode, language: item.language,
    speed: item.speed, format: item.format, instruct_text: item.instruct_text, project_id: item.project_id,
  })
  const projectColors = useMemo(() => new Map(projects.map(value => [value.id, value.color || '#6d777d'])), [projects])

  return <div className="content-page history-page">
    <div className="page-heading large"><div><span className="eyebrow">ARCHIVE</span><h1>生成历史</h1><p>搜索、播放并复用这台 Mac 上生成过的内容</p></div><span className="record-count">{total} 条记录</span></div>
    <div className="history-filters">
      <label className="search-field"><Search /><input value={queryInput} onChange={event => setQueryInput(event.target.value)} placeholder="搜索文本内容" aria-label="搜索历史文本" /></label>
      <select aria-label="按模型筛选" value={model} onChange={event => updateParams({ model: event.target.value, page: '1' })}><option value="">全部模型</option>{models.map(value => <option key={value.id}>{value.id}</option>)}</select>
      <select aria-label="按状态筛选" value={status} onChange={event => updateParams({ status: event.target.value, page: '1' })}><option value="">全部状态</option><option value="completed">已完成</option><option value="leased">生成中</option><option value="queued">排队中</option><option value="failed">失败</option><option value="cancelled">已取消</option></select>
      <select aria-label="按项目筛选" value={project} onChange={event => updateParams({ project: event.target.value, page: '1' })}><option value="">全部项目</option><option value="__none__">未归类</option>{projects.map(value => <option key={value.id} value={value.id}>{value.name}</option>)}</select>
      {filtered && <button className="quiet-button clear-filters" onClick={() => { setQueryInput(''); setSearchParams({}, { replace: true }) }}><X />清空筛选</button>}
    </div>
    {error && <div className="inline-error page-error" role="alert">{error}</div>}
    {loading && <div className="loading-state"><LoaderCircle className="spin" />正在加载历史记录…</div>}
    {!loading && !error && items.length === 0 && <div className="empty-state"><History /><h3>{filtered ? '没有符合条件的记录' : '还没有生成记录'}</h3><p>{filtered ? '尝试清空筛选，或换一个关键词。' : '从生成工作台提交第一条语音任务。'}</p></div>}
    <div className={`history-list ${loading ? 'is-loading' : ''}`} aria-busy={loading}>
      {items.map(item => <article className="history-entry" key={item.id}>
        <div className="history-main"><div className="history-topline"><span className={`state-label ${item.status}`}>{statusLabel[item.status]}</span><span className="project-badge"><span className="badge-dot" style={{ background: projectColors.get(item.project_id) || '#6d777d' }} />{item.project_name || '未归类'}</span><time>{new Date(item.created_at).toLocaleString('zh-CN', { hour12: false })}</time></div><p>{item.text}</p><small>{item.model} · {item.voice_name} · {item.mode} · {item.speed.toFixed(1)}× · {item.format.toUpperCase()}{item.duration_seconds != null ? ` · 时长 ${item.duration_seconds.toFixed(1)}s` : ''}{item.elapsed_seconds != null ? ` · 耗时 ${item.elapsed_seconds.toFixed(1)}s` : ''}{item.cache_hit ? ' · 缓存命中' : ''}{item.node_name ? ` · 由 ${item.node_name} 生成` : ''}</small>{item.error_message && <div className="entry-error">{item.error_message}</div>}<label className="move-project"><span>{moving === item.id ? '移动中' : '移到'}</span><select disabled={moving === item.id} value={item.project_id || ''} onChange={event => moveToProject(item.id, event.target.value)}><option value="">未归类</option>{projects.map(value => <option key={value.id} value={value.id}>{value.name}</option>)}</select></label></div>
        <div className="history-player">{item.audio_available ? <AudioPlayer src={`/v1/history/${item.id}/audio`} compact /> : <span className="audio-missing">{item.status === 'failed' ? '没有音频' : item.status === 'cancelled' ? '任务已取消' : '音频已清理'}</span>}</div>
        <div className="history-actions">{item.audio_available && <a href={`/v1/history/${item.id}/audio`} download aria-label="下载音频"><Download /></a>}<button onClick={() => reuse(item)} aria-label="在工作台复用"><RefreshCw /></button><button onClick={() => setDeleting(item)} aria-label="删除历史记录"><Trash2 /></button></div>
      </article>)}
    </div>
    {total > 10 && <div className="pagination"><button disabled={page <= 1} onClick={() => updateParams({ page: String(page - 1) })}>上一页</button><span>{page} / {Math.ceil(total / 10)}</span><button disabled={page >= Math.ceil(total / 10)} onClick={() => updateParams({ page: String(page + 1) })}>下一页</button></div>}
    <ConfirmDialog open={Boolean(deleting)} title="删除这条历史记录？" description="记录会从 MySQL 删除，但磁盘中的共享音频缓存不会因此删除。" confirmLabel="删除记录" busy={deleteBusy} onCancel={() => setDeleting(null)} onConfirm={remove} />
  </div>
}
