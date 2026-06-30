import { createContext, useContext, useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { Pause, Play, RotateCcw, Volume2, VolumeX } from 'lucide-react'

const waveformCache = new Map<string, number[]>()
const PlaybackContext = createContext<{ activeId: string; setActiveId: (id: string) => void } | null>(null)

export function AudioPlaybackProvider({ children }: { children: ReactNode }) {
  const [activeId, setActiveId] = useState('')
  return <PlaybackContext.Provider value={{ activeId, setActiveId }}>{children}</PlaybackContext.Provider>
}

function formatTime(seconds: number) {
  if (!Number.isFinite(seconds)) return '0:00'
  return `${Math.floor(seconds / 60)}:${Math.floor(seconds % 60).toString().padStart(2, '0')}`
}

export default function AudioPlayer({ src, compact = false }: { src: string; compact?: boolean }) {
  const id = useId()
  const playback = useContext(PlaybackContext)
  const root = useRef<HTMLDivElement>(null)
  const audio = useRef<HTMLAudioElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const [visible, setVisible] = useState(false)
  const [playing, setPlaying] = useState(false)
  const [duration, setDuration] = useState(0)
  const [current, setCurrent] = useState(0)
  const [volume, setVolume] = useState(1)
  const [muted, setMuted] = useState(false)
  const [samples, setSamples] = useState<number[]>(() => waveformCache.get(src) || [])
  const [loadError, setLoadError] = useState('')

  useEffect(() => {
    const element = root.current
    if (!element || typeof IntersectionObserver === 'undefined') { setVisible(true); return }
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { setVisible(true); observer.disconnect() }
    }, { rootMargin: '160px' })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (!visible || samples.length || loadError) return
    const cached = waveformCache.get(src)
    if (cached) { setSamples(cached); return }
    const controller = new AbortController()
    let context: AudioContext | null = null
    fetch(src, { signal: controller.signal }).then(response => {
      if (!response.ok) throw new Error('音频不可用')
      return response.arrayBuffer()
    }).then(async data => {
      context = new AudioContext()
      const decoded = await context.decodeAudioData(data)
      const channel = decoded.getChannelData(0)
      const bars = 96; const size = Math.max(1, Math.floor(channel.length / bars))
      const next = Array.from({ length: bars }, (_, i) => {
        let peak = 0
        for (let j = i * size; j < Math.min(channel.length, (i + 1) * size); j++) peak = Math.max(peak, Math.abs(channel[j]))
        return peak
      })
      const max = Math.max(...next, 0.01)
      const normalized = next.map(value => Math.max(.08, value / max))
      waveformCache.set(src, normalized); setSamples(normalized)
    }).catch(error => {
      if (error instanceof Error && error.name !== 'AbortError') setLoadError('音频加载失败')
    }).finally(() => { void context?.close() })
    return () => { controller.abort(); void context?.close() }
  }, [visible, src, samples.length, loadError])

  useEffect(() => {
    if (playback?.activeId && playback.activeId !== id && playing) audio.current?.pause()
  }, [playback?.activeId, id, playing])
  useEffect(() => { if (audio.current) audio.current.volume = volume }, [volume])

  useEffect(() => {
    const element = canvas.current
    if (!element || !samples.length) return
    const ratio = window.devicePixelRatio || 1
    const width = element.clientWidth || 600; const height = element.clientHeight || 64
    element.width = width * ratio; element.height = height * ratio
    const ctx = element.getContext('2d')!; ctx.scale(ratio, ratio); ctx.clearRect(0, 0, width, height)
    const step = width / samples.length; const progress = duration ? current / duration : 0
    samples.forEach((value, index) => {
      const heightValue = Math.max(2, value * height * .78)
      ctx.fillStyle = index / samples.length <= progress ? '#d98d52' : '#465057'
      ctx.fillRect(index * step, (height - heightValue) / 2, Math.max(1.5, step * .38), heightValue)
    })
  }, [samples, current, duration])

  const toggle = async () => {
    if (!audio.current) return
    if (playing) audio.current.pause()
    else {
      try { playback?.setActiveId(id); await audio.current.play(); setLoadError('') }
      catch { setLoadError('无法播放音频') }
    }
  }
  const setPosition = (ratio: number) => {
    if (!audio.current || !duration) return
    audio.current.currentTime = Math.max(0, Math.min(1, ratio)) * duration
  }
  const seek = (event: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = event.currentTarget.getBoundingClientRect()
    setPosition((event.clientX - rect.left) / rect.width)
  }
  const seekKey = (event: React.KeyboardEvent<HTMLCanvasElement>) => {
    if (!audio.current || !duration || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    if (event.key === 'Home') setPosition(0)
    else if (event.key === 'End') setPosition(1)
    else audio.current.currentTime = Math.max(0, Math.min(duration, audio.current.currentTime + (event.key === 'ArrowRight' ? 5 : -5)))
  }

  return <div ref={root} className={`audio-player ${compact ? 'compact' : ''}`}>
    <audio ref={audio} src={src} preload="metadata" muted={muted} onLoadedMetadata={event => setDuration(event.currentTarget.duration)} onTimeUpdate={event => setCurrent(event.currentTarget.currentTime)} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} onError={() => setLoadError('音频加载失败')} />
    <button className="play-button" onClick={toggle} aria-label={playing ? '暂停' : '播放'}>{playing ? <Pause /> : <Play />}</button>
    <div className="wave-wrap">
      {loadError ? <button className="audio-error" onClick={() => { setLoadError(''); setSamples([]) }}><RotateCcw />{loadError}，重试</button> : samples.length ? <canvas ref={canvas} tabIndex={0} role="slider" aria-label="音频播放进度" aria-valuemin={0} aria-valuemax={Math.round(duration)} aria-valuenow={Math.round(current)} onClick={seek} onKeyDown={seekKey} /> : <div className="wave-loading" aria-label="正在加载音频波形" />}
      <div className="timecode">{formatTime(current)} <span>/ {formatTime(duration)}</span></div>
    </div>
    {!compact && <div className="volume-control"><button aria-label={muted ? '取消静音' : '静音'} onClick={() => setMuted(value => !value)}>{muted ? <VolumeX /> : <Volume2 />}</button><input aria-label="音量" type="range" min="0" max="1" step="0.05" value={volume} onChange={event => { setVolume(Number(event.target.value)); setMuted(false) }} /></div>}
  </div>
}
