import assert from 'node:assert/strict'
import { test } from 'node:test'
import { safeCameraUrl, summarizeFarm } from './farm.ts'

test('camera URLs are suppressed in demo and reject non-HTTP schemes', () => {
  assert.equal(safeCameraUrl('demo', 'http://10.0.0.42/stream'), '')
  assert.equal(safeCameraUrl('live', 'javascript:alert(1)'), '')
  assert.equal(safeCameraUrl('live', 'http://10.0.0.42/stream'), 'http://10.0.0.42/stream')
})

test('disabled inventory is excluded from active and ready printer counts', () => {
  const printers: Parameters<typeof summarizeFarm>[0] = [
    { enabled: false, kind: 'fdm', state: 'printing', bed_clear: false, current_job_id: null },
    { enabled: true, kind: 'fdm', state: 'standby', bed_clear: true, current_job_id: null },
    { enabled: true, kind: 'fdm', state: 'standby', bed_clear: false, current_job_id: null },
    { enabled: true, kind: 'belt', state: 'standby', bed_clear: true, current_job_id: null },
    { enabled: true, kind: 'fdm', state: 'paused', bed_clear: false, current_job_id: 'job-1' },
  ]
  assert.deepEqual(summarizeFarm(printers, [{ state: 'queued' }, { state: 'needs_attention' }]), {
    active: 1, ready: 1, managed: 3, queued: 1, held: 1,
  })
})
