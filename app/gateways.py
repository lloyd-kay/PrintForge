import hashlib
import math
import time
from pathlib import Path

import httpx


class LiveGateway:
    def __init__(self, spoolman_url: str, transport=None):
        self.spoolman_url = spoolman_url.rstrip('/')
        self.transport = transport

    async def request(self, printer, method, path, **kwargs):
        if not printer['moonraker_url']:
            raise ValueError('Set this printer’s Moonraker URL')
        headers = {'X-Api-Key': printer['api_key']} if printer.get('api_key') else {}
        async with httpx.AsyncClient(timeout=15, transport=self.transport, follow_redirects=False) as client:
            response = await client.request(method, printer['moonraker_url'].rstrip('/') + path, headers=headers, **kwargs)
            response.raise_for_status()
            body = response.json()
            if 'result' not in body:
                raise ValueError('Moonraker response has no result')
            return body['result']

    async def discover(self, printer):
        # Verify credentials before adopting a connection. Metadata is advisory:
        # travel limits are not necessarily a printable bed (e.g. delta/belt).
        config = await self.request(printer, 'GET', '/server/config')
        if not isinstance(config.get('config'), dict):
            raise ValueError('Moonraker configuration response is invalid')
        info = await self.request(printer, 'GET', '/server/info')
        result = {'volume': [0, 0, 0], 'nozzle': 0, 'camera_url': ''}
        if info.get('klippy_connected'):
            try:
                identity = await self.request(printer, 'GET', '/printer/info')
                result['name'] = str(identity.get('hostname') or 'Discovered Klipper')[:100]
                query = await self.request(printer, 'POST', '/printer/objects/query', json={'objects': {
                    'configfile': ['settings'], 'toolhead': ['axis_minimum', 'axis_maximum', 'extruder']}})
                status = query['status']
                head = status['toolhead']
                volume = [float(head['axis_maximum'][i]) - float(head['axis_minimum'][i]) for i in range(3)]
                if all(math.isfinite(n) and 0 < n <= 10000 for n in volume):
                    result['volume'] = volume
                nozzle = float(status['configfile']['settings'][head.get('extruder', 'extruder')]['nozzle_diameter'])
                if math.isfinite(nozzle) and 0 < nozzle <= 2:
                    result['nozzle'] = nozzle
            except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
                pass
        try:
            from .models import valid_http_url
            cameras = await self.request(printer, 'GET', '/server/webcams/list')
            for camera in cameras.get('webcams', []):
                try:
                    url = valid_http_url(camera.get('stream_url') or '')
                    if url:
                        result['camera_url'] = url
                        break
                except ValueError:
                    continue
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            pass
        return result

    async def status(self, printer):
        info = await self.request(printer, 'GET', '/server/info')
        if not info.get('klippy_connected') or info.get('klippy_state') != 'ready':
            raise ValueError('Klipper is not connected and ready')
        result = await self.request(printer, 'POST', '/printer/objects/query', json={'objects': {
            'webhooks': ['state'], 'print_stats': ['state', 'filename', 'message'],
            'virtual_sdcard': ['progress', 'is_active'], 'extruder': ['temperature'],
            'heater_bed': ['temperature'], 'pause_resume': ['is_paused']}})
        data = result['status']
        stats = data.get('print_stats', {})
        if not stats.get('state') or data.get('webhooks', {}).get('state') != 'ready':
            raise ValueError('Required printer status objects are missing/not ready')
        spool = await self.request(printer, 'GET', '/server/spoolman/status')
        state = stats['state']
        if data.get('pause_resume', {}).get('is_paused'):
            state = 'paused'
        elif data.get('virtual_sdcard', {}).get('is_active'):
            state = 'printing'
        return {'state': state, 'filename': stats.get('filename', ''),
                'progress': data.get('virtual_sdcard', {}).get('progress', 0),
                'hotend': data.get('extruder', {}).get('temperature', 0),
                'bed': data.get('heater_bed', {}).get('temperature', 0),
                'error': stats.get('message') or None,
                'spoolman_connected': spool.get('spoolman_connected', False),
                'spool_id_actual': spool.get('spool_id'),
                'pending_reports': bool(spool.get('pending_reports'))}

    async def spools(self):
        if not self.spoolman_url:
            raise ValueError('Set SPOOLMAN_URL to enable filament-aware scheduling')
        result = []
        offset = 0
        async with httpx.AsyncClient(timeout=15, transport=self.transport, follow_redirects=False) as client:
            while True:
                response = await client.get(self.spoolman_url + '/api/v1/spool', params={'allow_archived': 'false', 'limit': 100, 'offset': offset})
                response.raise_for_status()
                page = response.json()
                for item in page:
                    filament = item.get('filament') or {}
                    result.append({'id': item['id'], 'name': filament.get('name') or f"Spool {item['id']}",
                                   'material': filament.get('material') or '',
                                   'color': filament.get('color_hex') or '', 'remaining_g': item.get('remaining_weight'), 'reserved_g': 0})
                offset += len(page)
                if len(page) < 100:
                    return result

    async def set_spool(self, printer, spool_id):
        await self.request(printer, 'POST', '/server/spoolman/spool_id', json={'spool_id': spool_id})

    async def upload(self, printer, path: Path, filename: str):
        with path.open('rb') as file:
            checksum = hashlib.file_digest(file, 'sha256').hexdigest()
            file.seek(0)
            result = await self.request(printer, 'POST', '/server/files/upload',
                data={'root': 'gcodes', 'print': 'false', 'checksum': checksum}, files={'file': (filename, file, 'application/octet-stream')})
        if result.get('print_started') or result.get('print_queued'):
            raise ValueError('Moonraker unexpectedly started/queued upload; inspect printer')
        returned = result['item']['path']
        if returned != filename:
            raise ValueError('Moonraker returned an unexpected uploaded filename')
        return returned

    async def start(self, printer, filename):
        await self.request(printer, 'POST', '/printer/print/start', json={'filename': filename})

    async def control(self, printer, action):
        await self.request(printer, 'POST', '/printer/print/' + action)


