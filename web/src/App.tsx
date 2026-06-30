import { useCallback, useEffect, useState } from 'react'
import { AudioLines, Clock3, FolderKanban, Library, Menu, Settings, SlidersHorizontal, X } from 'lucide-react'
import { Navigate, NavLink, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { api } from './api'
import TaskCenter from './components/TaskCenter'
import { ConfirmDialog } from './components/Feedback'
import { useGeneration } from './context/GenerationContext'
import HistoryPage from './pages/HistoryPage'
import ProjectsPage from './pages/ProjectsPage'
import SettingsPage from './pages/SettingsPage'
import VoiceLibrary from './pages/VoiceLibrary'
import Workbench from './pages/Workbench'
import type { GenerationDraft, ProjectDetail, SystemInfo } from './types'

const nav = [
  { path: '/', label: '生成工作台', icon: AudioLines, end: true },
  { path: '/voices', label: '音色库', icon: Library },
  { path: '/projects', label: '项目', icon: FolderKanban },
  { path: '/history', label: '生成历史', icon: Clock3 },
  { path: '/settings', label: '服务设置', icon: Settings },
]

export default function App() {
  const [system, setSystem] = useState<SystemInfo | null>(null)
  const [mobileNav, setMobileNav] = useState(false)
  const [projects, setProjects] = useState<ProjectDetail[]>([])
  const [settingsDirty, setSettingsDirty] = useState(false)
  const [pendingPath, setPendingPath] = useState('')
  const { replaceDraft, historyVersion } = useGeneration()
  const navigate = useNavigate()
  const location = useLocation()

  const refreshSystem = useCallback(() => api.system().then(setSystem).catch(() => setSystem(null)), [])
  const loadProjects = useCallback(() => api.projects().then(setProjects).catch(() => setProjects([])), [])
  useEffect(() => {
    void refreshSystem(); void loadProjects()
    const id = window.setInterval(() => { if (!document.hidden) void refreshSystem() }, 15000)
    return () => window.clearInterval(id)
  }, [refreshSystem, loadProjects])
  useEffect(() => { setMobileNav(false) }, [location.pathname])

  const reuse = (next: GenerationDraft) => { replaceDraft(next); navigate('/') }
  const viewProjectGenerations = (projectId: string) => navigate(`/history?project=${encodeURIComponent(projectId)}`)

  return <div className="app-shell">
    <header className="topbar">
      <button className="icon-button mobile-only" onClick={() => setMobileNav(true)} aria-label="打开导航"><Menu /></button>
      <NavLink className="brand" to="/" onClick={event => { if (settingsDirty && location.pathname === '/settings') { event.preventDefault(); setPendingPath('/') } }}><span className="brand-mark"><SlidersHorizontal /></span><strong>VoiceGeneration</strong><span>本机语音工作台</span></NavLink>
      <div className="top-status">
        <span className={`status-dot ${system ? '' : 'offline'}`} />
        <span>{system ? '本地服务在线' : '服务未连接'}</span>
        <span className="device-badge">Apple MPS · {system?.mps ? '可用' : '检测中'}</span>
        <TaskCenter />
      </div>
    </header>
    <div className="workspace">
      <aside className={`sidebar ${mobileNav ? 'open' : ''}`}>
        <button className="icon-button close-nav" onClick={() => setMobileNav(false)} aria-label="关闭导航"><X /></button>
        <nav>{nav.map(item => <NavLink key={item.path} to={item.path} end={item.end} className={({ isActive }) => isActive ? 'active' : ''} onClick={event => { if (settingsDirty && location.pathname === '/settings' && item.path !== '/settings') { event.preventDefault(); setPendingPath(item.path) } }}><item.icon /><span>{item.label}</span></NavLink>)}</nav>
        <div className="sidebar-foot"><span>LOCAL RUNTIME</span><small>仅绑定 127.0.0.1</small></div>
      </aside>
      {mobileNav && <button className="nav-scrim" aria-label="关闭导航" onClick={() => setMobileNav(false)} />}
      <main className="main-view">
        <Routes>
          <Route path="/" element={<Workbench projects={projects} onProjectsChange={loadProjects} />} />
          <Route path="/voices" element={<VoiceLibrary />} />
          <Route path="/projects" element={<ProjectsPage projects={projects} onChange={loadProjects} onViewGenerations={viewProjectGenerations} />} />
          <Route path="/history" element={<HistoryPage version={historyVersion} projects={projects} onReuse={reuse} />} />
          <Route path="/settings" element={<SettingsPage system={system} onSystemChange={refreshSystem} onDirtyChange={setSettingsDirty} />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
    <ConfirmDialog open={Boolean(pendingPath)} title="放弃未保存的设置？" description="离开后，本页尚未保存的全局设置和模型配置会丢失。" confirmLabel="放弃并离开" onCancel={() => setPendingPath('')} onConfirm={() => { const path = pendingPath; setSettingsDirty(false); setPendingPath(''); navigate(path) }} />
  </div>
}
