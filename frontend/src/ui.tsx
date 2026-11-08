import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Camera, Check, CircleAlert, X } from 'lucide-react'
import { label, safeCameraUrl } from './farm'
import type { Printer, PrinterKind } from './farm'

export function Dialog({ title, description, children, close }: { title: string; description?: string; children: ReactNode; close: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const element = dialog.current
    element?.showModal()
    return () => element?.close()
  }, [])
  return <dialog ref={dialog} aria-labelledby="dialog-title" className="dialog" onCancel={event => { event.preventDefault(); close() }}>
    <div className="dialog-header">
      <div><h2 id="dialog-title">{title}</h2>{description && <p>{description}</p>}</div>
      <button type="button" className="icon-button" aria-label="Close dialog" onClick={close}><X size={20} /></button>
    </div>
    {children}
  </dialog>
}

export function Alert({ children, success = false }: { children: ReactNode; success?: boolean }) {
  return <div className={'alert ' + (success ? 'success' : '')} role={success ? 'status' : 'alert'}>
    {success ? <Check size={18} /> : <CircleAlert size={18} />}<span>{children}</span>
  </div>
}

export function Status({ value }: { value: string }) {
  return <span className={'status ' + value}><i />{label(value)}</span>
}

export function Empty({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return <div className="empty"><span className="empty-mark"><i /><i /><i /></span><h3>{title}</h3><p>{description}</p>{action}</div>
}

export function Sketch({ kind, enclosed }: { kind: PrinterKind; enclosed: boolean }) {
  return <svg className="sketch" viewBox="0 0 236 122" aria-hidden="true">
    <ellipse cx="118" cy="110" rx="78" ry="9" fill="currentColor" opacity=".06" />
    <g stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      {kind === 'resin' ? <>
        <path d="M78 85V25l16-13h52l15 13v60Z" fill="currentColor" fillOpacity=".07" />
        <path d="M78 25h83m-67 0v60m52-60v60m-68 0h83v15l-7 7H85l-7-7Z" fill="none" />
        <path d="M103 96h32m-22-4h12m-23-23h35m-18-31v27m-16 9h33" />
      </> : enclosed ? <>
        <path d="M78 22h70l15 13v64l-13 10H73V36Z" fill="currentColor" fillOpacity=".08" />
        <path d="M73 36h77v73m0-73 13-1m-80 10h56v53H83Z" fill="none" />
        <path d="M91 55h39m-32 5h25m-14-1v14h9V59m-6 14v5m-23 9h44l-9-7H97Z" fill="none" />
        <path d="M105 91h14m18-26v14m19-34v44m-56-62h20m-21 5h21" />
      </> : <>
        <path d="M70 100h95l14-9h-94Zm8-4V28l9-9h66l9 9v67m-84-67h84m-76 0v62m68-62v62" fill="currentColor" fillOpacity=".06" />
        <path d="M86 52h68m-68 5h68m-40 0v13h14V57m-7 13v6m-29 6h49l9 6H83Z" fill="none" />
        <path d="M70 100v7h95l14-9v-7m-14 9v7m-55-80V12h22v15" fill="none" />
        <ellipse cx="121" cy="13" rx="11" ry="5" fill="currentColor" fillOpacity=".12" />
        {kind === 'belt' && <path d="M103 78l47-43m-39 43 41-38M96 81l47-47" opacity=".8" />}
      </>}
    </g>
    <path d="M28 105H53m130 0h25M40 26v16m-8-8h16m148 19v12m-6-6h12" stroke="currentColor" strokeOpacity=".25" />
  </svg>
}

export function CameraFeed({ printer, mode }: { printer: Printer; mode: string }) {
  const [failed, setFailed] = useState(false)
  const url = safeCameraUrl(mode, printer.camera_url)
  if (!url || failed) return <Empty title={failed ? 'Camera unavailable' : 'No live camera'} description={failed ? 'Check the camera URL and browser access to the printer’s network.' : mode === 'demo' ? 'Live camera requests are disabled in simulation.' : 'Add a direct MJPEG stream URL in Printer setup.'} />
  return <img className="camera-feed" alt={'Live camera for ' + printer.name} src={url} referrerPolicy="no-referrer" onError={() => setFailed(true)} />
}

export function CameraTag({ available, onClick }: { available: boolean; onClick: () => void }) {
  return available
    ? <button className="camera-tag" type="button" onClick={onClick}><Camera size={13} /> View camera</button>
    : <span className="camera-tag"><Camera size={13} /> No live feed</span>
}
