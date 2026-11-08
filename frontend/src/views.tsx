import { useState } from 'react'
import type { ChangeEvent } from 'react'
import { Activity, ArrowRight, Boxes, CircleCheck, CircleHelp, Clock3, Cog, FileCode2, Flame, Layers3, ListFilter, PackageOpen, Pause, Pencil, Play, Plus, Printer as PrinterIcon, Square, Thermometer, Upload, Workflow, Wrench } from 'lucide-react'
import type { FarmState, Job, Printer, Send } from './farm'
import { isIdle, safeCameraUrl, summarizeFarm, time } from './farm'
import { Alert, CameraTag, Empty, Sketch, Status } from './ui'
import { errorText } from './api'

export type Confirm = { title: string; description: string; label: string; path: string; body?: object; danger?: boolean }
export type OpenConfirm = (data: Confirm) => void
type CardActions = { confirm: OpenConfirm; edit: (printer: Printer) => void; camera: (printer: Printer) => void }

function PrinterCard({ printer, farm, index, actions }: { printer: Printer; farm: FarmState; index: number; actions: CardActions }) {
  const tracked = farm.jobs.find(job => job.id === printer.current_job_id)
  const usable = printer.enabled && printer.kind === 'fdm'
  const active = ['printing', 'paused'].includes(printer.state)
  const percent = Number.isFinite(printer.progress) ? Math.min(100, Math.max(0, printer.progress * 100)) : 0
  const progress = Math.round(percent)
  const status = printer.kind !== 'fdm' ? 'inventory' : !printer.enabled ? 'disabled' : printer.state
  const clear = usable && isIdle(printer.state) && !printer.current_job_id && !printer.bed_clear
  const command = (action: 'pause' | 'resume' | 'cancel') => actions.confirm({
    title: (action === 'cancel' ? 'Cancel' : action === 'pause' ? 'Pause' : 'Resume') + ' print?',
    description: 'Send a ' + action + ' command to ' + printer.name + '.' + (action === 'cancel' ? ' The job stays assigned until the printer reports its result.' : ''),
    label: (action === 'cancel' ? 'Cancel' : action === 'pause' ? 'Pause' : 'Resume') + ' print',
    path: '/printers/' + printer.id + '/control',
    body: { action },
    danger: action === 'cancel',
  })
  return <article className={'printer-card ' + (active && usable ? 'working' : '')}>
    <header className="printer-header">
      <span className="device-icon"><PrinterIcon size={17} /></span>
      <div className="printer-name"><h3>{printer.name}</h3><span>{farm.mode === 'demo' && usable ? 'SIMULATED FDM' : printer.kind === 'fdm' ? 'FDM' : printer.kind.toUpperCase()} · {printer.nozzle} mm</span></div>
      <Status value={status} />
    </header>
    <div className="printer-art">
      <span className="device-id">UNIT {String(index + 1).padStart(2, '0')}</span>
      <Sketch kind={printer.kind} enclosed={/qidi|enclosed/i.test(printer.name + ' ' + printer.profile)} />
      <CameraTag available={Boolean(safeCameraUrl(farm.mode, printer.camera_url))} onClick={() => actions.camera(printer)} />
    </div>
    <div className="printer-body">
      {active ? <>
        <div className="print-label"><span title={tracked?.name || printer.filename}>{tracked?.name || printer.filename || 'Print in progress'}</span><strong>{progress}%</strong></div>
        <progress max="100" value={percent} aria-label={printer.name + ' print progress'} />
      </> : <div className="print-label idle-label"><span>{tracked?.state === 'needs_attention' ? 'Operator review required' : !usable ? 'Inventory entry' : printer.bed_clear ? 'Bed cleared for dispatch' : isIdle(printer.state) ? 'Awaiting bed clearance' : 'Waiting for printer connection'}</span></div>}
      <div className="printer-measures">
        <span title="Hotend"><Flame size={15} />{usable && printer.updated_at && printer.state !== 'offline' ? Math.round(printer.hotend) + '°C' : '—'}</span>
        <span title="Bed"><Thermometer size={15} />{usable && printer.updated_at && printer.state !== 'offline' ? Math.round(printer.bed) + '°C' : '—'}</span>
        <span title="Loaded material"><Layers3 size={15} />{printer.material || '—'}</span>
      </div>
      {usable && printer.error && <p className="printer-error">{printer.error}</p>}
    </div>
    <footer className="printer-footer">
      {usable && active ? <>
        {printer.state === 'paused' ? <button className="button compact" onClick={() => command('resume')}><Play size={14} /> Resume print</button> : <button className="button compact" onClick={() => command('pause')}><Pause size={14} /> Pause print</button>}
        <button className="icon-button danger-text" onClick={() => command('cancel')} aria-label={'Cancel print on ' + printer.name}><Square size={14} /></button>
      </> : clear ? <button className="button compact clear-button" onClick={() => actions.confirm({
        title: 'Confirm clear bed',
        description: 'Confirm the build plate on ' + printer.name + ' is empty and safe. If the scheduler is running, an eligible job can start automatically.',
        label: 'Confirm clear bed',
        path: '/printers/' + printer.id + '/clear-bed',
      })}><CircleCheck size={15} /> Clear bed</button> : <span className="card-note">{usable && printer.bed_clear ? <><CircleCheck size={14} /> Ready to dispatch</> : <><Cog size={14} /> {usable ? 'Printer controls unavailable' : 'Scheduling disabled'}</>}</span>}
      <button className="icon-button edit-printer" onClick={() => actions.edit(printer)} aria-label={'Edit ' + printer.name} disabled={Boolean(printer.current_job_id)}><Pencil size={15} /></button>
    </footer>
  </article>
}

