import { useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { ArrowUpRight, Boxes, Plus } from 'lucide-react'
import { errorText } from './api'
import type { FarmState, Printer, PrinterConfig, PrinterKind, Send, Volume } from './farm'
import { Alert } from './ui'

export function Field({ name, title, children, note, wide = false }: { name: string; title: string; children: ReactNode; note?: string; wide?: boolean }) {
  return <label className={'field' + (wide ? ' wide' : '')} htmlFor={name}>
    <span>{title}</span>{children}{note && <small>{note}</small>}
  </label>
}

function Dimensions({ defaults = [0, 0, 0], title }: { defaults?: Volume; title: string }) {
  return <fieldset className="dimensions wide">
    <legend>{title} <small>mm</small></legend>
    {['X', 'Y', 'Z'].map((axis, index) => <label key={axis}><span>{axis}</span><input aria-label={axis + ' dimension in millimetres'} name={'volume-' + index} type="number" min=".001" max="10000" step="any" required defaultValue={defaults[index] || undefined} placeholder="0" /></label>)}
  </fieldset>
}

function read(form: HTMLFormElement) {
  const data = new FormData(form)
  const string = (key: string) => String(data.get(key) ?? '').trim()
  const number = (key: string) => Number(string(key))
  const volume: Volume = [number('volume-0'), number('volume-1'), number('volume-2')]
  return { string, number, volume }
}

function useForm(close: () => void) {
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  async function submit(event: FormEvent<HTMLFormElement>, execute: (form: HTMLFormElement) => Promise<void>) {
    event.preventDefault()
    if (saving) return
    setError('')
    setSaving(true)
    try {
      await execute(event.currentTarget)
      close()
    } catch (problem) {
      setError(errorText(problem))
    } finally {
      setSaving(false)
    }
  }
  return { submit, error, saving }
}

export function BatchForm({ farm, send, close, openLibrary }: { farm: FarmState; send: Send; close: () => void; openLibrary: () => void }) {
  const { submit, error, saving } = useForm(close)
  const profiles = Array.from(new Set(farm.printers.filter(p => p.enabled && p.kind === 'fdm').map(p => p.profile))).filter(Boolean)
  if (!farm.files.length) return <div className="form-empty"><Boxes size={24} /><p>Upload a sliced G-code file before creating a batch.</p><button className="button primary" onClick={openLibrary}>Open library <ArrowUpRight size={15} /></button></div>
  return <form className="dialog-form" onSubmit={event => submit(event, form => {
    const { string, number, volume } = read(form)
    const start = string('not_before')
    return send('/jobs/batch', {
      name: string('name'), file_id: string('file_id'), quantity: number('quantity'), profile: string('profile'),
      volume, nozzle: number('nozzle'), material: string('material'), color: string('color'), grams: number('grams'),
      duration_minutes: number('duration_minutes'), priority: number('priority'), not_before: start ? new Date(start).toISOString() : '',
    })
  })}>
    <div className="form-grid">
      <Field name="name" title="Batch name"><input id="name" name="name" required maxLength={120} placeholder="e.g. Mounting bracket" /></Field>
      <Field name="file_id" title="G-code file"><select id="file_id" name="file_id" required>{farm.files.map(file => <option value={file.id} key={file.id}>{file.name}</option>)}</select></Field>
      <Field name="profile" title="Slicer profile" note="Must exactly match the printer’s configured profile."><input id="profile" name="profile" list="printer-profiles" required maxLength={100} placeholder="e.g. qidi-x-plus-3" defaultValue={profiles.length === 1 ? profiles[0] : ''} /><datalist id="printer-profiles">{profiles.map(p => <option key={p}>{p}</option>)}</datalist></Field>
      <Field name="quantity" title="Copies"><input id="quantity" name="quantity" type="number" min="1" max="100" required defaultValue="1" /></Field>
      <Dimensions title="Print envelope" />
      <Field name="nozzle" title="Nozzle (mm)"><input id="nozzle" name="nozzle" type="number" min=".001" max="2" step="any" required defaultValue=".4" /></Field>
      <Field name="material" title="Material"><input id="material" name="material" maxLength={40} required defaultValue="PLA" /></Field>
      <Field name="color" title="Colour (optional)"><input id="color" name="color" maxLength={40} placeholder="Any loaded colour" /></Field>
      <Field name="grams" title="Grams per copy"><input id="grams" name="grams" type="number" min=".001" max="100000" step="any" required placeholder="30" /></Field>
      <Field name="duration_minutes" title="Estimated minutes per copy"><input id="duration_minutes" name="duration_minutes" type="number" min=".001" max="100000" step="any" required placeholder="60" /></Field>
      <Field name="priority" title="Priority (0–100)"><input id="priority" name="priority" type="number" min="0" max="100" required defaultValue="50" /></Field>
      <Field name="not_before" title="Earliest start (local time)" note="Leave empty to queue for the next eligible slot." wide><input id="not_before" name="not_before" type="datetime-local" /></Field>
    </div>
    {error && <Alert>{error}</Alert>}
    <div className="form-actions"><button type="button" className="button" onClick={close}>Cancel</button><button type="submit" className="button primary" disabled={saving}><Plus size={16} />{saving ? 'Creating…' : 'Create batch'}</button></div>
  </form>
}

export function PrinterForm({ farm, printer, send, close }: { farm: FarmState; printer?: Printer; send: Send; close: () => void }) {
  const { submit, error, saving } = useForm(close)
  const [kind, setKind] = useState<PrinterKind>(printer?.kind ?? 'fdm')
  return <form className="dialog-form" onSubmit={event => submit(event, form => {
    const { string, number, volume } = read(form)
    const config: PrinterConfig = {
      name: string('name'), kind, profile: string('profile'), volume, nozzle: number('nozzle'), material: string('material'), color: string('color'),
      spool_id: string('spool_id') ? number('spool_id') : null, moonraker_url: string('moonraker_url'), camera_url: string('camera_url'),
      enabled: kind === 'fdm' && string('enabled') === 'on', bed_clear: false, api_key: string('api_key'),
    }
    return send(printer ? '/printers/' + printer.id : '/printers', config, printer ? 'PUT' : 'POST')
  })}>
    <div className="form-grid">
      <Field name="name" title="Printer name"><input id="name" name="name" maxLength={100} required defaultValue={printer?.name} /></Field>
      <Field name="kind" title="Printer type"><select id="kind" name="kind" value={kind} onChange={event => setKind(event.target.value as PrinterKind)}><option value="fdm">FDM</option><option value="belt">Belt · inventory only</option><option value="resin">Resin · inventory only</option></select></Field>
      <Field name="profile" title="Slicer profile"><input id="profile" name="profile" required maxLength={100} defaultValue={printer?.profile} placeholder="e.g. qidi-x-plus-3" /></Field>
      <Field name="nozzle" title="Nozzle (mm)"><input id="nozzle" name="nozzle" type="number" min=".001" max="2" step="any" required defaultValue={printer?.nozzle ?? .4} /></Field>
      <Dimensions title="Build volume" defaults={printer?.volume} />
      <Field name="material" title="Loaded material"><input id="material" name="material" maxLength={40} defaultValue={printer?.material ?? 'PLA'} required /></Field>
      <Field name="color" title="Loaded colour"><input id="color" name="color" maxLength={40} defaultValue={printer?.color} /></Field>
      <Field name="spool_id" title="Assigned spool" wide><select id="spool_id" name="spool_id" defaultValue={printer?.spool_id ?? ''}><option value="">No spool assigned</option>{printer?.spool_id && !farm.spools.some(spool => spool.id === printer.spool_id) && <option value={printer.spool_id}>Spool #{printer.spool_id} (unavailable)</option>}{farm.spools.map(spool => <option key={spool.id} value={spool.id}>#{spool.id} · {spool.name}</option>)}</select></Field>
      <Field name="moonraker_url" title="Moonraker URL" wide><input id="moonraker_url" name="moonraker_url" type="url" placeholder="http://192.168.1.10:7125" defaultValue={printer?.moonraker_url} /></Field>
      <Field name="api_key" title="Moonraker API key" note={printer ? 'Leave empty to keep the saved key.' : 'Optional if your Moonraker instance trusts the server.'} wide><input id="api_key" name="api_key" type="password" maxLength={512} autoComplete="new-password" /></Field>
      <Field name="camera_url" title="Camera stream URL" note="Direct MJPEG stream. Your browser must be able to reach it." wide><input id="camera_url" name="camera_url" type="url" placeholder="http://192.168.1.10/webcam/?action=stream" defaultValue={printer?.camera_url} /></Field>
      <label className="checkbox wide"><input type="checkbox" name="enabled" defaultChecked={printer?.enabled ?? false} disabled={kind !== 'fdm'} /> Enable FDM printer for scheduling and controls</label>
    </div>
    {error && <Alert>{error}</Alert>}
    <div className="form-actions"><button type="button" className="button" onClick={close}>Cancel</button><button type="submit" className="button primary" disabled={saving}>{saving ? 'Saving…' : 'Save printer'}</button></div>
  </form>
}
