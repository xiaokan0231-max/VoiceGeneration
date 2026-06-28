import { useEffect, useState } from 'react'
import { Download, History, RefreshCw, Search, Trash2 } from 'lucide-react'
import { api } from '../api'
import AudioPlayer from '../components/AudioPlayer'
import type { GenerationDraft, HistoryItem, ModelInfo, ProjectDetail } from '../types'

export default function HistoryPage({ version, projects, initialProject, onReuse }: { version: number; projects: ProjectDetail[]; initialProject: string; onReuse: (draft: GenerationDraft) => void }) {
  const [items, setItems] = useState<HistoryItem[]>([])
  const [models, setModels] = useState<ModelInfo[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [query, setQuery] = useState('')
  const [model, setModel] = useState('')
  const [status, setStatus] = useState('')
  const [project, setProject] = useState(initialProject)
  const [error, setError] = useState('')

  const load = () => {
    const params = new URLSearchParams({ page: String(page), page_size: '10' })
    if (query) params.set('q', query); if (model) params.set('model', model); if (status) params.set('status', status); if (project) params.set('project', project)
    api.history(params.toString()).then(r => { setItems(r.items); setTotal(r.total) }).catch(e => setError(e.message))
  }
  useEffect(() => { api.models().then(setModels).catch(() => undefined) }, [])
  useEffect(() => { setProject(initialProject); setPage(1) }, [initialProject])
  useEffect(load, [page, model, status, project, version])
  const remove = async (id: string) => { if (!window.confirm('删除这条历史记录？磁盘音频缓存不会因此删除。')) return; await api.deleteHistory(id); load() }
  const moveToProject = async (id: string, projectId: string) => { await api.setHistoryProject(id, projectId || null); load() }
  const projectColor = (id: string | null) => projects.find(p => p.id === id)?.color || '#6d777d'

  return <div className="content-page history-page">
    <div className="page-heading large"><div><span className="eyebrow">ARCHIVE</span><h1>生成历史</h1><p>搜索、播放并复用这台 Mac 上生成过的内容</p></div><span className="record-count">{total} 条记录</span></div>
    <div className="history-filters"><label className="search-field"><Search /><input value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => e.key === 'Enter' && (setPage(1), load())} placeholder="搜索文本内容" /></label><select value={model} onChange={e => { setModel(e.target.value); setPage(1) }}><option value="">全部模型</option>{models.map(m => <option key={m.id}>{m.id}</option>)}</select><select value={status} onChange={e => { setStatus(e.target.value); setPage(1) }}><option value="">全部状态</option><option value="completed">已完成</option><option value="leased">生成中</option><option value="queued">排队中</option><option value="failed">失败</option></select><select value={project} onChange={e => { setProject(e.target.value); setPage(1) }}><option value="">全部项目</option><option value="__none__">未归类</option>{projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select><button className="quiet-button" onClick={() => { setPage(1); load() }}>筛选</button></div>
    {error && <div className="inline-error page-error">{error}</div>}
    <div className="history-list">
      {items.map(item => <article className="history-entry" key={item.id}>
        <div className="history-main"><div className="history-topline"><span className={`state-label ${item.status}`}>{({ completed: '已完成', running: '生成中', leased: '生成中', queued: '排队中', failed: '失败' } as Record<string, string>)[item.status] || item.status}</span><span className="project-badge"><span className="badge-dot" style={{ background: projectColor(item.project_id) }} />{item.project_name || '未归类'}</span><time>{new Date(item.created_at).toLocaleString('zh-CN', { hour12: false })}</time></div><p>{item.text}</p><small>{item.model} · {item.voice_name} · {item.mode} · {item.speed.toFixed(1)}× · {item.format.toUpperCase()}{item.duration_seconds != null ? ` · 时长 ${item.duration_seconds.toFixed(1)}s` : ''}{item.elapsed_seconds != null ? ` · 耗时 ${item.elapsed_seconds.toFixed(1)}s` : ''}{item.cache_hit ? ' · 缓存命中' : ''}{item.node_name ? ` · 由 ${item.node_name} 生成` : ''}</small>{item.error_message && <div className="entry-error">{item.error_message}</div>}<label className="move-project"><span>移到</span><select value={item.project_id || ''} onChange={e => moveToProject(item.id, e.target.value)}><option value="">未归类</option>{projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label></div>
        <div className="history-player">{item.audio_available ? <AudioPlayer src={`/v1/history/${item.id}/audio`} compact /> : <span className="audio-missing">{item.status === 'failed' ? '没有音频' : '音频已清理'}</span>}</div>
        <div className="history-actions">{item.audio_available && <a href={`/v1/history/${item.id}/audio`} download><Download /></a>}<button onClick={() => onReuse(item)} title="重新生成"><RefreshCw /></button><button onClick={() => remove(item.id)} title="删除"><Trash2 /></button></div>
      </article>)}
      {items.length === 0 && <div className="empty-state"><History /><h3>没有符合条件的记录</h3><p>生成一段声音后，它会自动出现在这里。</p></div>}
    </div>
    {total > 10 && <div className="pagination"><button disabled={page <= 1} onClick={() => setPage(p => p - 1)}>上一页</button><span>{page} / {Math.ceil(total / 10)}</span><button disabled={page >= Math.ceil(total / 10)} onClick={() => setPage(p => p + 1)}>下一页</button></div>}
  </div>
}
