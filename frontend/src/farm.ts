export type Volume = [number, number, number]
export type PrinterKind = 'fdm' | 'belt' | 'resin'
export type PrinterConfig = {
  name: string
  kind: PrinterKind
  profile: string
  volume: Volume
  nozzle: number
  material: string
  color: string
  spool_id: number | null
  moonraker_url: string
  camera_url: string
  enabled: boolean
  bed_clear: boolean
  api_key?: string
}
export type Printer = Omit<PrinterConfig, 'api_key'> & {
  id: string
  state: string
  progress: number
  hotend: number
  bed: number
  filename: string
  error: string | null
  current_job_id: string | null
  updated_at: string | null
}
export type Job = {
  id: string
  batch_id: string
  name: string
  file_id: string
  filename: string
  profile: string
  volume: Volume
  nozzle: number
  material: string
  color: string
  grams: number
  duration_minutes: number
  priority: number
  not_before: string
  state: string
  printer_id: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  error: string | null
}
export type FileRecord = { id: string; name: string; size: number; sha256: string; created_at: string }
export type Spool = { id: number; name: string; material: string; color: string; remaining_g: number | null; reserved_g: number; available_g: number; active_reserved_g: number }
export type FarmState = {
  mode: string
  scheduler_enabled: boolean
  printers: Printer[]
  jobs: Job[]
  files: FileRecord[]
  spools: Spool[]
  spoolman_error: string | null
  spoolman_updated_at: string | null
  ai: { enabled: false; status: string }
  server_time: string
}
export type Health = { status: string; mode: 'demo' | 'live'; version: string }
export type Send = (path: string, body?: object | FormData, method?: 'POST' | 'PUT') => Promise<void>

export function safeCameraUrl(mode: string, input: string): string {
  if (mode !== 'live') return ''
  try {
    const url = new URL(input)
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : ''
  } catch {
    return ''
  }
}

export function summarizeFarm(printers: Pick<Printer, 'enabled' | 'kind' | 'state' | 'bed_clear' | 'current_job_id'>[], jobs: Pick<Job, 'state'>[]) {
  const managed = printers.filter(printer => printer.enabled && printer.kind === 'fdm')
  return {
    active: managed.filter(printer => ['printing', 'paused'].includes(printer.state)).length,
    ready: managed.filter(printer => printer.bed_clear && isIdle(printer.state) && !printer.current_job_id).length,
    managed: managed.length,
    queued: jobs.filter(job => job.state === 'queued').length,
    held: jobs.filter(job => job.state === 'needs_attention').length,
  }
}

export function isIdle(state: string) {
  return ['standby', 'complete', 'cancelled'].includes(state)
}

export function time(value: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return '—'
  return date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export function label(value: string) {
  return value.replaceAll('_', ' ').replace(/^./, first => first.toUpperCase())
}
