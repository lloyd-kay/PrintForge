import { useEffect, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { Activity, ArrowRight, ChevronRight, FlaskConical, FolderCode, History, Layers3, LayoutDashboard, ListOrdered, LockKeyhole, LogOut, Pause, Play, Plus, Radio, RefreshCw, Settings2, Sparkles, X } from 'lucide-react'
import { ApiError, errorText, request } from './api'
import type { FarmState, Health, Printer, Send } from './farm'
import { time } from './farm'
import { BatchForm, PrinterForm } from './forms'
import { Alert, CameraFeed, Dialog } from './ui'
import { Filament, Library, Overview, Queue, Setup } from './views'
import type { Confirm } from './views'
import './styles.css'

const pages = [
  { id: 'overview', title: 'Overview', heading: 'Farm overview', description: 'Monitor your printer fleet and manage production from one place.', icon: LayoutDashboard },
  { id: 'queue', title: 'Print queue', heading: 'Print queue', description: 'Create batches, schedule start times and review jobs that need inspection.', icon: ListOrdered },
  { id: 'library', title: 'G-code library', heading: 'G-code library', description: 'Upload sliced files that can be reused across batches.', icon: FolderCode },
  { id: 'filament', title: 'Filament', heading: 'Filament', description: 'Check spool levels and see what each print has reserved.', icon: Layers3 },
  { id: 'history', title: 'History', heading: 'Print history', description: 'Review completed, failed and cancelled print jobs.', icon: History },
  { id: 'setup', title: 'Printer setup', heading: 'Printer setup', description: 'Configure printer profiles, Moonraker connections and camera streams.', icon: Settings2 },
] as const
type Page = typeof pages[number]['id']
type Modal = { type: 'batch' } | { type: 'printer'; printer?: Printer } | { type: 'camera'; printer: Printer } | { type: 'confirm'; data: Confirm }

function pageFromHash(): Page {
  return pages.find(page => '#' + page.id === window.location.hash)?.id ?? 'overview'
}

function Logo() {
  return <div className="logo">
    <span className="brand-mark"><svg viewBox="0 0 24 24" width="24" height="24" fill="none" aria-hidden="true"><path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5m-18 4 9 5 9-5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg></span>
    <div><strong>Print<span>Forge</span></strong><small>FARM CONTROL</small></div>
  </div>
}

function Entry({ health, loading, error, login, retry }: { health: Health | null; loading: boolean; error: string; login: (token: string) => Promise<void>; retry: () => void }) {
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const token = String(new FormData(event.currentTarget).get('token') ?? '').trim()
    if (token) await login(token)
  }
  return <main className="entry">
    <section className="entry-card">
      <Logo />
      <div className="eyebrow"><span /> OPERATOR ACCESS</div>
      <h1>{loading ? 'Connecting to PrintForge' : 'Connect to your farm'}</h1>
      <p>{loading ? 'Checking the service and loading your workspace.' : health ? 'Enter the administrator API token for this PrintForge instance.' : 'Check that the PrintForge service is running, then retry.'}</p>
      {error && <Alert>{error}</Alert>}
      {!loading && health && <form onSubmit={submit}>
        <label className="field" htmlFor="token"><span>Administrator token</span><input id="token" name="token" type="password" required autoComplete="off" autoFocus /></label>
        <button className="button primary" type="submit"><LockKeyhole size={16} /> Connect <ArrowRight size={16} /></button>
      </form>}
      {!loading && !health && <button className="button" onClick={retry}><RefreshCw size={16} /> Retry connection</button>}
    </section>
  </main>
}

function ConfirmAction({ data, close, send }: { data: Confirm; close: () => void; send: Send }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function confirm() {
    setBusy(true)
    setError('')
    try {
      await send(data.path, data.body)
      close()
    } catch (problem) {
      setError(errorText(problem))
    } finally {
      setBusy(false)
    }
  }
  return <div className="dialog-form">
    {error && <Alert>{error}</Alert>}
    <div className="form-actions"><button className="button" onClick={close}>Back</button><button className={'button ' + (data.danger ? 'danger' : 'primary')} onClick={confirm} disabled={busy}>{busy ? 'Sending…' : data.label}</button></div>
  </div>
}

