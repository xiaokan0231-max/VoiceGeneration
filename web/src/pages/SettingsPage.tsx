import { useEffect, useState } from 'react'
import { Cpu, Database, HardDrive, LoaderCircle, Network, Play, Power, RefreshCw, Save, Server, Square } from 'lucide-react'
import { api } from '../api'
import type { ClusterInfo, RuntimeModel, SettingsInfo, SystemInfo } from '../types'

const bytes = (value: number) => value < 1024 ** 2 ? `${(value / 1024).toFixed(0)} KB` : value < 1024 ** 3 ? `${(value / 1024 ** 2).toFixed(1)} MB` : `${(value / 1024 ** 3).toFixed(2)} GB`

export default function SettingsPage({ system, onSystemChange }: { system: SystemInfo | null; onSystemChange: () => void }) {
  const [settings, setSettings] = useState<SettingsInfo | null>(null)
  const [models, setModels] = useState<RuntimeModel[]>([])
  const [cluster, setCluster] = useState<ClusterInfo | null>(null)
  const [busy, setBusy] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const load = () => api.settings().then(value => { setSettings(value); setModels(value.models) }).catch(e => setError(e.message))
  const loadCluster = () => api.cluster().then(setCluster).catch(() => setCluster(null))
  useEffect(() => { void load(); void loadCluster(); const id = window.setInterval(loadCluster, 5000); return () => window.clearInterval(id) }, [])
  const updateSetting = <K extends keyof SettingsInfo>(key: K, value: SettingsInfo[K]) => setSettings(s => s ? ({ ...s, [key]: value }) : s)
  const updateModel = (id: string, update: Partial<RuntimeModel>) => setModels(list => list.map(m => m.id === id ? { ...m, ...update } : m))
  const updateOption = (id: string, key: string, value: unknown) => setModels(list => list.map(m => m.id === id ? { ...m, options: { ...m.options, [key]: value } } : m))
  const saveGeneral = async () => {
    if (!settings) return; setBusy('general'); setError('')
    try { const next = await api.saveSettings({ default_model: settings.default_model, default_format: settings.default_format, worker_idle_timeout: Number(settings.worker_idle_timeout), worker_start_timeout: Number(settings.worker_start_timeout), cache_max_gb: Number(settings.cache_max_gb) }); setSettings(next); setModels(next.models); setMessage('全局设置已保存') }
    catch (e) { setError(e instanceof Error ? e.message : '保存失败') } finally { setBusy('') }
  }
  const saveModel = async (model: RuntimeModel) => {
    setBusy(`save-${model.id}`); setError('')
    try { await api.saveModel(model.id, { enabled: model.enabled, python: model.python, port: Number(model.port), options: model.options }); setMessage(`${model.id} 配置已保存`); await load(); onSystemChange() }
    catch (e) { setError(e instanceof Error ? e.message : '保存失败') } finally { setBusy('') }
  }
  const action = async (model: RuntimeModel, name: 'start' | 'stop' | 'restart') => {
    setBusy(`${name}-${model.id}`); setError('')
    try { await api.modelAction(model.id, name); setMessage(`${model.id} 已${name === 'start' ? '启动' : name === 'stop' ? '停止' : '重启'}`); await load(); onSystemChange() }
    catch (e) { setError(e instanceof Error ? e.message : '操作失败') } finally { setBusy('') }
  }
  const shutdown = async () => { if (!window.confirm('停止网关和全部模型进程？之后可再次双击 VoiceGeneration.app 启动。')) return; await api.shutdown(); setMessage('服务正在停止…') }

  return <div className="content-page settings-page">
    <div className="page-heading large"><div><span className="eyebrow">LOCAL SERVICE</span><h1>服务设置</h1><p>检查本机运行状态，并管理模型与高级配置</p></div><button className="danger-button" onClick={shutdown}><Power />停止服务</button></div>
    <div className="health-grid">
      <div className="health-item"><Database /><span>MySQL</span><strong className={system?.database === 'online' ? 'online' : 'offline'}>{system?.database === 'online' ? '在线' : '离线'}</strong><small>voice_generation</small></div>
      <div className="health-item"><HardDrive /><span>音频缓存</span><strong>{system ? bytes(system.cache_bytes) : '—'}</strong><small>上限 {system ? bytes(system.cache_limit_bytes) : '—'}</small></div>
      <div className="health-item"><Cpu /><span>Apple MPS</span><strong className={system?.mps ? 'online' : ''}>{system?.mps ? '可用' : '不可用'}</strong><small>{system?.apple_silicon ? 'Apple Silicon' : '当前设备'}</small></div>
      <div className="health-item"><Server /><span>网关服务</span><strong className={system ? 'online' : 'offline'}>{system ? '在线' : '离线'}</strong><small>127.0.0.1:8080</small></div>
    </div>
    {(message || error) && <div className={error ? 'inline-error page-error' : 'success-banner'}>{error || message}</div>}
    {settings && <section className="settings-section"><div className="settings-title"><div><h2>全局设置</h2><p>工作台默认值与本机资源回收策略</p></div><button className="primary-small" onClick={saveGeneral} disabled={busy === 'general'}><Save />保存</button></div><div className="settings-form general-form">
      <div className="field"><label>默认模型</label><select value={settings.default_model} onChange={e => updateSetting('default_model', e.target.value)}>{models.filter(m => m.enabled).map(m => <option key={m.id}>{m.id}</option>)}</select></div>
      <div className="field"><label>默认格式</label><select value={settings.default_format} onChange={e => updateSetting('default_format', e.target.value)}><option>wav</option><option>mp3</option><option>opus</option></select></div>
      <div className="field"><label>空闲回收（秒）</label><input type="number" min="30" value={settings.worker_idle_timeout} onChange={e => updateSetting('worker_idle_timeout', Number(e.target.value))} /></div>
      <div className="field"><label>启动超时（秒）</label><input type="number" min="30" value={settings.worker_start_timeout} onChange={e => updateSetting('worker_start_timeout', Number(e.target.value))} /></div>
      <div className="field"><label>缓存上限（GB）</label><input type="number" min=".1" step=".1" value={settings.cache_max_gb} onChange={e => updateSetting('cache_max_gb', Number(e.target.value))} /></div>
    </div></section>}
    {cluster && <section className="settings-section"><div className="settings-title"><div><h2>集群</h2><p>本机：{cluster.self.node_name}（{cluster.self.role}{cluster.self.coordinator_runs_jobs ? ' · 参与生成' : ' · 仅协调'}）· 队列 {cluster.queue_depth} 条待处理</p></div><Network /></div>
      <div className="node-list">
        {cluster.nodes.map(n => <div className="node-row" key={n.node_id}>
          <span className={`status-dot ${n.status === 'online' ? '' : 'idle'}`} />
          <div className="node-copy"><h3>{n.name}</h3><small>{n.node_id} · {n.role} · {n.models.join(' / ') || '无模型'} · 并发 {n.max_concurrency}</small></div>
          <span className={n.status === 'online' ? 'online' : 'offline'}>{n.status === 'online' ? '在线' : '离线'}</span>
        </div>)}
        {cluster.nodes.length === 0 && <p className="muted">暂无已注册节点</p>}
      </div>
    </section>}
    <section className="settings-section"><div className="settings-title"><div><h2>模型服务</h2><p>保存配置后会安全重载；启动模型可能需要几十秒</p></div></div>
      {models.map(model => <div className="model-config" key={model.id}>
        <div className="model-config-head"><div><span className={`status-dot ${model.loaded ? '' : 'idle'}`} /><h3>{model.id}</h3><span>{model.description}</span></div><label className="switch"><input type="checkbox" checked={model.enabled} onChange={e => updateModel(model.id, { enabled: e.target.checked })} /><span /></label></div>
        <div className="settings-form model-form"><div className="field span-two"><label>Python 路径</label><input value={model.python} onChange={e => updateModel(model.id, { python: e.target.value })} /></div><div className="field"><label>端口</label><input type="number" value={model.port} onChange={e => updateModel(model.id, { port: Number(e.target.value) })} /></div><div className="field"><label>设备</label><select value={String(model.options.device || 'auto')} onChange={e => updateOption(model.id, 'device', e.target.value)}><option value="auto">auto</option><option value="cuda">cuda</option><option value="mps">mps</option><option value="cpu">cpu</option></select></div>{model.options.model_dir !== undefined && <div className="field span-two"><label>模型目录</label><input value={String(model.options.model_dir)} onChange={e => updateOption(model.id, 'model_dir', e.target.value)} /></div>}{model.options.repo_dir !== undefined && <div className="field span-two"><label>代码目录</label><input value={String(model.options.repo_dir)} onChange={e => updateOption(model.id, 'repo_dir', e.target.value)} /></div>}</div>
        <div className="model-actions"><button onClick={() => action(model, 'start')} disabled={!model.enabled || Boolean(busy)}><Play />启动</button><button onClick={() => action(model, 'stop')} disabled={Boolean(busy)}><Square />停止</button><button onClick={() => action(model, 'restart')} disabled={!model.enabled || Boolean(busy)}><RefreshCw />重启</button><button className="save-model" onClick={() => saveModel(model)} disabled={Boolean(busy)}>{busy === `save-${model.id}` ? <LoaderCircle className="spin" /> : <Save />}保存配置</button></div>
      </div>)}
    </section>
  </div>
}
