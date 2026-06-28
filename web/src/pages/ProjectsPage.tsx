import { useState } from 'react'
import { FolderKanban, ListMusic, Plus, Save, Trash2, X } from 'lucide-react'
import { api } from '../api'
import type { ProjectDetail } from '../types'

interface FormState { id: string; existing: boolean; name: string; description: string; color: string }
const PALETTE = ['#d98d52', '#5b8c6e', '#5a7fae', '#9a6cae', '#b65c6b', '#7d878d']
const blank: FormState = { id: '', existing: false, name: '', description: '', color: PALETTE[0] }

export default function ProjectsPage({ projects, onChange, onViewGenerations }: {
  projects: ProjectDetail[]
  onChange: () => void
  onViewGenerations: (projectId: string) => void
}) {
  const [editing, setEditing] = useState<FormState | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setEditing(f => f ? ({ ...f, [key]: value }) : f)
  const edit = (p: ProjectDetail) => setEditing({ id: p.id, existing: true, name: p.name, description: p.description || '', color: p.color || PALETTE[0] })

  const save = async () => {
    if (!editing || !editing.name.trim()) { setError('项目名称必填'); return }
    setSaving(true); setError('')
    try {
      await api.saveProject({ name: editing.name.trim(), description: editing.description.trim(), color: editing.color }, editing.existing ? editing.id : undefined)
      setEditing(null); onChange()
    } catch (e) { setError(e instanceof Error ? e.message : '保存失败') }
    finally { setSaving(false) }
  }
  const remove = async (p: ProjectDetail) => {
    if (!window.confirm(`删除项目“${p.name}”？该项目下的生成记录会变为“未归类”，音频不受影响。`)) return
    try { await api.deleteProject(p.id); onChange() } catch (e) { setError(e instanceof Error ? e.message : '删除失败') }
  }

  return <div className="content-page">
    <div className="page-heading large"><div><span className="eyebrow">PROJECTS</span><h1>项目</h1><p>按项目归类与管理生成记录</p></div><button className="primary-small" onClick={() => { setError(''); setEditing(blank) }}><Plus />新建项目</button></div>
    <div className="info-strip"><FolderKanban /><span>生成时在工作台选择项目；这里可新建/改名/删除，并查看每个项目下的生成。</span></div>
    {error && <div className="inline-error page-error">{error}</div>}
    <div className="voice-list">
      {projects.map(p => <article className="voice-row" key={p.id}>
        <div className="voice-avatar" style={{ background: p.color || PALETTE[0] }}>{p.name.slice(0, 1)}</div>
        <div className="voice-copy"><h3>{p.name}</h3><p>{p.description || '—'}</p><small>{p.generation_count} 条生成 · {new Date(p.created_at).toLocaleDateString('zh-CN')}</small></div>
        <div className="row-actions"><button onClick={() => onViewGenerations(p.id)}><ListMusic />查看生成</button><button onClick={() => edit(p)}>编辑</button><button className="danger-icon" onClick={() => remove(p)} aria-label="删除项目"><Trash2 /></button></div>
      </article>)}
      {projects.length === 0 && <div className="empty-state"><FolderKanban /><h3>还没有项目</h3><p>新建一个项目，生成时就能把音频归到它名下。</p></div>}
    </div>
    {editing && <div className="dialog-backdrop"><section className="dialog-panel" role="dialog" aria-modal="true">
      <div className="dialog-head"><div><span className="eyebrow">PROJECT</span><h2>{editing.existing ? '编辑项目' : '新建项目'}</h2></div><button className="icon-button" onClick={() => setEditing(null)}><X /></button></div>
      <div className="field"><label>项目名称</label><input value={editing.name} onChange={e => set('name', e.target.value)} placeholder="例如：歴史-序章" /></div>
      <div className="field"><label>描述（可选）</label><textarea value={editing.description} onChange={e => set('description', e.target.value)} placeholder="这个项目用来做什么" /></div>
      <div className="field"><label>标签色</label><div className="color-swatches">{PALETTE.map(c => <button key={c} type="button" className={`swatch ${editing.color === c ? 'active' : ''}`} style={{ background: c }} aria-label={c} onClick={() => set('color', c)} />)}</div></div>
      <div className="dialog-actions"><button className="quiet-button" onClick={() => setEditing(null)}>取消</button><button className="primary-small" disabled={saving} onClick={save}><Save />{saving ? '保存中…' : '保存项目'}</button></div>
    </section></div>}
  </div>
}
