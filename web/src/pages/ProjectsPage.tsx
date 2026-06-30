import { useState } from 'react'
import { FolderKanban, ListMusic, Plus, Save, Trash2 } from 'lucide-react'
import { api } from '../api'
import { ConfirmDialog, Dialog, useToast } from '../components/Feedback'
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
  const [deleting, setDeleting] = useState<ProjectDetail | null>(null)
  const [deleteBusy, setDeleteBusy] = useState(false)
  const { notify } = useToast()

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setEditing(f => f ? ({ ...f, [key]: value }) : f)
  const edit = (p: ProjectDetail) => setEditing({ id: p.id, existing: true, name: p.name, description: p.description || '', color: p.color || PALETTE[0] })

  const save = async () => {
    if (!editing || !editing.name.trim()) { setError('项目名称必填'); return }
    setSaving(true); setError('')
    try {
      await api.saveProject({ name: editing.name.trim(), description: editing.description.trim(), color: editing.color }, editing.existing ? editing.id : undefined)
      setEditing(null); onChange(); notify('项目已保存', 'success')
    } catch (e) { setError(e instanceof Error ? e.message : '保存失败') }
    finally { setSaving(false) }
  }
  const remove = async () => {
    if (!deleting) return
    setDeleteBusy(true)
    try { await api.deleteProject(deleting.id); setDeleting(null); onChange(); notify('项目已删除，生成记录已移到未归类', 'success') }
    catch (e) { setError(e instanceof Error ? e.message : '删除失败') }
    finally { setDeleteBusy(false) }
  }

  return <div className="content-page projects-page">
    <div className="page-heading large"><div><span className="eyebrow">PROJECTS</span><h1>项目</h1><p>按项目归类与管理生成记录</p></div><button className="primary-small" onClick={() => { setError(''); setEditing(blank) }}><Plus />新建项目</button></div>
    <div className="info-strip"><FolderKanban /><span>生成时在工作台选择项目；这里可新建/改名/删除，并查看每个项目下的生成。</span></div>
    {error && <div className="inline-error page-error" role="alert">{error}</div>}
    <div className="voice-list">
      {projects.map(p => <article className="voice-row project-row" key={p.id}>
        <div className="voice-avatar" style={{ background: p.color || PALETTE[0] }}>{p.name.slice(0, 1)}</div>
        <div className="voice-copy"><h3>{p.name}</h3><p>{p.description || '—'}</p><small>{p.generation_count} 条生成 · {new Date(p.created_at).toLocaleDateString('zh-CN')}</small></div>
        <div className="row-actions"><button onClick={() => onViewGenerations(p.id)}><ListMusic />查看生成</button><button onClick={() => edit(p)}>编辑</button><button className="danger-icon" onClick={() => setDeleting(p)} aria-label="删除项目"><Trash2 /></button></div>
      </article>)}
      {projects.length === 0 && <div className="empty-state"><FolderKanban /><h3>还没有项目</h3><p>新建一个项目，生成时就能把音频归到它名下。</p></div>}
    </div>
    <Dialog open={Boolean(editing)} title={editing?.existing ? '编辑项目' : '新建项目'} eyebrow="PROJECT" onClose={() => setEditing(null)} footer={<><button className="quiet-button" disabled={saving} onClick={() => setEditing(null)}>取消</button><button className="primary-small" disabled={saving} onClick={save}><Save />{saving ? '保存中…' : '保存项目'}</button></>}>
      {editing && <>
        <div className="field"><label htmlFor="project-name">项目名称</label><input id="project-name" data-autofocus value={editing.name} onChange={e => set('name', e.target.value)} placeholder="例如：历史-序章" /></div>
        <div className="field"><label htmlFor="project-description">描述（可选）</label><textarea id="project-description" value={editing.description} onChange={e => set('description', e.target.value)} placeholder="这个项目用来做什么" /></div>
        <div className="field"><label>标签色</label><div className="color-swatches">{PALETTE.map((color, index) => <button key={color} type="button" className={`swatch ${editing.color === color ? 'active' : ''}`} style={{ background: color }} aria-label={`选择标签色 ${index + 1}`} onClick={() => set('color', color)} />)}</div></div>
      </>}
    </Dialog>
    <ConfirmDialog open={Boolean(deleting)} title={`删除项目“${deleting?.name || ''}”？`} description="该项目下的生成记录会移到“未归类”，音频文件不会删除。" confirmLabel="删除项目" busy={deleteBusy} onCancel={() => setDeleting(null)} onConfirm={remove} />
  </div>
}