export function Overview({ farm, actions, openQueue, openSetup }: { farm: FarmState; actions: CardActions; openQueue: () => void; openSetup: () => void }) {
  const summary = summarizeFarm(farm.printers, farm.jobs)
  const [filter, setFilter] = useState<'all' | 'managed'>('all')
  const fleet = farm.printers.filter(p => filter === 'all' || p.enabled && p.kind === 'fdm')
  const stats = [
    { icon: Activity, name: 'Active prints', value: summary.active, note: 'Printing or paused', color: 'orange' },
    { icon: ListFilter, name: 'Queued jobs', value: summary.queued, note: 'Copies awaiting dispatch', color: 'blue' },
    { icon: CircleCheck, name: 'Ready printers', value: summary.ready, note: summary.managed + ' managed FDM printers', color: 'green' },
    { icon: CircleHelp, name: 'Needs attention', value: summary.held, note: 'Jobs held for inspection', color: 'yellow' },
  ]
  return <>
    <section className="stats" aria-label="Farm summary">{stats.map(({ icon: Icon, name, value, note, color }) => <article className="stat" key={name}>
      <div className="stat-top"><span className={'stat-icon ' + color}><Icon size={17} /></span><span>{name}</span></div>
      <div className="stat-value">{String(value).padStart(2, '0')}</div>
      <p>{note}</p>
    </article>)}</section>
    {summary.held > 0 && <div className="attention-banner"><CircleHelp size={20} /><div><strong>{summary.held} {summary.held === 1 ? 'job needs' : 'jobs need'} inspection</strong><span>Inspect the assigned printer before resolving a held job.</span></div><button className="button compact" onClick={openQueue}>View queue <ArrowRight size={15} /></button></div>}
    <section className="fleet-section" aria-labelledby="fleet-title">
      <div className="section-heading"><div><div className="eyebrow"><span /> MONITORING</div><h2 id="fleet-title">Printer fleet</h2><p>{farm.printers.length} {farm.printers.length === 1 ? 'printer' : 'printers'} in the inventory</p></div><div className="segmented" aria-label="Printer filter"><button className={filter === 'all' ? 'selected' : ''} aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>All printers</button><button className={filter === 'managed' ? 'selected' : ''} aria-pressed={filter === 'managed'} onClick={() => setFilter('managed')}>Managed</button></div></div>
      {fleet.length ? <div className="printer-grid">{fleet.map(printer => <PrinterCard key={printer.id} printer={printer} farm={farm} index={farm.printers.indexOf(printer)} actions={actions} />)}</div> : <Empty title="No printers in this view" description="Add a printer or enable an FDM machine in Printer setup." action={<button className="button" onClick={openSetup}><Wrench size={16} /> Printer setup</button>} />}
    </section>
    <section className="queue-callout"><span className="device-icon"><Workflow size={20} /></span><div><h3>Batch queue</h3><p>Assign sliced G-code to compatible, cleared printers.</p></div><button className="button" onClick={openQueue}>Open queue <ArrowRight size={16} /></button></section>
  </>
}