function App() {
  const [page, setPage] = useState<Page>(pageFromHash)
  const [health, setHealth] = useState<Health | null>(null)
  const [token, setToken] = useState('')
  const [farm, setFarm] = useState<FarmState | null>(null)
  const [loading, setLoading] = useState(true)
  const [entryError, setEntryError] = useState('')
  const [pollError, setPollError] = useState('')
  const [notice, setNotice] = useState<{ message: string; success: boolean } | null>(null)
  const [busy, setBusy] = useState(false)
  const [modal, setModal] = useState<Modal | null>(null)
  const mutation = useRef(false)
  const refreshGeneration = useRef(0)
  const session = useRef('')
  const nav = pages.find(item => item.id === page)!
  const close = () => setModal(null)
  const openBatch = () => setModal({ type: 'batch' })
  const openSetup = () => navigate('setup')
  const openQueue = () => navigate('queue')
  const openLibrary = () => { close(); navigate('library') }
  const openPrinter = (printer?: Printer) => setModal({ type: 'printer', printer })
  const openConfirm = (data: Confirm) => setModal({ type: 'confirm', data })

  function navigate(next: Page) {
    if (page === next) return
    window.location.hash = next
    window.scrollTo({ top: 0 })
    setPage(next)
  }

  useEffect(() => {
    const onHashChange = () => setPage(pageFromHash())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  async function connect(key: string) {
    const data = await request<FarmState>('/state', key, 'GET', undefined, AbortSignal.timeout(20000))
    session.current = key
    setToken(key)
    setFarm(data)
    setPollError('')
    setNotice(null)
  }

  async function login(key: string) {
    setLoading(true)
    setEntryError('')
    try {
      await connect(key)
    } catch (problem) {
      setEntryError(errorText(problem))
    } finally {
      setLoading(false)
    }
  }

  async function bootstrap() {
    setLoading(true)
    setEntryError('')
    try {
      const result = await request<Health>('/health', '', 'GET', undefined, AbortSignal.timeout(10000))
      setHealth(result)
      if (result.mode === 'demo') await connect('printforge-demo')
    } catch (problem) {
      setEntryError(errorText(problem))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void bootstrap() }, [])

  function disconnect(message = '') {
    session.current = ''
    refreshGeneration.current++
    setToken('')
    setFarm(null)
    setModal(null)
    setEntryError(message)
  }

  async function refresh(key: string) {
    const generation = ++refreshGeneration.current
    try {
      const data = await request<FarmState>('/state', key, 'GET', undefined, AbortSignal.timeout(15000))
      if (session.current !== key || refreshGeneration.current !== generation) return
      setFarm(data)
      setPollError('')
    } catch (problem) {
      if (session.current !== key || refreshGeneration.current !== generation) return
      if (problem instanceof ApiError && [401, 403].includes(problem.status)) {
        disconnect('Your administrator token was rejected. Connect again to continue.')
        return
      }
      setPollError('Unable to refresh farm data. Displayed values may be out of date. ' + errorText(problem))
    }
  }

  useEffect(() => {
    if (!token) return
    let stop = false
    let timer: number
    const poll = async () => {
      if (!mutation.current) await refresh(token)
      if (!stop) timer = window.setTimeout(poll, 5000)
    }
    timer = window.setTimeout(poll, 5000)
    return () => { stop = true; window.clearTimeout(timer) }
  }, [token])

  const send: Send = async (path, body, method = 'POST') => {
    if (mutation.current) throw new Error('Another operation is in progress. Wait for it to finish.')
    if (!session.current) throw new Error('Connect to the farm before sending a command.')
    const key = session.current
    mutation.current = true
    refreshGeneration.current++
    setBusy(true)
    setNotice(null)
    try {
      await request(path, key, method, body)
      if (session.current !== key) return
      await refresh(key)
      if (session.current === key) setNotice({ success: true, message: path === '/sync' ? 'Farm sync complete.' : 'Farm updated.' })
    } catch (problem) {
      if (problem instanceof ApiError && [401, 403].includes(problem.status) && session.current === key) disconnect('Your administrator token was rejected. Connect again to continue.')
      throw problem
    } finally {
      mutation.current = false
      setBusy(false)
    }
  }

  async function quick(path: string, body?: object) {
    try {
      await send(path, body)
    } catch (problem) {
      setNotice({ success: false, message: errorText(problem) })
    }
  }

  useEffect(() => {
    if (!notice?.success) return
    const timer = window.setTimeout(() => setNotice(null), 4500)
    return () => window.clearTimeout(timer)
  }, [notice])

  if (!farm) return <Entry health={health} loading={loading} error={entryError} login={login} retry={bootstrap} />

  const demo = farm.mode === 'demo'
  const queued = farm.jobs.filter(job => job.state === 'queued' || job.state === 'needs_attention').length
  let content: ReactNode
  switch (page) {
    case 'overview': content = <Overview farm={farm} actions={{ confirm: openConfirm, edit: openPrinter, camera: printer => setModal({ type: 'camera', printer }) }} openQueue={openQueue} openSetup={openSetup} />; break
    case 'queue': content = <Queue key="queue" farm={farm} confirm={openConfirm} openBatch={openBatch} />; break
    case 'history': content = <Queue key="history" farm={farm} confirm={openConfirm} openBatch={openBatch} history />; break
    case 'library': content = <Library farm={farm} send={send} />; break
    case 'filament': content = <Filament farm={farm} confirm={openConfirm} />; break
    case 'setup': content = <Setup farm={farm} add={() => openPrinter()} edit={openPrinter} />; break
  }

  function startScheduler() {
    openConfirm({
      title: 'Start the scheduler?',
      description: demo ? 'Eligible jobs will start automatically on cleared simulated printers.' : 'Eligible jobs can start automatically on cleared printers. Confirm the fleet is ready for unattended dispatch.',
      label: 'Start scheduler',
      path: '/scheduler',
      body: { enabled: true },
    })
  }

  return <div className="app">
    <a className="skip" href="#main-content" onClick={event => { event.preventDefault(); document.getElementById('main-content')?.focus() }}>Skip to content</a>
    <aside className="sidebar">
      <Logo />
      <div className="nav-label">WORKSPACE</div>
      <nav aria-label="Workspace">{pages.map(item => <a key={item.id} href={'#' + item.id} aria-current={page === item.id ? 'page' : undefined} title={item.title} onClick={event => { event.preventDefault(); navigate(item.id) }}>
        <item.icon size={18} /><span>{item.title}</span>{item.id === 'queue' && queued > 0 && <em>{queued}</em>}
      </a>)}</nav>
      <div className="sidebar-bottom">
        <div className="ai-note"><span className="ai-icon"><Sparkles size={17} /></span><div><strong>AI monitoring</strong><small>Planned · Off</small></div></div>
        <div className="operator"><span className="avatar"><LockKeyhole size={15} /></span><div><strong>Operator</strong><small>Local workspace</small></div><button className="icon-button" aria-label="Disconnect" title="Disconnect" onClick={() => disconnect()}><LogOut size={17} /></button></div>
      </div>
    </aside>
    <div className="workspace">
      <header className="topbar">
        <div className="breadcrumbs"><span>Workspace</span><ChevronRight size={15} /><strong>{nav.title}</strong></div>
        <div className="top-actions"><span className={'mode-chip ' + (demo ? 'demo' : 'live')} title={demo ? 'No physical printer connections' : 'Live printer connections'}>{demo ? <FlaskConical size={14} /> : <Radio size={14} />}{demo ? 'Demo mode' : 'Live mode'}</span>
          <button className="icon-button refresh" title="Sync telemetry and inventory" aria-label="Sync telemetry and inventory" onClick={() => quick('/sync')} disabled={busy}><RefreshCw size={17} /></button></div>
      </header>
      <main id="main-content" className="main" tabIndex={-1}>
        <header className="page-heading">
          <div><div className="eyebrow"><span /> PRODUCTION WORKSPACE</div><h1>{nav.heading}</h1><p>{nav.description}</p></div>
          <div className="heading-actions">{page === 'overview' && <button className="button" disabled={busy || Boolean(pollError)} onClick={() => farm.scheduler_enabled ? quick('/scheduler', { enabled: false }) : startScheduler()}>{farm.scheduler_enabled ? <Pause size={16} /> : <Play size={16} />} {farm.scheduler_enabled ? 'Pause scheduler' : 'Start scheduler'}</button>}
          {(page === 'overview' || page === 'queue') && <button className="button primary" onClick={openBatch}><Plus size={17} /> New batch</button>}
          {page === 'setup' && <button className="button primary" onClick={() => openPrinter()}><Plus size={17} /> Add printer</button>}</div>
        </header>
        {pollError && <Alert>{pollError}</Alert>}
        {notice && <div className="notice"><Alert success={notice.success}>{notice.message}</Alert><button className="icon-button" aria-label="Dismiss notification" onClick={() => setNotice(null)}><X size={16} /></button></div>}
        {page === 'overview' && <div className="farm-strip">
          <div className="scheduler-indicator"><i className={farm.scheduler_enabled ? 'active' : ''} /><strong>Scheduler {farm.scheduler_enabled ? 'running' : 'paused'}</strong></div>
          <span className="strip-divider" /><span className="mode-description">{demo ? 'Simulated printers and inventory' : 'Moonraker · Spoolman'}</span>
          <span className="last-update"><Activity size={14} /> Last updated {time(farm.server_time)}</span>
        </div>}
        {content}
        <footer className="workspace-footer"><span>PRINTFORGE / {health?.version ?? '0.1.0'}</span><span>{demo ? 'SIMULATION' : 'LIVE CONNECTIONS'}</span></footer>
      </main>
    </div>
    {modal?.type === 'batch' && <Dialog title="Create a batch" description="Each copy is tracked as a separate job." close={close}><BatchForm farm={farm} send={send} close={close} openLibrary={openLibrary} /></Dialog>}
    {modal?.type === 'printer' && <Dialog title={modal.printer ? 'Edit printer' : 'Add printer'} description="Configure scheduling compatibility and connections." close={close}><PrinterForm farm={farm} printer={modal.printer} send={send} close={close} /></Dialog>}
    {modal?.type === 'camera' && <Dialog title={modal.printer.name} description="Direct camera stream" close={close}><CameraFeed key={modal.printer.id} printer={modal.printer} mode={farm.mode} /></Dialog>}
    {modal?.type === 'confirm' && <Dialog title={modal.data.title} description={modal.data.description} close={close}><ConfirmAction data={modal.data} send={send} close={close} /></Dialog>}
  </div>
}

createRoot(document.getElementById('root')!).render(<App />)
