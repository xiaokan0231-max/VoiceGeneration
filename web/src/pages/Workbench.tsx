import { useEffect, useMemo, useState } from 'react'
import { Check, ChevronDown, Copy, Download, LoaderCircle, Play, RefreshCw, SlidersHorizontal, Sparkles, X } from 'lucide-react'
import { api } from '../api'
import AudioPlayer from '../components/AudioPlayer'
import type { GenerationDraft, GenerationMode, HistoryItem, ModelInfo, ProjectDetail, VoiceInfo } from '../types'

const fallback: GenerationDraft = {
  text: '九一八事变后，东北局势急剧变化。', model: 'cosyvoice3', voice: 'narrator_zh',
  mode: 'clone', language: 'zh', speed: 1, format: 'wav',
  instruct_text: '沉稳克制、有历史厚重感的纪录片旁白，避免播音腔。', project_id: '',
}
const modeLabels: Record<GenerationMode, string> = { clone: '音色克隆', instruct: '指令控制', cross_lingual: '跨语言克隆' }

export default function Workbench({ initialDraft, historyVersion, projects, onProjectsChange, onGenerated }: { initialDraft?: GenerationDraft; historyVersion: number; projects: ProjectDetail[]; onProjectsChange: () => void; onGenerated: () => void }) {
  const [draft, setDraft] = useState<GenerationDraft>(initialDraft || fallback)
  const [models, setModels] = useState<ModelInfo[]>([])
  const [voices, setVoices] = useState<VoiceInfo[]>([])
  const [recent, setRecent] = useState<HistoryItem[]>([])
  const [audioUrl, setAudioUrl] = useState('')
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState('')
  const [generationId, setGenerationId] = useState('')
  const [parametersOpen, setParametersOpen] = useState(false)
  const [copiedId, setCopiedId] = useState('')

  useEffect(() => { if (initialDraft) setDraft(initialDraft) }, [initialDraft])
  useEffect(() => {
    if (!initialDraft) api.settings().then(value => setDraft(current => ({ ...current, model: value.default_model, format: value.default_format }))).catch(() => undefined)
  }, [])
  useEffect(() => { api.models().then(setModels).catch(e => setError(e.message)) }, [])
  useEffect(() => {
    api.voices(draft.model).then(list => {
      setVoices(list)
      if (list.length && !list.some(v => v.id === draft.voice)) setDraft(d => ({ ...d, voice: list[0].id }))
    }).catch(e => setError(e.message))
  }, [draft.model])
  useEffect(() => { api.history('page_size=4').then(r => setRecent(r.items)).catch(() => setRecent([])) }, [historyVersion])
  useEffect(() => () => { if (audioUrl.startsWith('blob:')) URL.revokeObjectURL(audioUrl) }, [audioUrl])

  const selectedVoice = useMemo(() => voices.find(v => v.id === draft.voice), [voices, draft.voice])
  const update = <K extends keyof GenerationDraft>(key: K, value: GenerationDraft[K]) => setDraft(d => ({ ...d, [key]: value }))
  const copyText = async (id: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopiedId(id)
      setTimeout(() => setCopiedId(current => (current === id ? '' : current)), 1500)
    } catch { setError('复制失败，请手动选择文本复制') }
  }
  const chooseProject = async (value: string) => {
    if (value !== '__new__') { update('project_id', value); return }
    const name = window.prompt('新项目名称')?.trim()
    if (!name) return
    try {
      const created = await api.saveProject({ name })
      onProjectsChange()
      update('project_id', created.id)
    } catch (e) { setError(e instanceof Error ? e.message : '创建项目失败') }
  }
  const generate = async () => {
    setError(''); setGenerating(true)
    try {
      const result = await api.synthesize(draft)
      setAudioUrl(result.url); setGenerationId(result.generationId); onGenerated()
      const history = await api.history('page_size=4'); setRecent(history.items)
    } catch (e) { setError(e instanceof Error ? e.message : '生成失败') }
    finally { setGenerating(false) }
  }

  return <div className="workbench-page">
    <section className="editor-column">
      <div className="page-heading"><div><span className="eyebrow">STUDIO</span><h1>生成工作台</h1></div><span className="model-note">COSYVOICE 3 · 本机推理</span></div>
      <section className="editor-block">
        <div className="section-line"><label htmlFor="script">输入文本</label><span>{draft.text.length} 字</span></div>
        <textarea id="script" value={draft.text} maxLength={5000} onChange={e => update('text', e.target.value)} placeholder="输入想要生成的内容……" />
        <div className="editor-hint">建议使用自然标点划分呼吸与停顿，长文本会自动分段合成。</div>
      </section>
      <section className="result-block">
        <div className="section-line"><label>生成结果</label>{generationId && <span className="success-text">生成完成</span>}</div>
        {audioUrl ? <>
          <AudioPlayer src={audioUrl} />
          <div className="result-actions"><a className="quiet-button" href={audioUrl} download={`voice-${generationId.slice(0, 8)}.${draft.format}`}><Download />下载音频</a><button className="quiet-button" onClick={generate}><RefreshCw />重新生成</button></div>
        </> : <div className="empty-audio"><span className="empty-wave">||||||||||||||||||||||||</span><p>生成后的真实音频波形会显示在这里</p></div>}
      </section>
      <section className="recent-block">
        <div className="section-line"><label>最近生成</label><span>保存在本机 MySQL</span></div>
        {recent.length === 0 ? <p className="muted">还没有生成记录</p> : recent.map(item => <div className="recent-row" key={item.id}>
          <span className={`history-status ${item.status}`} />
          <div><p>{item.text}</p><small>{item.voice_name} · {modeLabels[item.mode]} · {new Date(item.created_at).toLocaleString('zh-CN', { hour12: false })}</small></div>
          <span className="recent-duration">{item.duration_seconds ? `${item.duration_seconds.toFixed(1)}s` : item.status === 'failed' ? '失败' : '处理中'}</span>
          <button className={`recent-copy ${copiedId === item.id ? 'copied' : ''}`} title="复制文本" aria-label="复制文本" onClick={() => copyText(item.id, item.text)}>{copiedId === item.id ? <Check /> : <Copy />}</button>
        </div>)}
      </section>
    </section>
    <button className="mobile-parameter-button" onClick={() => setParametersOpen(true)}><SlidersHorizontal />生成参数</button>
    {parametersOpen && <button className="parameter-scrim" aria-label="关闭参数遮罩" onClick={() => setParametersOpen(false)} />}
    <aside className={`parameter-panel ${parametersOpen ? 'params-open' : ''}`}>
      <div className="parameter-title"><div><span className="eyebrow">CONTROL</span><h2>生成参数</h2></div><ChevronDown className="desktop-chevron" /><button className="drawer-close" aria-label="关闭参数" onClick={() => setParametersOpen(false)}><X /></button></div>
      <div className="field"><label>项目</label><div className="select-wrap"><select value={draft.project_id} onChange={e => chooseProject(e.target.value)}><option value="">未归类</option>{projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}<option value="__new__">＋ 新建项目…</option></select></div><small>把这次生成归到某个项目</small></div>
      <div className="field"><label>推理模型</label><div className="select-wrap"><select value={draft.model} onChange={e => setDraft(current => ({ ...current, model: e.target.value, mode: e.target.value === 'cosyvoice3' ? current.mode : 'clone' }))}>{models.map(m => <option key={m.id} value={m.id}>{m.id === 'cosyvoice3' ? 'CosyVoice 3' : m.description || m.id}</option>)}</select></div><small>优先使用最新 CosyVoice 3</small></div>
      <div className="field"><label>音色</label><select value={draft.voice} onChange={e => update('voice', e.target.value)}>{voices.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}</select><div className="voice-meta"><small>{selectedVoice?.language?.toUpperCase() || '—'} · {selectedVoice?.kind === 'builtin' ? '系统音色' : '克隆音色'}</small>{selectedVoice?.kind === 'clone' && <button onClick={() => { void new Audio(`/v1/voices/${draft.voice}/audio`).play() }}><Play />试听音色</button>}</div></div>
      <div className="field"><label>生成模式</label><div className="segmented three">{(['clone','instruct','cross_lingual'] as GenerationMode[]).map(m => <button key={m} disabled={draft.model !== 'cosyvoice3' && m !== 'clone'} className={draft.mode === m ? 'active' : ''} onClick={() => update('mode', m)}>{m === 'clone' ? '克隆' : m === 'instruct' ? '指令' : '跨语言'}</button>)}</div></div>
      <div className="field-row"><div className="field"><label>语言</label><select value={draft.language} onChange={e => update('language', e.target.value)}><option value="zh">中文</option><option value="ja">日语</option><option value="en">英语</option><option value="auto">自动识别</option></select></div><div className="field"><label>格式</label><select value={draft.format} onChange={e => update('format', e.target.value)}><option value="wav">WAV</option><option value="mp3">MP3</option><option value="opus">OPUS</option></select></div></div>
      <div className="field"><div className="range-label"><label>语速</label><output>{draft.speed.toFixed(1)}×</output></div><input type="range" min="0.5" max="2" step="0.1" value={draft.speed} onChange={e => update('speed', Number(e.target.value))} /></div>
      <div className="field"><label>{draft.mode === 'instruct' ? '风格指令（必填）' : '风格指令'}</label><textarea className="instruction" value={draft.instruct_text} onChange={e => update('instruct_text', e.target.value)} placeholder="例如：沉稳、克制、有纪录片质感" /><small>{draft.mode === 'instruct' ? 'CosyVoice 3 会按此指令控制语气' : '克隆与跨语言模式保留备用'}</small></div>
      {error && <div className="inline-error">{error}</div>}
      <button className="generate-button" disabled={generating || !draft.text.trim() || !draft.voice || (draft.mode === 'instruct' && !draft.instruct_text.trim())} onClick={generate}>{generating ? <LoaderCircle className="spin" /> : <Sparkles />}{generating ? '正在本机生成…' : '生成语音'}</button>
      <p className="privacy-note">音频与文字仅在这台 Mac 上处理</p>
    </aside>
  </div>
}