export function Queue({ farm, confirm, openBatch, history = false }: { farm: FarmState; confirm: OpenConfirm; openBatch: () => void; history?: boolean }) {
  const [outcomes, setOutcomes] = useState<Record<string, 'completed' | 'failed' | 'cancelled'>>({})
  const visible = farm.jobs.filter(job => ['completed', 'failed', 'cancelled'].includes(job.state) === history)
    .sort((a, b) => history ? (b.finished_at ?? '').localeCompare(a.finished_at ?? '') : queueOrder(a, b))
  function queueOrder(a: Job, b: Job) {
    const rank = (job: Job) => ['needs_attention', 'running', 'dispatching', 'queued'].indexOf(job.state)
    return rank(a) - rank(b) || b.priority - a.priority || a.not_before.localeCompare(b.not_before) || a.created_at.localeCompare(b.created_at)
  }
  if (!visible.length) return <Empty title={history ? 'No finished jobs' : 'The queue is clear'} description={history ? 'Completed, failed and cancelled jobs will appear here.' : 'Create a batch from a G-code file to queue one or more copies.'} action={!history && <button className="button primary" onClick={openBatch}><Plus size={16} /> New batch</button>} />
  return <div className="table-shell"><table>
    <thead><tr><th>Job</th><th>Status</th><th>Printer</th><th>{history ? 'Finished' : 'Earliest start'}</th>{!history && <th>Actions</th>}</tr></thead>
    <tbody>{visible.map(job => <tr key={job.id}>
      <td><strong>{job.name}</strong><small>{job.filename} · {job.grams} g {job.material}</small>{job.error && <small className="error-text">{job.error}</small>}</td>
      <td><Status value={job.state} /></td>
      <td>{farm.printers.find(printer => printer.id === job.printer_id)?.name ?? '—'}</td>
      <td><span className="table-time"><Clock3 size={14} /> {time(history ? job.finished_at : job.not_before)}</span>{!history && <small>Priority {job.priority} · {job.duration_minutes} min</small>}</td>
      {!history && <td>{job.state === 'queued' ? <button className="button compact" onClick={() => confirm({
        title: 'Cancel queued job?', description: 'Remove this copy of ' + job.name + ' from the dispatch queue.', label: 'Cancel queued job', path: '/jobs/' + job.id + '/cancel', danger: true,
      })}>Cancel job</button> : job.state === 'needs_attention' ? <div className="resolve-actions">
        <select aria-label={'Outcome for ' + job.name} value={outcomes[job.id] ?? 'failed'} onChange={event => setOutcomes(current => ({ ...current, [job.id]: event.target.value as 'completed' | 'failed' | 'cancelled' }))}><option value="failed">Failed</option><option value="completed">Completed</option><option value="cancelled">Cancelled</option></select>
        <button className="button compact" onClick={() => confirm({
          title: 'Resolve held job?',
          description: 'Confirm you inspected the assigned printer, verified it is idle and want to mark this job as ' + (outcomes[job.id] ?? 'failed') + '.',
          label: 'Resolve job', path: '/jobs/' + job.id + '/resolve', body: { outcome: outcomes[job.id] ?? 'failed' },
        })}>Resolve</button>
      </div> : '—'}</td>}
    </tr>)}</tbody>
  </table></div>
}

