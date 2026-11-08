import asyncio
import copy
import hashlib
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException

from .config import Settings
from .gateways import DemoGateway, LiveGateway
from .models import BatchInput, PrinterInput
from .store import Store

ACTIVE = {'dispatching', 'running', 'needs_attention'}
IDLE = {'standby', 'complete', 'cancelled'}


def now():
    return datetime.now(timezone.utc).isoformat()


def ident():
    return uuid.uuid4().hex


def fail(status, message):
    raise HTTPException(status, message)


def normalize(value):
    return str(value or '').strip().lstrip('#').casefold()


class Engine:
    def __init__(self, settings: Settings, gateway=None):
        settings.validate()
        self.settings = settings
        self.lock = asyncio.Lock()
        self.store = Store(settings.data_dir)
        self.files_dir = settings.data_dir / 'files'
        self.files_dir.mkdir(exist_ok=True)
        self.gateway = gateway or (DemoGateway() if settings.mode == 'demo' else LiveGateway(settings.spoolman_url))
        self.state = self.store.load() or self.seed()
        self.state.setdefault('spool_budgets', {})
        if self.state['mode'] != settings.mode:
            raise ValueError('Use separate data directories/volumes for demo and live mode')
        self.state['scheduler_enabled'] = False
        for printer in self.state['printers']:
            printer['bed_clear'] = False
            printer['state'] = 'offline' if printer['kind'] == 'fdm' else 'unsupported'
        for job in self.state['jobs']:
            if job['state'] == 'dispatching':
                job.update(state='needs_attention', error='Service restarted during dispatch; inspect printer before resolving')
        self.save()

    def save(self):
        self.store.save(self.state)

    @staticmethod
    def base_printer(name, profile, material='PLA', color='', spool_id=None, enabled=False, kind='fdm', volume=None):
        return dict(id=ident(), name=name, kind=kind, profile=profile,
                    volume=volume or [0, 0, 0], nozzle=.4, material=material, color=color,
                    spool_id=spool_id, moonraker_url='', camera_url='', api_key='', enabled=enabled,
                    bed_clear=False, state='offline', progress=0, hotend=0, bed=0, filename='',
                    error=None, current_job_id=None, updated_at=None)

    def seed(self):
        printers = []
        files = []
        if self.settings.mode == 'demo':
            printers = [self.base_printer('Simulator · CR-10', 'demo-cr10', color='F97316', spool_id=1, enabled=True, volume=[300,300,400]),
                        self.base_printer('Simulator · Qidi', 'demo-qidi', material='PETG', color='64748B', spool_id=2, enabled=True, volume=[280,280,270])]
            content = b'; PRINTFORGE DEMONSTRATION ONLY - no motion commands\n'
            file_id = ident()
            (self.files_dir / (file_id + '.gcode')).write_bytes(content)
            files = [dict(id=file_id, name='demo-bracket.gcode', size=len(content), sha256=hashlib.sha256(content).hexdigest(), created_at=now(), demo_only=True)]
        printers += [self.base_printer('Qidi X-Plus 3', 'qidi-xplus3'),
                     self.base_printer('CR-10 · 01', 'cr10-verified'), self.base_printer('CR-10 · 02', 'cr10-verified'),
                     self.base_printer('CR-30', 'cr30-belt', kind='belt'), self.base_printer('Ender 3', 'ender3-verified'),
                     self.base_printer('Resin printer · model to confirm', 'resin', kind='resin')]
        return dict(schema_version=1, mode=self.settings.mode, scheduler_enabled=False, printers=printers,
                    jobs=[], files=files, spools=[], spoolman_error=None, spoolman_updated_at=None)

    def get(self, collection, identifier):
        item = next((x for x in self.state[collection] if x['id'] == identifier), None)
        if item is None:
            fail(404, f'{collection.rstrip("s").capitalize()} not found')
        return item

    def reserved(self, spool_id):
        return sum(j['grams'] for j in self.state['jobs'] if j['state'] in ACTIVE and j.get('spool_id') == spool_id)

    def available(self, spool):
        if spool['remaining_g'] is None:
            return 0
        budget = self.state['spool_budgets'].get(str(spool['id']), 0)
        return max(0, min(spool['remaining_g'] - self.reserved(spool['id']), budget))

    def accept_inventory(self, spools):
        # Moonraker clears pending_reports BEFORE its HTTP usage write finishes.
        # Keep an independent, conservative budget; automatic reads can only lower it.
        # Only an explicit operator reconciliation may increase it.
        for spool in spools:
            if spool['remaining_g'] is not None:
                key = str(spool['id'])
                previous = self.state['spool_budgets'].get(key, max(0, spool['remaining_g'] - self.reserved(spool['id'])))
                self.state['spool_budgets'][key] = max(0, min(previous, spool['remaining_g']))
        self.state.update(spools=spools, spoolman_error=None, spoolman_updated_at=now())

    def snapshot(self):
        state = copy.deepcopy(self.state)
        for printer in state['printers']:
            printer.pop('api_key', None)
        for spool in state['spools']:
            spool['available_g'] = self.available(spool)
            spool['reserved_g'] = max(0, (spool['remaining_g'] or 0) - spool['available_g'])
            spool['active_reserved_g'] = self.reserved(spool['id'])
        state.update(ai={'enabled': False, 'status': 'Local AI monitoring is planned for a later milestone'}, server_time=now())
        return state

    async def configure_printer(self, data: PrinterInput, printer_id=None):
        async with self.lock:
            if self.settings.mode == 'live' and data.enabled and data.kind == 'fdm' and not data.moonraker_url:
                fail(400, 'A Moonraker URL is required before enabling a real printer')
            if data.spool_id is not None and any(p['spool_id'] == data.spool_id and p['id'] != printer_id for p in self.state['printers']):
                fail(409, 'A physical spool can be assigned to only one printer')
            printer = self.get('printers', printer_id) if printer_id else self.base_printer(data.name, data.profile)
            if any(j['printer_id'] == printer['id'] and j['state'] in ACTIVE for j in self.state['jobs']):
                fail(409, 'Finish or resolve the reserved job before editing this printer')
            values = data.model_dump(mode='json')
            if not values['api_key'] and printer_id:
                values.pop('api_key')
            printer.update(values, bed_clear=False)
            if printer['kind'] != 'fdm':
                printer.update(enabled=False, state='unsupported')
            if not printer_id:
                self.state['printers'].append(printer)
            self.save()
            return next(p for p in self.snapshot()['printers'] if p['id'] == printer['id'])

    async def add_file(self, filename, content):
        async with self.lock:
            file_id = ident()
            path = self.files_dir / (file_id + '.gcode')
            path.write_bytes(content)
            entry = dict(id=file_id, name=filename, size=len(content), sha256=hashlib.sha256(content).hexdigest(), created_at=now())
            self.state['files'].append(entry)
            self.save()
            return copy.deepcopy(entry)

    async def create_batch(self, data: BatchInput):
        async with self.lock:
            file = self.get('files', data.file_id)
            if self.settings.mode == 'live' and file.get('demo_only'):
                fail(400, 'Demonstration G-code cannot run on real printers')
            batch_id = ident()
            jobs = []
            for _ in range(data.quantity):
                job = data.model_dump(mode='json', exclude={'quantity'})
                job.update(id=ident(), batch_id=batch_id, filename=file['name'], state='queued', printer_id=None,
                           spool_id=None, remote_filename=None, created_at=now(), started_at=None, finished_at=None, error=None)
                jobs.append(job)
            self.state['jobs'].extend(jobs)
            self.save()
            return {'jobs': jobs, 'batch_id': batch_id}

    async def _poll(self, printer):
        if printer['kind'] != 'fdm':
            printer.update(state='unsupported', bed_clear=False, error='Inventory only: this printer type is outside the MVP scheduler')
            return
        if self.settings.mode == 'live' and not printer['moonraker_url']:
            printer.update(state='offline', bed_clear=False, error='Connection details not configured')
            return
        try:
            status = await self.gateway.status(printer)
            if status['state'] not in IDLE or status['filename'] != printer['filename']:
                printer['bed_clear'] = False
            printer.update(status, updated_at=now())
        except Exception as exc:
            printer.update(state='offline', bed_clear=False, error=f'{type(exc).__name__}: {exc}', updated_at=now())

    async def _refresh(self):
        # Bounded connections: separate printers are independent; each gateway request has a timeout.
        semaphore = asyncio.Semaphore(8)
        async def poll(printer):
            async with semaphore:
                await self._poll(printer)
        await asyncio.gather(*(poll(p) for p in self.state['printers']))
        try:
            self.accept_inventory(await self.gateway.spools())
        except Exception as exc:
            self.state['spoolman_error'] = f'{type(exc).__name__}: {exc}'
        self._reconcile()
        self.save()

    def _reconcile(self):
        for printer in self.state['printers']:
            current = next((j for j in self.state['jobs'] if j['printer_id'] == printer['id'] and j['state'] in ACTIVE), None)
            printer['current_job_id'] = current['id'] if current else None
            if not current or current['state'] == 'needs_attention':
                continue
            if printer['state'] == 'offline':
                current.update(state='needs_attention', error='Printer disconnected during a job; inspect it before resolving')
                continue
            if printer['filename'] != current['remote_filename']:
                current.update(state='needs_attention', error='Printer reports another file; job outcome requires inspection')
                continue
            state = printer['state']
            if state in ('printing', 'paused'):
                current['state'] = 'running'
            elif state in ('complete', 'error', 'cancelled'):
                if printer.get('pending_reports') or not printer.get('spoolman_connected') or self.state['spoolman_error']:
                    current['error'] = 'Waiting for Spoolman consumption reports before releasing reservation'
                    continue
                current.update(state={'complete': 'completed', 'error': 'failed', 'cancelled': 'cancelled'}[state], finished_at=now(), error=printer.get('error'))
                printer.update(current_job_id=None, bed_clear=False)
            else:
                current.update(state='needs_attention', error='Print stopped without a terminal result; inspect printer')

    async def sync(self):
        async with self.lock:
            await self._refresh()

    def compatible(self, job, printer):
        if not printer['enabled'] or printer['kind'] != 'fdm' or not printer['bed_clear'] or printer['state'] not in IDLE:
            return False
        if printer['current_job_id'] or not printer.get('spoolman_connected') or printer.get('pending_reports'):
            return False
        if printer['profile'] != job['profile'] or abs(printer['nozzle'] - job['nozzle']) > .001:
            return False
        if any(a > b for a, b in zip(job['volume'], printer['volume'])):
            return False
        if normalize(printer['material']) != normalize(job['material']):
            return False
        if job['color'] and normalize(printer['color']) != normalize(job['color']):
            return False
        spool = next((s for s in self.state['spools'] if s['id'] == printer['spool_id']), None)
        if not spool or spool['remaining_g'] is None or normalize(spool['material']) != normalize(job['material']):
            return False
        if job['color'] and normalize(spool['color']) != normalize(job['color']):
            return False
        return self.available(spool) >= job['grams']

    async def tick(self):
        async with self.lock:
            await self._refresh()
            if not self.state['scheduler_enabled'] or self.state['spoolman_error']:
                return
            jobs = sorted((j for j in self.state['jobs'] if j['state'] == 'queued'), key=lambda j: (-j['priority'], j['not_before'], j['created_at']))
            for job in jobs:
                if datetime.fromisoformat(job['not_before']) > datetime.now(timezone.utc):
                    continue
                printer = next((p for p in self.state['printers'] if self.compatible(job, p)), None)
                if printer:
                    await self._dispatch(job, printer)

    async def _dispatch(self, job, printer):
        # No await between selection and durable reservation. Never retry this job automatically.
        clearance_filename = printer['filename']
        clearance_state = printer['state']
        spool = next(s for s in self.state['spools'] if s['id'] == printer['spool_id'])
        self.state['spool_budgets'][str(spool['id'])] = max(0, self.available(spool) - job['grams'])
        job.update(state='dispatching', printer_id=printer['id'], spool_id=printer['spool_id'], remote_filename=f"pf_{job['id']}.gcode")
        printer.update(current_job_id=job['id'], bed_clear=False)
        self.save()
        try:
            status = await self.gateway.status(printer)
            if status['state'] != clearance_state or status['filename'] != clearance_filename or not status.get('spoolman_connected') or status.get('pending_reports'):
                raise ValueError('Printer or filament tracking is no longer ready')
            await self.gateway.set_spool(printer, printer['spool_id'])
            await self.gateway.upload(printer, self.files_dir / (job['file_id'] + '.gcode'), job['remote_filename'])
            status = await self.gateway.status(printer)
            if status['state'] != clearance_state or status['filename'] != clearance_filename or not status.get('spoolman_connected') or status.get('pending_reports') or status.get('spool_id_actual') != printer['spool_id']:
                raise ValueError('Readiness or active spool changed before start')
            await self.gateway.start(printer, job['remote_filename'])
            job.update(state='running', started_at=now(), error=None)
        except Exception as exc:
            job.update(state='needs_attention', error=f'Dispatch outcome needs inspection. {type(exc).__name__}: {exc}')
        self.save()

    async def clear_bed(self, printer_id):
        async with self.lock:
            printer = self.get('printers', printer_id)
            await self._poll(printer)
            if printer['kind'] != 'fdm' or printer['state'] not in IDLE:
                fail(409, 'Only a connected, idle FDM printer can be marked clear')
            if any(j['printer_id'] == printer_id and j['state'] in ACTIVE for j in self.state['jobs']):
                fail(409, 'A job still reserves this printer; inspect and resolve it first')
            printer['bed_clear'] = True
            self.save()

    async def set_scheduler(self, enabled):
        async with self.lock:
            self.state['scheduler_enabled'] = enabled
            self.save()

    async def cancel_job(self, job_id):
        async with self.lock:
            job = self.get('jobs', job_id)
            if job['state'] != 'queued':
                fail(409, 'Only queued jobs can be cancelled here; use printer controls for an active print')
            job.update(state='cancelled', finished_at=now())
            self.save()

    async def resolve_job(self, job_id, outcome):
        async with self.lock:
            job = self.get('jobs', job_id)
            if job['state'] != 'needs_attention':
                fail(409, 'Only jobs needing attention can be resolved')
            printer = self.get('printers', job['printer_id'])
            await self._poll(printer)
            if printer['state'] not in IDLE | {'error'}:
                fail(409, 'Printer must be connected and idle before resolving an uncertain job')
            if printer.get('pending_reports') or not printer.get('spoolman_connected'):
                fail(409, 'Wait for Moonraker to finish reporting filament consumption')
            try:
                self.accept_inventory(await self.gateway.spools())
            except Exception:
                fail(409, 'Refresh Spoolman successfully before releasing this reservation')
            job.update(state=outcome, finished_at=now(), error='Outcome confirmed by operator')
            printer.update(current_job_id=None, bed_clear=False)
            self.save()

    async def reconcile_spool(self, spool_id):
        """Operator certifies Spoolman now reflects final usage or a physical refill."""
        async with self.lock:
            if any(j.get('spool_id') == spool_id and j['state'] in ACTIVE for j in self.state['jobs']):
                fail(409, 'Finish or resolve active jobs before reconciling inventory')
            for printer in self.state['printers']:
                if printer['spool_id'] == spool_id:
                    await self._poll(printer)
                    if printer['state'] not in IDLE or printer.get('pending_reports') or not printer.get('spoolman_connected'):
                        fail(409, 'Assigned printer must be idle with filament reports settled')
            try:
                self.accept_inventory(await self.gateway.spools())
            except Exception:
                fail(409, 'Spoolman must be reachable to reconcile inventory')
            spool = self.get('spools', spool_id)
            if spool['remaining_g'] is None:
                fail(409, 'Spoolman must have a known remaining weight')
            self.state['spool_budgets'][str(spool_id)] = max(0, spool['remaining_g'])
            self.save()

    async def control(self, printer_id, action):
        async with self.lock:
            printer = self.get('printers', printer_id)
            await self._poll(printer)
            allowed = {'pause': {'printing'}, 'resume': {'paused'}, 'cancel': {'printing', 'paused'}}
            if printer['state'] not in allowed[action]:
                fail(409, f'Cannot {action} a printer in state {printer["state"]}')
            printer['bed_clear'] = False
            self.save()
            try:
                await self.gateway.control(printer, action)
            except Exception as exc:
                # No automatic retries of physical commands.
                for job in self.state['jobs']:
                    if job['printer_id'] == printer_id and job['state'] in ACTIVE:
                        job.update(state='needs_attention', error=f'{action} result uncertain; inspect printer')
                self.save()
                fail(502, f'Command outcome uncertain; inspect printer. {type(exc).__name__}')
            await self._refresh()
