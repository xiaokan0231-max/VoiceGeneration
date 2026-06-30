import { useEffect, useRef, useState } from 'react'
import { FileAudio, Mic, Plus, Save, Square, Trash2, Upload } from 'lucide-react'
import { api } from '../api'
import AudioPlayer from '../components/AudioPlayer'
import { ConfirmDialog, Dialog, useToast } from '../components/Feedback'
import type { VoiceDetail } from '../types'

interface FormState { id: string; existing: boolean; name: string; language: string; refText: string; models: string[]; file?: File }
const blank: FormState = { id: '', existing: false, name: '', language: 'zh', refText: '', models: ['cosyvoice3', 'f5_tts'] }

export default function VoiceLibrary() {
  const [voices, setVoices] = useState<VoiceDetail[]>([])
  const [editing, setEditing] = useState<FormState | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [recording, setRecording] = useState(false)
  const [warming, setWarming] = useState(false)
  const [deleting, setDeleting] = useState<VoiceDetail | null>(null)
  const [deleteBusy, setDeleteBusy] = useState(false)
  const recorder = useRef<MediaRecorder | null>(null)
  const chunks = useRef<Blob[]>([])
  const { notify } = useToast()

  const load = () => api.voiceLibrary().then(setVoices).catch(e => setError(e.message))
  useEffect(() => { void load() }, [])
  const edit = (voice: VoiceDetail) => setEditing({ id: voice.id, existing: true, name: voice.name, language: voice.language, refText: voice.ref_text, models: voice.models })
  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setEditing(f => f ? ({ ...f, [key]: value }) : f)

  const startRecord = async () => {
    setError('')
    try {
      // 关闭回声消除/降噪/自动增益：克隆需要原始保真，且 AGC 爬升会让开头变弱
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      })
      // 先让活的麦克风流预热 ~0.5s（硬件/驱动死区 + 开录瞬间的爆音都发生在这段），
      // 之后再 start() 开始捕获——这样死区和爆音不会进入录音文件，也不会吞掉第一个字。
      setWarming(true)
      await new Promise(resolve => setTimeout(resolve, 500))
      const next = new MediaRecorder(stream); chunks.current = []
      next.ondataavailable = e => chunks.current.push(e.data)
      next.onstop = () => {
        const blob = new Blob(chunks.current, { type: next.mimeType || 'audio/webm' })
        set('file', new File([blob], 'browser-recording.webm', { type: blob.type }))
        stream.getTracks().forEach(track => track.stop())
      }
      recorder.current = next; next.start()
      setWarming(false); setRecording(true)
    } catch { setError('无法使用麦克风，请在系统设置中允许浏览器录音。') }
  }
  const stopRecord = () => { recorder.current?.stop(); setRecording(false) }
  const save = async () => {
    if (!editing) return
    if (!editing.existing && !editing.file) { setError('新建音色必须上传或录制参考音频。'); return }
    setSaving(true); setError('')
    const form = new FormData()
    form.set('name', editing.name); form.set('language', editing.language); form.set('ref_text', editing.refText); form.set('models', JSON.stringify(editing.models))
    if (!editing.existing && editing.id) form.set('voice_id', editing.id)
    if (editing.file) form.set('audio', editing.file)
    try { await api.saveVoice(form, editing.existing ? editing.id : undefined); setEditing(null); await load(); notify('音色已保存', 'success') }
    catch (e) { setError(e instanceof Error ? e.message : '保存失败') }
    finally { setSaving(false) }
  }
  const remove = async () => {
    if (!deleting) return
    setDeleteBusy(true)
    try { await api.deleteVoice(deleting.id); setDeleting(null); await load(); notify('音色已删除', 'success') }
    catch (e) { setError(e instanceof Error ? e.message : '删除失败') }
    finally { setDeleteBusy(false) }
  }
  const closeEditor = () => {
    if (recording) recorder.current?.stop()
    recorder.current?.stream.getTracks().forEach(track => track.stop())
    setRecording(false); setWarming(false); setEditing(null)
  }

  return <div className="content-page voice-library-page">
    <div className="page-heading large"><div><span className="eyebrow">VOICE LIBRARY</span><h1>音色库</h1><p>管理用于 CosyVoice 3 克隆的本机参考声音</p></div><button className="primary-small" onClick={() => setEditing(blank)}><Plus />新建音色</button></div>
    <div className="info-strip"><Mic /><span>参考音频会统一转换为 16kHz 单声道 WAV，建议 5–15 秒、环境安静、语气自然。</span></div>
    {error && <div className="inline-error page-error" role="alert">{error}</div>}
    <div className="voice-list">
      {voices.map(voice => <article className="voice-row" key={voice.id}>
        <div className="voice-avatar">{voice.name.slice(0, 1)}</div>
        <div className="voice-copy"><h3>{voice.name}</h3><p>{voice.ref_text}</p><small>{voice.id} · {voice.language.toUpperCase()} · {voice.models.join(' / ')}</small></div>
        <div className="voice-preview"><AudioPlayer src={voice.audio_url} compact /></div>
        <div className="row-actions"><button onClick={() => edit(voice)}>编辑</button><button className="danger-icon" onClick={() => setDeleting(voice)} aria-label="删除音色"><Trash2 /></button></div>
      </article>)}
      {voices.length === 0 && <div className="empty-state"><FileAudio /><h3>还没有克隆音色</h3><p>上传一段清晰参考录音，即可开始使用 CosyVoice 3 音色克隆。</p></div>}
    </div>
    <Dialog open={Boolean(editing)} title={editing?.existing ? '编辑音色' : '新建音色'} eyebrow="VOICE PROFILE" onClose={closeEditor} footer={<><button className="quiet-button" disabled={saving} onClick={closeEditor}>取消</button><button className="primary-small" disabled={saving} onClick={save}><Save />{saving ? '保存中…' : '保存音色'}</button></>}>
      {editing && <>
        <div className="field-row"><div className="field"><label htmlFor="voice-name">音色名称</label><input id="voice-name" data-autofocus value={editing.name} onChange={e => set('name', e.target.value)} placeholder="例如：沉稳男声" /></div><div className="field"><label htmlFor="voice-language">语言</label><select id="voice-language" value={editing.language} onChange={e => set('language', e.target.value)}><option value="zh">中文</option><option value="ja">日语</option><option value="en">英语</option></select></div></div>
        {!editing.existing && <div className="field"><label htmlFor="voice-id">音色 ID（可选）</label><input id="voice-id" value={editing.id} onChange={e => set('id', e.target.value)} placeholder="留空则自动生成" /></div>}
        <div className="field"><label htmlFor="voice-transcript">参考音频逐字稿</label><textarea id="voice-transcript" value={editing.refText} onChange={e => set('refText', e.target.value)} placeholder="请准确填写录音中说的文字" /></div>
        <label className="upload-zone"><input type="file" accept="audio/wav,audio/mpeg,audio/mp4,audio/webm" onChange={e => set('file', e.target.files?.[0])} /><Upload /><strong>{editing.file ? editing.file.name : editing.existing ? '保留当前参考音频，或点击替换' : '点击选择 WAV / MP3 / WebM'}</strong><span>最大 20MB，时长 3–30 秒</span></label>
        <div className="record-line"><span>{warming ? '麦克风准备中，请稍候…' : recording ? '正在录音，请开始说话' : '也可以直接用 Mac 麦克风录制'}</span><button className={recording ? 'recording' : ''} disabled={warming} onClick={recording ? stopRecord : startRecord}>{recording ? <Square /> : <Mic />}{warming ? '准备中…' : recording ? '停止并使用录音' : '开始录音'}</button></div>
        <div className="check-row"><label><input type="checkbox" checked={editing.models.includes('cosyvoice3')} onChange={e => set('models', e.target.checked ? [...editing.models, 'cosyvoice3'] : editing.models.filter(x => x !== 'cosyvoice3'))} /> CosyVoice 3</label><label><input type="checkbox" checked={editing.models.includes('f5_tts')} onChange={e => set('models', e.target.checked ? [...editing.models, 'f5_tts'] : editing.models.filter(x => x !== 'f5_tts'))} /> F5-TTS</label></div>
      </>}
    </Dialog>
    <ConfirmDialog open={Boolean(deleting)} title={`删除音色“${deleting?.name || ''}”？`} description="音色资料和参考音频会被删除，既有历史成品不会受到影响。" confirmLabel="删除音色" busy={deleteBusy} onCancel={() => setDeleting(null)} onConfirm={remove} />
  </div>
}