export function Library({ farm, send }: { farm: FarmState; send: Send }) {
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0]
    event.currentTarget.value = ''
    if (!file) return
    setError('')
    if (!/\.gcode$/i.test(file.name) || file.size > 50 * 1024 * 1024) {
      setError('Select a .gcode file no larger than 50 MiB.')
      return
    }
    setUploading(true)
    const body = new FormData()
    body.set('file', file)
    try {
      await send('/files', body)
    } catch (problem) {
      setError(errorText(problem))
    } finally {
      setUploading(false)
    }
  }
  return <>
    <div className="upload-box"><span className="device-icon"><FileCode2 size={24} /></span><div><h3>Upload sliced G-code</h3><p>One .gcode file up to 50 MiB per upload.</p></div><label className="button primary upload-button"><Upload size={16} /> {uploading ? 'Uploading…' : 'Choose G-code'}<input type="file" aria-label="Choose G-code file" accept=".gcode" onChange={upload} disabled={uploading} /></label></div>
    {error && <Alert>{error}</Alert>}
    {farm.files.length ? <div className="table-shell"><table><thead><tr><th>File name</th><th>Size</th><th>Added</th><th>Checksum</th></tr></thead><tbody>
      {farm.files.map(file => <tr key={file.id}><td><strong className="file-name"><FileCode2 size={17} /> {file.name}</strong></td><td>{file.size.toLocaleString()} bytes</td><td>{time(file.created_at)}</td><td><code title={file.sha256}>{file.sha256.slice(0, 12)}</code></td></tr>)}
    </tbody></table></div> : <Empty title="No G-code files yet" description="Upload a file to make it available for batch scheduling." />}
  </>
}

export function Filament({ farm, confirm }: { farm: FarmState; confirm: OpenConfirm }) {
  return <>
    {farm.spoolman_error && <Alert>{farm.spoolman_error}</Alert>}
    <div className="inventory-note"><Layers3 size={17} /> {farm.mode === 'demo' ? 'Simulated spool inventory' : 'Spoolman inventory'} <span>Updated {time(farm.spoolman_updated_at)}</span></div>
    {farm.spools.length ? <div className="table-shell"><table><thead><tr><th>Spool</th><th>Material</th><th>Colour</th><th>Remaining</th><th>Reserved / held</th><th>Schedulable</th><th>Printer</th><th>Actions</th></tr></thead><tbody>
      {farm.spools.map(spool => <tr key={spool.id}><td><strong>{spool.name}</strong><small>Spool #{spool.id}</small></td><td>{spool.material}</td><td>{spool.color || '—'}</td><td>{spool.remaining_g == null ? '—' : Math.round(spool.remaining_g) + ' g'}</td><td>{Math.round(spool.reserved_g) + ' g'}</td><td>{spool.remaining_g == null ? '—' : Math.round(spool.available_g) + ' g'}</td><td>{farm.printers.find(p => p.spool_id === spool.id)?.name ?? '—'}</td><td>
        {spool.reserved_g > spool.active_reserved_g ? <button className="button compact" onClick={() => confirm({
          title: 'Reconcile spool #' + spool.id + '?',
          description: 'Confirm Spoolman’s remaining weight includes all finished prints or your physical re-weigh. This releases the conservative accounting hold.',
          label: 'Reconcile inventory',
          path: '/spools/' + spool.id + '/reconcile',
        })}>Reconcile inventory</button> : '—'}
      </td></tr>)}
    </tbody></table></div> : <Empty title="No spool data available" description="Connect Spoolman to supply filament inventory for scheduling." />}
  </>
}

export function Setup({ farm, add, edit }: { farm: FarmState; add: () => void; edit: (printer: Printer) => void }) {
  return <>
    <div className="inventory-note"><Boxes size={17} /> <div>Profiles identify compatible sliced G-code and start macros. Belt and resin printers are tracked as inventory.</div></div>
    {farm.printers.length ? <div className="table-shell"><table><thead><tr><th>Printer</th><th>Profile</th><th>Build volume</th><th>Moonraker</th><th>Setup</th><th>Actions</th></tr></thead><tbody>
      {farm.printers.map(p => <tr key={p.id}>
        <td><strong>{p.name}</strong><small>{p.kind.toUpperCase()}</small></td>
        <td><code>{p.profile}</code></td>
        <td>{p.volume.join(' × ')} mm</td>
        <td>{p.moonraker_url || '—'}</td>
        <td><Status value={p.kind !== 'fdm' ? 'inventory' : p.enabled ? 'enabled' : 'disabled'} /></td>
        <td><button className="button compact" onClick={() => edit(p)} disabled={Boolean(p.current_job_id)} title={p.current_job_id ? 'Configuration is locked while a job is assigned' : undefined}><Pencil size={14} /> Edit</button></td>
      </tr>)}
    </tbody></table></div> : <Empty title="No printers configured" description="Add a printer with its slicer profile and connection details." action={<button className="button primary" onClick={add}><Plus size={16} /> Add printer</button>} />}
  </>
}
