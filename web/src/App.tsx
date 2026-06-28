import { useEffect, useState } from 'react'
import { AudioLines, Clock3, FolderKanban, Library, Menu, Settings, SlidersHorizontal, X } from 'lucide-react'
import { api } from './api'
import HistoryPage from './pages/HistoryPage'
import ProjectsPage from './pages/ProjectsPage'
import SettingsPage from './pages/SettingsPage'
import VoiceLibrary from './pages/VoiceLibrary'
import Workbench from './pages/Workbench'
import type { GenerationDraft, PageKey, ProjectDetail, SystemInfo } from './types'

const nav = [
  { id: 'workbench' as const, label: '生成工作台', icon: AudioLines },
  { id: 'voices' as const, label: '音色库', icon: Library },
  { id: 'projects' as const, label: '项目', icon: FolderKanban },
  { id: 'history' as const, label: '生成历史', icon: Clock3 },
  { id: 'settings' as const, label: '服务设置', icon: Settings },
]

export default function App() {
  const [page, setPage] = useState<PageKey>('workbench')
  const [system, setSystem] = useState<SystemInfo | null>(null)
  const [mobileNav, setMobileNav] = useState(false)
  const [draft, setDraft] = useState<GenerationDraft | undefined>()
  const [historyVersion, setHistoryVersion] = useState(0)
  const [projects, setProjects] = useState<ProjectDetail[]>([])
  const [historyProject, setHistoryProject] = useState('')

  const refreshSystem = () => api.system().then(setSystem).catch(() => setSystem(null))
  const loadProjects = () => api.projects().then(setProjects).catch(() => setProjects([]))
  useEffect(() => {
    refreshSystem()
    void loadProjects()
    const id = window.setInterval(refreshSystem, 15000)
    return () => window.clearInterval(id)
  }, [])

  const choose = (next: PageKey) => { setPage(next); setMobileNav(false) }
  const reuse = (next: GenerationDraft) => { setDraft(next); choose('workbench') }
  const viewProjectGenerations = (projectId: string) => { setHistoryProject(projectId); choose('history') }

  return <div className="app-shell">
    <header className="topbar">
      <button className="icon-button mobile-only" onClick={() => setMobileNav(true)} aria-label="打开导航"><Menu /></button>
      <div className="brand"><span className="brand-mark"><SlidersHorizontal /></span><strong>VoiceGeneration</strong><span>本机语音工作台</span></div>
      <div className="top-status">
        <span className={`status-dot ${system ? '' : 'offline'}`} />
        <span>{system ? '本地服务在线' : '服务未连接'}</span>
        <span className="device-badge">Apple MPS · {system?.mps ? '可用' : '检测中'}</span>
      </div>
    </header>
    <div className="workspace">
      <aside className={`sidebar ${mobileNav ? 'open' : ''}`}>
        <button className="icon-button close-nav" onClick={() => setMobileNav(false)} aria-label="关闭导航"><X /></button>
        <nav>{nav.map(item => <button key={item.id} className={page === item.id ? 'active' : ''} onClick={() => choose(item.id)}><item.icon /><span>{item.label}</span></button>)}</nav>
        <div className="sidebar-foot"><span>LOCAL RUNTIME</span><small>仅绑定 127.0.0.1</small></div>
      </aside>
      {mobileNav && <button className="nav-scrim" aria-label="关闭导航" onClick={() => setMobileNav(false)} />}
      <main className="main-view">
        {page === 'workbench' && <Workbench initialDraft={draft} historyVersion={historyVersion} projects={projects} onProjectsChange={loadProjects} onGenerated={() => setHistoryVersion(v => v + 1)} />}
        {page === 'voices' && <VoiceLibrary />}
        {page === 'projects' && <ProjectsPage projects={projects} onChange={loadProjects} onViewGenerations={viewProjectGenerations} />}
        {page === 'history' && <HistoryPage version={historyVersion} projects={projects} initialProject={historyProject} onReuse={reuse} />}
        {page === 'settings' && <SettingsPage system={system} onSystemChange={refreshSystem} />}
      </main>
    </div>
  </div>
}
