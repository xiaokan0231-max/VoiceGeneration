import { useMemo, useState } from 'react'
import { CheckCircle2, Clock3, Download, LoaderCircle, RefreshCw, StopCircle, X } from 'lucide-react'
import { useGeneration } from '../context/GenerationContext'
import { useToast } from './Feedback'
import type { GenerationTask } from '../types'

const labels: Record<GenerationTask['status'], string> = {
  queued: '排队中', leased: '生成中', running: '生成中', completed: '已完成', failed: '失败', cancelled: '已取消',
}

function elapsed(task: GenerationTask) {
  if (task.elapsed_seconds != null) return `${task.elapsed_seconds.toFixed(1)} 秒`
  const end = task.completed_at ? Date.parse(task.completed_at) : Date.now()
  return `${Math.max(0, Math.round((end - Date.parse(task.created_at)) / 1000))} 秒`
}

export default function TaskCenter() {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState('')
  const { tasks, activeCount, liveJobs, liveCount, cancelGeneration, retryGeneration } = useGeneration()
  const { notify } = useToast()
  // 合并：本会话提交的任务(含已完成，用于下载/重试) + 全集群在跑任务(任何来源)，按 id 去重
  const merged = useMemo(() => {
    const byId = new Map<string, GenerationTask>()
    for (const task of tasks) byId.set(task.id, task)
    for (const job of liveJobs) byId.set(job.id, job)   // 服务端在跑态覆盖客户端旧态
    return [...byId.values()].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
  }, [tasks, liveJobs])
  const badge = Math.max(activeCount, liveCount)         // 右上角徽标=真实在跑数
  const queued = merged.filter(task => task.status === 'queued').length
  const working = merged.filter(task => task.status === 'leased' || task.status === 'running').length

  const cancel = async (id: string) => {
    setBusy(id)
    try { await cancelGeneration(id) }
    catch (error) { notify(error instanceof Error ? error.message : '取消失败', 'error') }
    finally { setBusy('') }
  }
  const retry = async (task: GenerationTask) => {
    setBusy(task.id)
    try { await retryGeneration(task) }
    catch (error) { notify(error instanceof Error ? error.message : '重新生成失败', 'error') }
    finally { setBusy('') }
  }

  return <div className="task-center">
    <button className={`task-trigger ${badge ? 'active' : ''}`} aria-expanded={open} aria-controls="task-drawer" onClick={() => setOpen(value => !value)}>
      {badge ? <LoaderCircle className="spin" /> : <Clock3 />}
      <span>任务</span><strong>{badge}</strong>
    </button>
    {open && <>
      <button className="task-scrim" aria-label="关闭任务中心" onClick={() => setOpen(false)} />
      <aside className="task-drawer" id="task-drawer" aria-label="生成任务中心">
        <div className="task-drawer-head"><div><span>GENERATION QUEUE</span><h2>生成任务</h2></div><button className="icon-button" aria-label="关闭任务中心" onClick={() => setOpen(false)}><X /></button></div>
        <div className="task-summary"><span>排队 <strong>{queued}</strong></span><span>生成中 <strong>{working}</strong></span></div>
        <div className="task-list">
          {merged.length === 0 && <div className="task-empty"><CheckCircle2 /><p>当前没有生成任务</p><small>从工作台提交后，可在任意页面查看进度。</small></div>}
          {merged.map(task => <article className={`task-item ${task.status}`} key={task.id}>
            <div className="task-item-top"><span className={`status-dot ${task.status}`} /><strong>{labels[task.status]}</strong><time>{elapsed(task)}</time></div>
            <p>{task.text}</p>
            <small>{task.model} · {task.voice_name}{task.node_name ? ` · ${task.node_name}` : ''}</small>
            {task.error_message && <div className="task-error">{task.error_message}</div>}
            <div className="task-actions">
              {task.status === 'queued' && <button disabled={busy === task.id} onClick={() => cancel(task.id)}><StopCircle />取消排队</button>}
              {task.status === 'completed' && task.audio_url && <a href={task.audio_url} download><Download />下载</a>}
              {(task.status === 'failed' || task.status === 'cancelled') && <button disabled={busy === task.id} onClick={() => retry(task)}><RefreshCw />重新生成</button>}
            </div>
          </article>)}
        </div>
      </aside>
    </>}
  </div>
}
