import { useEffect, useMemo, useState } from 'react'
import { Copy, Cpu, Database, HardDrive, LoaderCircle, Network, Play, Power, RefreshCw, Save, Server } from 'lucide-react'
import { api } from '../api'
import { ConfirmDialog, useToast } from '../components/Feedback'
import type { ClusterInfo, ClusterNodeInfo, ClusterWorkerInfo, ConnectInfo, RuntimeModel, SettingsInfo, SystemInfo } from '../types'

const bytes = (value: number) => value < 1024 ** 2 ? `${(value / 1024).toFixed(0)} KB` : value < 1024 ** 3 ? `${(value / 1024 ** 2).toFixed(1)} MB` : `${(value / 1024 ** 3).toFixed(2)} GB`
const speed = (value: number | null) => value == null ? '—' : `${value.toFixed(2)}×`
const slotSummary = (workers: ClusterWorkerInfo[]) => {
  const counts = workers.reduce<Record<string, number>>((all, worker) => ({ ...all, [worker.model]: (all[worker.model] || 0) + 1 }), {})
  return Object.entries(counts).map(([model, count]) => `${model} ${count}槽`).join(' · ')
}

const workerStatus = (worker: ClusterWorkerInfo) => {
  if (worker.started == null) return worker.active ? '工作中' : '状态未上报'
  if (!worker.started) return '未启动'
  return worker.active ? '工作中' : '待机'
}

function WorkerRuntime({ worker }: { worker: ClusterWorkerInfo }) {
  const status = workerStatus(worker)
  const latestDetail = worker.audio_seconds != null && worker.inference_seconds != null
    ? `${worker.audio_seconds.toFixed(1)}s 音频 / ${worker.inference_seconds.toFixed(1)}s 推理`
    : worker.speed != null ? '最近完成样本' : '等待首个完成样本'

  return <div className="worker-runtime">
    <div className="worker-identity">
      <span className={`worker-state ${worker.active ? 'active' : worker.started ? 'ready' : ''}`} />
      <div><strong>{worker.id}</strong><small>{worker.model}{worker.port ? ` · :${worker.port}` : ''}</small></div>
    </div>
    <div className={`worker-status-copy ${worker.active ? 'active' : ''}`}><span>状态</span><strong>{status}</strong><small>{worker.active ? `已运行 ${Math.round(worker.elapsed_seconds || 0)} 秒` : worker.error || '可接收任务'}</small></div>
    <div className="worker-speed primary"><span>近 30 分钟平均</span><strong>{speed(worker.speed_30m)}</strong><small>{worker.samples_30m > 0 ? `${worker.samples_30m} 个样本` : '暂无历史样本'}</small></div>
    <div className="worker-speed"><span>最近一次</span><strong>{speed(worker.speed)}</strong><small>{latestDetail}</small></div>
  </div>
}

function ClusterNode({ node }: { node: ClusterNodeInfo }) {
  return <article className="cluster-node">
    <header className="cluster-node-head">
      <div className="cluster-node-name"><span className={`status-dot ${node.status === 'online' ? '' : 'idle'}`} /><div><h3>{node.name}</h3><p>{node.node_id} · {node.role} · 总槽位 {node.max_concurrency}{node.workers?.length ? ` · ${slotSummary(node.workers)}` : ''}</p></div></div>
      <span className={`node-online-state ${node.status === 'online' ? 'online' : 'offline'}`}>{node.status === 'online' ? '在线' : '离线'}</span>
    </header>
    <div className="cluster-node-stats">
      <div><span>已启动</span><strong>{node.started_workers ?? '—'}<small> / {node.max_concurrency}</small></strong><em>可用 Worker</em></div>
      <div className={node.working_workers > 0 ? 'working' : ''}><span>工作中</span><strong>{node.working_workers}</strong><em>正在生成</em></div>
      <div className="primary"><span>近 30 分钟平均</span><strong>{speed(node.average_speed_30m)}</strong><em>{node.samples_30m > 0 ? `${node.samples_30m} 个完成样本` : '暂无历史样本'}</em></div>
      <div><span>最近一次总速度</span><strong>{speed(node.latest_speed)}</strong><em>各 Worker 最近结果合计</em></div>
    </div>
    {node.workers?.length > 0
      ? <div className="worker-runtime-list">{node.workers.map(worker => <WorkerRuntime worker={worker} key={worker.id} />)}</div>
      : node.working_workers > 0 && <p className="metrics-hint">副节点正在工作；更新副节点程序后可查看每个 Worker 的速度。</p>}
  </article>
}

