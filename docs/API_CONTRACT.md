# MVP API contract (implementation reference)

All `/api/*` except `/api/health` require `Authorization: Bearer <PRINTFORGE_TOKEN>`. Demo default token: `printforge-demo`. Token is kept in browser memory. JSON error: `{detail: string}` (validation errors may be arrays). GET `/api/health`: `{status:'ok',mode:'demo'|'live',version:'0.1.0'}`. GET `/api/state` returns the dashboard document below. UI polls every 5s.

```ts
type Printer = {
 id:string; name:string; kind:'fdm'|'belt'|'resin'; profile:string;
 volume:[number,number,number]; nozzle:number; material:string; color:string;
 spool_id:number|null; moonraker_url:string; camera_url:string;
 enabled:boolean; bed_clear:boolean; state:string; progress:number;
 hotend:number; bed:number; filename:string; error:string|null;
 current_job_id:string|null; updated_at:string|null;
};
type Job = {
 id:string; batch_id:string; name:string; file_id:string; filename:string;
 profile:string; volume:[number,number,number]; nozzle:number;
 material:string; color:string; grams:number; duration_minutes:number;
 priority:number; not_before:string; state:string;
 printer_id:string|null; created_at:string; started_at:string|null;
 finished_at:string|null; error:string|null;
};
type FileRecord={id:string;name:string;size:number;sha256:string;created_at:string};
type Spool={id:number;name:string;material:string;color:string;remaining_g:number|null;reserved_g:number;available_g:number;active_reserved_g:number};
type FarmState={mode:string;scheduler_enabled:boolean;printers:Printer[];jobs:Job[];files:FileRecord[];spools:Spool[];spoolman_error:string|null;spoolman_updated_at:string|null;ai:{enabled:false;status:string};server_time:string};
```

- POST `/api/printers`: printer config fields above excluding telemetry/id; optional `api_key`. Volume means XYZ mm. API returns saved Printer. All fields required except key and camera. New printer bed_clear always false regardless input.
- PUT `/api/printers/{id}`: full editable config; optional api_key (empty keeps previous). Reject configuration changes while reserved; returns Printer. Non-fdm entries are inventory only.
- POST `/api/printers/{id}/clear-bed`: requires fresh ready idle telemetry and no active/held job. Sets clearance; returns `{ok:true}`.
- POST `/api/printers/{id}/control`: `{action:'pause'|'resume'|'cancel'}`; physical command to live printer; cancel retains job reservation pending telemetry. Returns `{ok:true}` or useful failure. No automatic cancel retries.
- POST `/api/files`: multipart `file` ending .gcode, max 50MiB. Returns FileRecord.
- POST `/api/jobs/batch`: `{name,file_id,quantity,profile,volume,nozzle,material,color,grams,duration_minutes,priority,not_before}`. quantity 1..100, grams/duration >0, priority 0..100, not_before ISO timestamp (empty means now). Creates one Job per copy; `{jobs:Job[],batch_id:string}`.
- POST `/api/jobs/{id}/cancel`: queued only, `{ok:true}`.
- POST `/api/jobs/{id}/resolve`: `{outcome:'failed'|'completed'|'cancelled'}`; needs_attention only, operator has inspected printer, requires idle fresh telemetry; returns `{ok:true}`. Never automatically retries.
- POST `/api/scheduler`: `{enabled:boolean}`; `{ok:true}`. Default paused on every service start. Enable is explicit operator action. Tick every 5 sec while enabled; telemetry updates even when paused.
- POST `/api/sync`: refresh telemetry and Spoolman without dispatch, `{ok:true}`.
- POST `/api/spools/{id}/reconcile`: explicit operator attestation that Spoolman's remaining weight includes all completed usage or a physical re-weigh. Requires no active reservations and connected idle assigned printers. Raises the conservative budget to observed weight; `{ok:true}`. UI must ask for this attestation, never call automatically.

Printer `progress` is a fraction from 0 to 1. Spool `reserved_g` includes conservative accounting holds as well as active job reservations. `available_g` is the maximum schedulable weight, not a second Spoolman balance.

States: queued, dispatching, running, needs_attention, completed, failed, cancelled. Printer states: standby, printing, paused, complete, cancelled, error, offline, unsupported. UI must say Demo/simulated when mode=demo, and show AI as planned/off. Demo inventory has plausible simulated printers, spools and a demo G-code file; seeded real fleet entries are disabled with empty Moonraker URLs. Demo adapter must never contact real URLs. Camera URL editable; live MJPEG `<img>` only for actual configured URLs, otherwise display an honest no-camera placeholder. No fake camera photographs.
