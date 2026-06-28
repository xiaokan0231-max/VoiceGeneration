import { useEffect, useRef, useState } from 'react'
import { Pause, Play, Volume2 } from 'lucide-react'

function formatTime(seconds: number) {
  if (!Number.isFinite(seconds)) return '0:00'
  return `${Math.floor(seconds / 60)}:${Math.floor(seconds % 60).toString().padStart(2, '0')}`
}

export default function AudioPlayer({ src, compact = false }: { src: string; compact?: boolean }) {
  const audio = useRef<HTMLAudioElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const [playing, setPlaying] = useState(false)
  const [duration, setDuration] = useState(0)
  const [current, setCurrent] = useState(0)
  const [samples, setSamples] = useState<number[]>([])

  useEffect(() => {
    let cancelled = false
    fetch(src).then(r => r.arrayBuffer()).then(async data => {
      const context = new AudioContext()
      const decoded = await context.decodeAudioData(data)
      const channel = decoded.getChannelData(0)
      const bars = 96
      const size = Math.max(1, Math.floor(channel.length / bars))
      const next = Array.from({ length: bars }, (_, i) => {
        let peak = 0
        for (let j = i * size; j < Math.min(channel.length, (i + 1) * size); j++) peak = Math.max(peak, Math.abs(channel[j]))
        return peak
      })
      const max = Math.max(...next, 0.01)
      if (!cancelled) setSamples(next.map(v => Math.max(.08, v / max)))
      await context.close()
    }).catch(() => setSamples(Array.from({ length: 96 }, (_, i) => .15 + Math.abs(Math.sin(i * .8)) * .55)))
    return () => { cancelled = true }
  }, [src])

  useEffect(() => {
    const el = canvas.current
    if (!el) return
    const ratio = window.devicePixelRatio || 1
    const width = el.clientWidth || 600
    const height = el.clientHeight || 64
    el.width = width * ratio; el.height = height * ratio
    const ctx = el.getContext('2d')!; ctx.scale(ratio, ratio); ctx.clearRect(0, 0, width, height)
    const step = width / Math.max(samples.length, 1); const progress = duration ? current / duration : 0
    samples.forEach((value, i) => {
      const x = i * step; const h = Math.max(2, value * height * .78)
      ctx.fillStyle = i / samples.length <= progress ? '#d98d52' : '#465057'
      ctx.fillRect(x, (height - h) / 2, Math.max(1.5, step * .38), h)
    })
  }, [samples, current, duration])

  const toggle = () => {
    if (!audio.current) return
    if (playing) audio.current.pause(); else audio.current.play()
  }
  const seek = (event: React.MouseEvent<HTMLCanvasElement>) => {
    if (!audio.current || !duration) return
    const rect = event.currentTarget.getBoundingClientRect()
    audio.current.currentTime = ((event.clientX - rect.left) / rect.width) * duration
  }

  return <div className={`audio-player ${compact ? 'compact' : ''}`}>
    <audio ref={audio} src={src} preload="metadata" onLoadedMetadata={e => setDuration(e.currentTarget.duration)} onTimeUpdate={e => setCurrent(e.currentTarget.currentTime)} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} />
    <button className="play-button" onClick={toggle} aria-label={playing ? '暂停' : '播放'}>{playing ? <Pause /> : <Play />}</button>
    <div className="wave-wrap"><canvas ref={canvas} onClick={seek} /><div className="timecode">{formatTime(current)} <span>/ {formatTime(duration)}</span></div></div>
    {!compact && <Volume2 className="volume-icon" />}
  </div>
}