function ClusterSection({ cluster, connect, onCopy }: { cluster: ClusterInfo; connect: ConnectInfo | null; onCopy: (text: string) => void }) {
  const onlineNodes = cluster.nodes.filter(node => node.status === 'online').length
  const totalSlots = cluster.nodes.reduce((total, node) => total + node.max_concurrency, 0)
  const workingWorkers = cluster.nodes.reduce((total, node) => total + node.working_workers, 0)

  return <section className="settings-section cluster-section">
    <div className="cluster-heading">
      <div className="cluster-heading-copy"><div className="cluster-title-line"><span className="cluster-title-icon"><Network /></span><div><h2>集群运行状态</h2><p>本机：{cluster.self.node_name}（{cluster.self.role}{cluster.self.coordinator_runs_jobs ? ' · 参与生成' : ' · 仅协调'}）</p></div></div><small>近 30 分钟平均来自 MySQL 完成记录；最近一次为每个 Worker 最近完成速度的合计。速度 = 音频时长 ÷ 推理耗时。</small></div>
      <div className="cluster-overview">
        <div><span>待处理</span><strong>{cluster.queue_depth}<small> 条</small></strong></div>
        <div><span>在线节点</span><strong>{onlineNodes}<small> / {cluster.nodes.length}</small></strong></div>
        <div><span>工作槽位</span><strong>{workingWorkers}<small> / {totalSlots}</small></strong></div>
      </div>
    </div>
    <div className="node-list">
      {cluster.nodes.map(node => <ClusterNode node={node} key={node.node_id} />)}
      {cluster.nodes.length === 0 && <p className="muted">暂无已注册节点</p>}
    </div>
    {connect && <div className="connect-info">
      <h3>副节点接入信息</h3>
      <p className="muted">把下面的地址和令牌填进副节点控制台（http://&lt;副节点&gt;:8090），即可加入集群。</p>
      {!connect.reachable && <div className="inline-error">⚠ 当前网关只监听 {connect.host}，其它机器连不上。请把 settings.host 改为 0.0.0.0 后重启网关。</div>}
      {!connect.token && <p className="muted">⚠ 多机务必设置 cluster.token（两端一致），否则任何人都能接入。</p>}
      <div className="connect-row"><label>协调端地址</label><div className="url-list">
        {connect.candidate_urls.map(url => <div key={url} className="url-item"><code>{url}</code><button className="quiet-button" onClick={() => onCopy(url)}><Copy />复制</button></div>)}
        {connect.candidate_urls.length === 0 && <span className="muted">未探测到局域网地址</span>}
      </div></div>
      <div className="connect-row"><label>令牌 token</label><code className="token-box">{connect.token || '（未设置）'}</code>{connect.token && <button className="quiet-button" onClick={() => onCopy(connect.token)}><Copy />复制</button>}</div>
    </div>}
  </section>
}

