import { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react'
import { AlertTriangle, CheckCircle2, Info, X } from 'lucide-react'

type ToastTone = 'success' | 'error' | 'info'
interface ToastItem { id: number; message: string; tone: ToastTone }
interface ToastApi { notify: (message: string, tone?: ToastTone) => void }

const ToastContext = createContext<ToastApi | null>(null)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const nextId = useRef(0)
  const notify = useCallback((message: string, tone: ToastTone = 'info') => {
    const id = ++nextId.current
    setItems(current => [...current, { id, message, tone }])
    window.setTimeout(() => setItems(current => current.filter(item => item.id !== id)), 4500)
  }, [])
  const value = useMemo(() => ({ notify }), [notify])
  return <ToastContext.Provider value={value}>
    {children}
    <div className="toast-region" aria-live="polite" aria-atomic="false">
      {items.map(item => <div className={`toast ${item.tone}`} role={item.tone === 'error' ? 'alert' : 'status'} key={item.id}>
        {item.tone === 'success' ? <CheckCircle2 /> : item.tone === 'error' ? <AlertTriangle /> : <Info />}
        <span>{item.message}</span>
        <button aria-label="关闭通知" onClick={() => setItems(current => current.filter(value => value.id !== item.id))}><X /></button>
      </div>)}
    </div>
  </ToastContext.Provider>
}

export function useToast() {
  const value = useContext(ToastContext)
  if (!value) throw new Error('useToast must be used inside ToastProvider')
  return value
}

export function Dialog({ open, title, eyebrow, onClose, children, footer, className = '' }: {
  open: boolean
  title: string
  eyebrow?: string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
  className?: string
}) {
  const titleId = useId()
  const panel = useRef<HTMLElement>(null)
  const previousFocus = useRef<HTMLElement | null>(null)
  const onCloseRef = useRef(onClose)
  useEffect(() => { onCloseRef.current = onClose }, [onClose])

  useEffect(() => {
    if (!open) return
    previousFocus.current = document.activeElement as HTMLElement | null
    const oldOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const focusable = () => Array.from(panel.current?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
    ) || [])
    window.setTimeout(() => (panel.current?.querySelector<HTMLElement>('[data-autofocus]') || focusable()[0])?.focus())
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); onCloseRef.current(); return }
      if (event.key !== 'Tab') return
      const nodes = focusable()
      if (!nodes.length) return
      const first = nodes[0]; const last = nodes[nodes.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = oldOverflow
      previousFocus.current?.focus()
    }
  }, [open])

  if (!open) return null
  return <div className="dialog-backdrop">
    <section ref={panel} className={`dialog-panel ${className}`} role="dialog" aria-modal="true" aria-labelledby={titleId}>
      <div className="dialog-head"><div>{eyebrow && <span className="eyebrow">{eyebrow}</span>}<h2 id={titleId}>{title}</h2></div><button className="icon-button" aria-label="关闭弹窗" onClick={onClose}><X /></button></div>
      {children}
      {footer && <div className="dialog-actions">{footer}</div>}
    </section>
  </div>
}

export function ConfirmDialog({ open, title, description, confirmLabel = '确认', danger = true, busy = false, onCancel, onConfirm }: {
  open: boolean
  title: string
  description: string
  confirmLabel?: string
  danger?: boolean
  busy?: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  return <Dialog open={open} title={title} eyebrow="CONFIRM" onClose={busy ? () => undefined : onCancel} footer={<>
    <button className="quiet-button" disabled={busy} onClick={onCancel}>取消</button>
    <button className={danger ? 'danger-button' : 'primary-small'} disabled={busy} onClick={onConfirm}>{busy ? '处理中…' : confirmLabel}</button>
  </>}>
    <p className="confirm-copy">{description}</p>
  </Dialog>
}