class DemoGateway:
    """In-memory virtual devices. Never constructs a network client."""

    def __init__(self):
        self.devices = {}
        self.start_count = 0

    async def status(self, printer):
        device = self.devices.setdefault(printer['id'], {'state': 'standby', 'filename': '', 'progress': 0, 'hotend': 22,
            'bed': 21, 'error': None, 'spoolman_connected': True, 'spool_id_actual': printer['spool_id'], 'pending_reports': False})
        if device['state'] == 'printing':
            device['progress'] = min(1, (time.monotonic() - device['_started']) / 60)
            if device['progress'] >= 1:
                device['state'] = 'complete'
        return {k: v for k, v in device.items() if not k.startswith('_')}

    async def spools(self):
        return [{'id': 1, 'name': 'Demo · Signal orange', 'material': 'PLA', 'color': 'F97316', 'remaining_g': 740., 'reserved_g': 0},
                {'id': 2, 'name': 'Demo · Graphite', 'material': 'PETG', 'color': '64748B', 'remaining_g': 520., 'reserved_g': 0},
                {'id': 3, 'name': 'Demo · Arctic white', 'material': 'PLA', 'color': 'F1F5F9', 'remaining_g': 890., 'reserved_g': 0}]

    async def set_spool(self, printer, spool_id):
        await self.status(printer)
        self.devices[printer['id']]['spool_id_actual'] = spool_id

    async def upload(self, printer, path, filename):
        return filename

    async def start(self, printer, filename):
        await self.status(printer)
        self.start_count += 1
        self.devices[printer['id']].update(state='printing', filename=filename, progress=0, hotend=210, bed=60, _started=time.monotonic())

    async def control(self, printer, action):
        device = self.devices[printer['id']]
        if action == 'pause':
            device['_paused'] = time.monotonic()
            device['state'] = 'paused'
        elif action == 'resume':
            device['_started'] += time.monotonic() - device.pop('_paused', time.monotonic())
            device['state'] = 'printing'
        else:
            device['state'] = 'cancelled'