export default function SettingsPage({ system, onSystemChange, onDirtyChange }: { system: SystemInfo | null; onSystemChange: () => void; onDirtyChange?: (dirty: boolean) => void }) {
  const [settings, setSettings] = useState<SettingsInfo | null>(null)
  const [models, setModels] = useState<RuntimeModel[]>([])
  const [baselineSettings, setBaselineSettings] = useState<SettingsInfo | null>(null)
  const [baselineModels, setBaselineModels] = useState<RuntimeModel[]>([])
  const [cluster, setCluster] = useState<ClusterInfo | null>(null)
  const [connect, setConnect] = useState<ConnectInfo | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [shutdownOpen, setShutdownOpen] = useState(false)
  const { notify } = useToast()

  const load = () => api.settings().then(value => { setSettings(value); setModels(value.models); setBaselineSettings(value); setBaselineModels(value.models) }).catch(e => setError(e.message))
  const loadCluster = async () => {
    const [clusterResult, connectResult] = await Promise.allSettled([api.cluster(), api.connectInfo()])
    setCluster(clusterResult.status === 'fulfilled' ? clusterResult.value : null)
    setConnect(connectResult.status === 'fulfilled' ? connectResult.value : null)
  }
  const copy = (text: string) => { navigator.clipboard?.writeText(text).then(() => notify('已复制到剪贴板', 'success')).catch(() => notify('复制失败', 'error')) }
  useEffect(() => { void load(); void loadCluster(); const id = window.setInterval(() => { if (!document.hidden) void loadCluster() }, 5000); return () => window.clearInterval(id) }, [])
  const updateSetting = <K extends keyof SettingsInfo>(key: K, value: SettingsInfo[K]) => setSettings(s => s ? ({ ...s, [key]: value }) : s)
  const updateModel = (id: string, update: Partial<RuntimeModel>) => setModels(list => list.map(m => m.id === id ? { ...m, ...update } : m))
  const updateOption = (id: string, key: string, value: unknown) => setModels(list => list.map(m => m.id === id ? { ...m, options: { ...m.options, [key]: value } } : m))
  const generalSnapshot = (value: SettingsInfo | null) => value ? JSON.stringify({ default_model: value.default_model, default_format: value.default_format, worker_idle_timeout: value.worker_idle_timeout, worker_start_timeout: value.worker_start_timeout, cache_max_gb: value.cache_max_gb }) : ''
  const modelSnapshot = (value: RuntimeModel) => JSON.stringify({ enabled: value.enabled, python: value.python, port: Number(value.port), replicas: Number(value.replicas), options: value.options })
  const generalDirty = generalSnapshot(settings) !== generalSnapshot(baselineSettings)
  const dirtyModels = useMemo(() => new Set(models.filter(model => {
    const baseline = baselineModels.find(value => value.id === model.id)
    return !baseline || modelSnapshot(model) !== modelSnapshot(baseline)
  }).map(model => model.id)), [models, baselineModels])
  const dirty = generalDirty || dirtyModels.size > 0
  useEffect(() => { onDirtyChange?.(dirty) }, [dirty, onDirtyChange])
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange])
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => { if (dirty) event.preventDefault() }
    window.addEventListener('beforeunload', beforeUnload)
    return () => window.removeEventListener('beforeunload', beforeUnload)
  }, [dirty])
  const saveGeneral = async () => {
    if (!settings) return; setBusy('general'); setError('')
    try {
      const next = await api.saveSettings({ default_model: settings.default_model, default_format: settings.default_format, worker_idle_timeout: Number(settings.worker_idle_timeout), worker_start_timeout: Number(settings.worker_start_timeout), cache_max_gb: Number(settings.cache_max_gb) })
      setSettings(current => current ? { ...current, default_model: next.default_model, default_format: next.default_format, worker_idle_timeout: next.worker_idle_timeout, worker_start_timeout: next.worker_start_timeout, cache_max_gb: next.cache_max_gb } : next)
      setBaselineSettings(next); notify('全局设置已保存', 'success')
    }
    catch (e) { setError(e instanceof Error ? e.message : '保存失败') } finally { setBusy('') }
  }
  const saveModel = async (model: RuntimeModel) => {
    setBusy(`save-${model.id}`); setError('')
    try {
      await api.saveModel(model.id, { enabled: model.enabled, python: model.python, port: Number(model.port), replicas: Number(model.replicas), options: model.options })
      setBaselineModels(current => current.map(value => value.id === model.id ? model : value)); notify(`${model.id} 配置已保存`, 'success')
      await Promise.all([loadCluster(), onSystemChange()])
    }
    catch (e) { setError(e instanceof Error ? e.message : '保存失败') } finally { setBusy('') }
  }
  const toggleModel = async (model: RuntimeModel, enabled: boolean) => {
    setBusy(`toggle-${model.id}`); setError('')
    updateModel(model.id, { enabled })
    try {
      await api.saveModel(model.id, { enabled })
      setBaselineModels(current => current.map(value => value.id === model.id ? { ...value, enabled } : value)); notify(`${model.id} 已${enabled ? '启用并参与调度' : '停用并退出调度'}`, 'success')
      await Promise.all([loadCluster(), onSystemChange()])
    }
    catch (e) { updateModel(model.id, { enabled: model.enabled }); setError(e instanceof Error ? e.message : '切换失败') } finally { setBusy('') }
  }
  const action = async (model: RuntimeModel, name: 'start' | 'restart') => {
    setBusy(`${name}-${model.id}`); setError('')
    try { await api.modelAction(model.id, name); notify(`${model.id} 已${name === 'start' ? '预热启动' : '重启'}`, 'success'); await Promise.all([loadCluster(), onSystemChange()]) }
    catch (e) { setError(e instanceof Error ? e.message : '操作失败') } finally { setBusy('') }
  }
  const shutdown = async () => {
    setBusy('shutdown')
    try { await api.shutdown(); notify('服务正在停止…', 'info'); setShutdownOpen(false) }
    catch (e) { setError(e instanceof Error ? e.message : '停止服务失败') }
    finally { setBusy('') }
  }

  return <div className="content-page settings-page">
    <div className="page-heading large"><div><span className="eyebrow">LOCAL SERVICE</span><h1>服务设置</h1><p>检查本机运行状态，并管理模型与高级配置</p></div><button className="danger-button" onClick={() => setShutdownOpen(true)}><Power />停止服务</button></div>
    <div className="health-grid">
      <div className="health-item"><Database /><span>MySQL</span><strong className={system?.database === 'online' ? 'online' : 'offline'}>{system?.database === 'online' ? '在线' : '离线'}</strong><small>voice_generation</small></div>
      <div className="health-item"><HardDrive /><span>音频缓存</span><strong>{system ? bytes(system.cache_bytes) : '—'}</strong><small>上限 {system ? bytes(system.cache_limit_bytes) : '—'}</small></div>
      <div className="health-item"><Cpu /><span>Apple MPS</span><strong className={system?.mps ? 'online' : ''}>{system?.mps ? '可用' : '不可用'}</strong><small>{system?.apple_silicon ? 'Apple Silicon' : '当前设备'}</small></div>
      <div className="health-item"><Server /><span>网关服务</span><strong className={system ? 'online' : 'offline'}>{system ? '在线' : '离线'}</strong><small>127.0.0.1:8080</small></div>
    </div>
    {error && <div className="inline-error page-error" role="alert">{error}</div>}
    {settings && <section className="settings-section"><div className="settings-title"><div><h2>全局设置 {generalDirty && <span className="dirty-badge">未保存</span>}</h2><p>工作台默认值与本机资源回收策略</p></div><button className="primary-small" onClick={saveGeneral} disabled={busy === 'general' || !generalDirty}>{busy === 'general' ? <LoaderCircle className="spin" /> : <Save />}保存</button></div><div className="settings-form general-form">
      <div className="field"><label>默认模型</label><select value={settings.default_model} onChange={e => updateSetting('default_model', e.target.value)}>{models.filter(m => m.enabled).map(m => <option key={m.id}>{m.id}</option>)}</select></div>
      <div className="field"><label>默认格式</label><select value={settings.default_format} onChange={e => updateSetting('default_format', e.target.value)}><option>wav</option><option>mp3</option><option>opus</option></select></div>
      <div className="field"><label>空闲回收（秒）</label><input type="number" min="30" value={settings.worker_idle_timeout} onChange={e => updateSetting('worker_idle_timeout', Number(e.target.value))} /></div>
      <div className="field"><label>启动超时（秒）</label><input type="number" min="30" value={settings.worker_start_timeout} onChange={e => updateSetting('worker_start_timeout', Number(e.target.value))} /></div>
      <div className="field"><label>缓存上限（GB）</label><input type="number" min=".1" step=".1" value={settings.cache_max_gb} onChange={e => updateSetting('cache_max_gb', Number(e.target.value))} /></div>
    </div></section>}
    {cluster && <ClusterSection cluster={cluster} connect={connect} onCopy={copy} />}
    <section className="settings-section"><div className="settings-title"><div><h2>模型服务</h2><p>开关即时控制模型是否参与调度；“启动”仅用于提前预热模型</p></div></div>
      {models.map(model => <div className="model-config" key={model.id}>
        <div className="model-config-head"><div><span className={`status-dot ${model.loaded ? '' : 'idle'}`} /><h3>{model.id}</h3>{dirtyModels.has(model.id) && <span className="dirty-badge">未保存</span>}<span>{model.description}</span></div><div className="model-enable"><span>{model.enabled ? '参与调度' : '已停用'}</span><label className="switch"><input type="checkbox" checked={model.enabled} disabled={busy.includes(model.id)} aria-label={`${model.enabled ? '停用' : '启用'} ${model.id}`} onChange={e => void toggleModel(model, e.target.checked)} /><span /></label></div></div>
        <div className="settings-form model-form"><div className="field span-two"><label>Python 路径</label><input value={model.python} onChange={e => updateModel(model.id, { python: e.target.value })} /></div><div className="field"><label>起始端口</label><input type="number" value={model.port} onChange={e => updateModel(model.id, { port: Number(e.target.value) })} /></div><div className="field"><label>Worker 副本</label><input type="number" min="1" max="8" value={model.replicas} onChange={e => updateModel(model.id, { replicas: Number(e.target.value) })} /></div><div className="field"><label>设备</label><select value={String(model.options.device || 'auto')} onChange={e => updateOption(model.id, 'device', e.target.value)}><option value="auto">auto</option><option value="cuda">cuda</option><option value="mps">mps</option><option value="cpu">cpu</option></select></div>{model.options.model_dir !== undefined && <div className="field span-two"><label>模型目录</label><input value={String(model.options.model_dir)} onChange={e => updateOption(model.id, 'model_dir', e.target.value)} /></div>}{model.options.repo_dir !== undefined && <div className="field span-two"><label>代码目录</label><input value={String(model.options.repo_dir)} onChange={e => updateOption(model.id, 'repo_dir', e.target.value)} /></div>}</div>
        <div className="model-actions"><button onClick={() => action(model, 'start')} disabled={!model.enabled || busy.includes(model.id)}><Play />预热启动</button><button onClick={() => action(model, 'restart')} disabled={!model.enabled || busy.includes(model.id)}><RefreshCw />重启</button><button className="save-model" onClick={() => saveModel(model)} disabled={busy.includes(model.id) || !dirtyModels.has(model.id)}>{busy === `save-${model.id}` ? <LoaderCircle className="spin" /> : <Save />}保存配置</button></div>
      </div>)}
    </section>
    <ConfirmDialog open={shutdownOpen} title="停止 VoiceGeneration 服务？" description="网关和全部模型进程都会停止。之后可以再次双击 VoiceGeneration.app 启动。" confirmLabel="停止服务" busy={busy === 'shutdown'} onCancel={() => setShutdownOpen(false)} onConfirm={shutdown} />
  </div>
}
