import { useEffect, useMemo, useState } from 'react'
import { Check, ChevronDown, Copy, Download, LoaderCircle, RefreshCw, RotateCcw, Save, SlidersHorizontal, Sparkles, X } from 'lucide-react'
import { api } from '../api'
import AudioPlayer from '../components/AudioPlayer'
import { ConfirmDialog, Dialog, useToast } from '../components/Feedback'
import { useGeneration } from '../context/GenerationContext'
import type { GenerationMode, HistoryItem, ModelInfo, ProjectDetail, VoiceInfo } from '../types'

const modeLabels: Record<GenerationMode, string> = { clone: '音色克隆', instruct: '指令控制', cross_lingual: '跨语言克隆' }

export default function Workbench({ projects, onProjectsChange }: { projects: ProjectDetail[]; onProjectsChange: () => void }) {
  const { draft, setDraft, resetDraft, savedAt, tasks, latestTask, historyVersion, startGeneration } = useGeneration()
  const [models, setModels] = useState<ModelInfo[]>([])
  const [voices, setVoices] = useState<VoiceInfo[]>([])
  const [recent, setRecent] = useState<HistoryItem[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [currentTaskId, setCurrentTaskId] = useState('')
  const [parametersOpen, setParametersOpen] = useState(false)
  const [copiedId, setCopiedId] = useState('')
  const [newProjectOpen, setNewProjectOpen] = useState(false)
  const [newProjectName, setNewProjectName] = useState('')
  const [savingProject, setSavingProject] = useState(false)
  const [resetOpen, setResetOpen] = useState(false)
  const { notify } = useToast()

  useEffect(() => { api.models().then(setModels).catch(value => setError(value.message)) }, [])
  useEffect(() => {
    api.voices(draft.model).then(list => {
      setVoices(list)
      if (list.length && !list.some(voice => voice.id === draft.voice)) setDraft(value => ({ ...value, voice: list[0].id }))
    }).catch(value => setError(value.message))
  }, [draft.model, draft.voice, setDraft])
  useEffect(() => { api.history('page_size=4').then(result => setRecent(result.items)).catch(() => setRecent([])) }, [historyVersion])

  const currentTask = useMemo(() => tasks.find(task => task.id === currentTaskId) || latestTask, [tasks, currentTaskId, latestTask])
  const selectedVoice = useMemo(() => voices.find(voice => voice.id === draft.voice), [voices, draft.voice])
  const update = <K extends keyof typeof draft>(key: K, value: typeof draft[K]) => setDraft(current => ({ ...current, [key]: value }))
  const canGenerate = Boolean(draft.text.trim() && draft.voice && (draft.mode !== 'instruct' || draft.instruct_text.trim()))

  const copyText = async (id: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text); setCopiedId(id)
      window.setTimeout(() => setCopiedId(current => current === id ? '' : current), 1500)
    } catch { notify('复制失败，请手动选择文本复制', 'error') }
  }
  const generate = async () => {
    if (!canGenerate || submitting) return
    setError(''); setSubmitting(true)
    try { const task = await startGeneration(draft); setCurrentTaskId(task.id) }
    catch (value) { setError(value instanceof Error ? value.message : '提交生成任务失败') }
    finally { setSubmitting(false) }
  }
  const createProject = async () => {
    const name = newProjectName.trim()
    if (!name) return
    setSavingProject(true)
    try {
      const created = await api.saveProject({ name }); await onProjectsChange()
      update('project_id', created.id); setNewProjectOpen(false); setNewProjectName('')
      notify(`项目“${created.name}”已创建`, 'success')
    } catch (value) { setError(value instanceof Error ? value.message : '创建项目失败') }
    finally { setSavingProject(false) }
  }

  return <div className="workbench-page">
    <section className="editor-column">
      <div className="page-heading"><div><span className="eyebrow">STUDIO</span><h1>生成工作台</h1></div><div className="draft-status"><span>{savedAt ? '草稿已自动保存' : '正在保存草稿'}</span><button onClick={() => setResetOpen(true)}><RotateCcw />重置</button></div></div>
      <section className="editor-block">
        <div className="section-line"><label htmlFor="script">输入文本</label><span>{draft.text.length} 字</span></div>
        <textarea id="script" value={draft.text} maxLength={5000} onChange={event => update('text', event.target.value)} onKeyDown={event => { if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') { event.preventDefault(); void generate() } }} placeholder="输入想要生成的内容……" />
        <div className="editor-hint">建议使用自然标点划分呼吸与停顿。按 ⌘ Enter 可快速生成。</div>
      </section>
      <section className="result-block">
        <div className="section-line"><label>生成结果</label>{currentTask && <span className={`task-inline-status ${currentTask.status}`}>{currentTask.status === 'completed' ? '生成完成' : currentTask.status === 'queued' ? '正在排队' : currentTask.status === 'failed' ? '生成失败' : currentTask.status === 'cancelled' ? '已取消' : `由 ${currentTask.node_name || 'Worker'} 生成中`}</span>}</div>
        {currentTask?.status === 'completed' && currentTask.audio_url ? <>
          <AudioPlayer src={currentTask.audio_url} />
          <div className="result-actions"><a className="quiet-button" href={currentTask.audio_url} download={`voice-${currentTask.id.slice(0, 8)}.${currentTask.format}`}><Download />下载音频</a><button className="quiet-button" onClick={generate}><RefreshCw />重新生成</button></div>
        </> : currentTask && !['failed', 'cancelled'].includes(currentTask.status) ? <div className="generation-pending"><LoaderCircle className="spin" /><div><strong>{currentTask.status === 'queued' ? '等待可用 Worker' : '正在生成音频'}</strong><p>{currentTask.node_name ? `${currentTask.node_name} 已领取任务，可放心切换页面。` : '任务已进入队列，可放心切换页面。'}</p></div></div> : <div className="empty-audio"><span className="empty-wave">||||||||||||||||||||||||</span><p>{currentTask?.error_message || '生成后的真实音频波形会显示在这里'}</p></div>}
      </section>
      <section className="recent-block">
        <div className="section-line"><label>最近生成</label><span>保存在本机 MySQL</span></div>
        {recent.length === 0 ? <p className="muted">还没有生成记录</p> : recent.map(item => <div className="recent-row" key={item.id}>
          <span className={`history-status ${item.status}`} />
          <div><p>{item.text}</p><small>{item.voice_name} · {modeLabels[item.mode]} · {new Date(item.created_at).toLocaleString('zh-CN', { hour12: false })}</small></div>
          <span className="recent-duration">{item.duration_seconds ? `${item.duration_seconds.toFixed(1)}s` : item.status === 'failed' ? '失败' : item.status === 'cancelled' ? '已取消' : '处理中'}</span>
          <button className={`recent-copy ${copiedId === item.id ? 'copied' : ''}`} aria-label="复制文本" onClick={() => copyText(item.id, item.text)}>{copiedId === item.id ? <Check /> : <Copy />}</button>
        </div>)}
      </section>
    </section>
    <button className="mobile-parameter-button" onClick={() => setParametersOpen(true)}><SlidersHorizontal />生成参数</button>
    {parametersOpen && <button className="parameter-scrim" aria-label="关闭参数遮罩" onClick={() => setParametersOpen(false)} />}
    <aside className={`parameter-panel ${parametersOpen ? 'params-open' : ''}`}>
      <div className="parameter-title"><div><span className="eyebrow">CONTROL</span><h2>生成参数</h2></div><ChevronDown className="desktop-chevron" /><button className="drawer-close" aria-label="关闭参数" onClick={() => setParametersOpen(false)}><X /></button></div>
      <div className="field"><label>项目</label><div className="select-wrap"><select value={draft.project_id} onChange={event => { if (event.target.value === '__new__') setNewProjectOpen(true); else update('project_id', event.target.value) }}><option value="">未归类</option>{projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}<option value="__new__">＋ 新建项目…</option></select></div><small>把这次生成归到某个项目</small></div>
      <div className="field"><label>推理模型</label><div className="select-wrap"><select value={draft.model} onChange={event => setDraft(current => ({ ...current, model: event.target.value, mode: event.target.value === 'cosyvoice3' ? current.mode : 'clone' }))}>{models.map(model => <option key={model.id} value={model.id}>{model.id === 'cosyvoice3' ? 'CosyVoice 3' : model.description || model.id}</option>)}</select></div><small>优先使用最新 CosyVoice 3</small></div>
      <div className="field"><label>音色</label><select value={draft.voice} onChange={event => update('voice', event.target.value)}>{voices.map(voice => <option key={voice.id} value={voice.id}>{voice.name}</option>)}</select><div className="voice-meta"><small>{selectedVoice?.language?.toUpperCase() || '—'} · {selectedVoice?.kind === 'builtin' ? '系统音色' : '克隆音色'}</small></div>{selectedVoice?.kind === 'clone' && <div className="voice-mini-preview"><AudioPlayer src={`/v1/voices/${draft.voice}/audio`} compact /></div>}</div>
      <div className="field"><label>生成模式</label><div className="segmented three">{(['clone', 'instruct', 'cross_lingual'] as GenerationMode[]).map(mode => <button key={mode} disabled={draft.model !== 'cosyvoice3' && mode !== 'clone'} className={draft.mode === mode ? 'active' : ''} onClick={() => update('mode', mode)}>{mode === 'clone' ? '克隆' : mode === 'instruct' ? '指令' : '跨语言'}</button>)}</div></div>
      <div className="field-row"><div className="field"><label>语言</label><select value={draft.language} onChange={event => update('language', event.target.value)}><option value="zh">中文</option><option value="ja">日语</option><option value="en">英语</option><option value="auto">自动识别</option></select></div><div className="field"><label>格式</label><select value={draft.format} onChange={event => update('format', event.target.value)}><option value="wav">WAV</option><option value="mp3">MP3</option><option value="opus">OPUS</option></select></div></div>
      <div className="field"><div className="range-label"><label>语速</label><output>{draft.speed.toFixed(1)}×</output></div><input type="range" min="0.5" max="2" step="0.1" value={draft.speed} onChange={event => update('speed', Number(event.target.value))} /></div>
      <div className="field"><label>{draft.mode === 'instruct' ? '风格指令（必填）' : '风格指令'}</label><textarea className="instruction" value={draft.instruct_text} onChange={event => update('instruct_text', event.target.value)} placeholder="例如：沉稳、克制、有纪录片质感" /><small>{draft.mode === 'instruct' ? 'CosyVoice 3 会按此指令控制语气' : '克隆与跨语言模式保留备用'}</small></div>
      {error && <div className="inline-error" role="alert">{error}</div>}
      <button className="generate-button" disabled={submitting || !canGenerate} onClick={generate}>{submitting ? <LoaderCircle className="spin" /> : <Sparkles />}{submitting ? '正在提交任务…' : '生成语音'}</button>
      <p className="privacy-note">音频与文字仅在这台 Mac 上处理</p>
    </aside>
    <Dialog open={newProjectOpen} title="新建项目" eyebrow="PROJECT" onClose={() => setNewProjectOpen(false)} footer={<><button className="quiet-button" onClick={() => setNewProjectOpen(false)}>取消</button><button className="primary-small" disabled={savingProject || !newProjectName.trim()} onClick={createProject}><Save />{savingProject ? '创建中…' : '创建项目'}</button></>}>
      <div className="field"><label htmlFor="quick-project-name">项目名称</label><input id="quick-project-name" data-autofocus value={newProjectName} onChange={event => setNewProjectName(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') void createProject() }} placeholder="例如：历史纪录片" /></div>
    </Dialog>
    <ConfirmDialog open={resetOpen} title="重置工作台草稿？" description="文本、模型、音色与生成参数会恢复为默认值。" confirmLabel="重置草稿" onCancel={() => setResetOpen(false)} onConfirm={() => { resetDraft(); setResetOpen(false); notify('工作台草稿已重置', 'info') }} />
  </div>
}
