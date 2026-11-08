import asyncio
import json

import httpx
import pytest

from app.gateways import LiveGateway


PRINTER = {'moonraker_url': 'http://printer.test:7125', 'api_key': 'test-key'}


def test_moonraker_payloads_and_upload_separation(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.headers['X-Api-Key'] == 'test-key'
        path = request.url.path
        result = {'ok': True}
        if path == '/server/info':
            result = {'klippy_connected': True, 'klippy_state': 'ready'}
        elif path == '/printer/objects/query':
            assert 'print_stats' in json.loads(request.content)['objects']
            result = {'status': {'webhooks': {'state': 'ready'}, 'print_stats': {'state': 'standby', 'filename': ''}, 'virtual_sdcard': {'progress': 0}}}
        elif path == '/server/spoolman/status':
            result = {'spoolman_connected': True, 'spool_id': 7, 'pending_reports': []}
        elif path == '/server/spoolman/spool_id':
            assert json.loads(request.content) == {'spool_id': 7}
        elif path == '/server/files/upload':
            assert b'name="print"\r\n\r\nfalse' in request.content
            assert b'filename="pf_unique.gcode"' in request.content
            result = {'item': {'path': 'pf_unique.gcode'}, 'print_started': False, 'print_queued': False}
        elif path == '/printer/print/start':
            assert json.loads(request.content) == {'filename': 'pf_unique.gcode'}
        return httpx.Response(200, json={'result': result})

    gateway = LiveGateway('', transport=httpx.MockTransport(handler))
    path = tmp_path / 'source.gcode'
    path.write_text('G28\n')
    async def exercise():
        status = await gateway.status(PRINTER)
        assert status['state'] == 'standby'
        assert status['spool_id_actual'] == 7
        await gateway.set_spool(PRINTER, 7)
        assert await gateway.upload(PRINTER, path, 'pf_unique.gcode') == 'pf_unique.gcode'
        assert not any(r.url.path == '/printer/print/start' for r in calls)
        await gateway.start(PRINTER, 'pf_unique.gcode')
    asyncio.run(exercise())
    assert sum(r.url.path == '/printer/print/start' for r in calls) == 1


def test_missing_status_cannot_be_misclassified_idle():
    def handler(request):
        if request.url.path == '/server/info':
            return httpx.Response(200, json={'result': {'klippy_connected': True, 'klippy_state': 'ready'}})
        return httpx.Response(200, json={'result': {'status': {}}})
    gateway = LiveGateway('', transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match='missing'):
        asyncio.run(gateway.status(PRINTER))


def test_unknown_spool_weight_and_pagination_and_no_consumption_write():
    offsets = []
    def handler(request):
        assert request.method == 'GET'
        assert request.url.path == '/api/v1/spool'
        offsets.append(int(request.url.params['offset']))
        if offsets[-1] == 0:
            return httpx.Response(200, json=[{'id': i, 'filament': {'material': 'PLA'}, 'remaining_weight': None} for i in range(1,101)])
        return httpx.Response(200, json=[{'id': 101, 'filament': {'material': 'PETG', 'color_hex': 'abcdef'}, 'remaining_weight': 200}])
    gateway = LiveGateway('http://spool.test', transport=httpx.MockTransport(handler))
    result = asyncio.run(gateway.spools())
    assert offsets == [0,100]
    assert len(result) == 101 and result[0]['remaining_g'] is None


def test_upload_that_auto_starts_is_held_as_unexpected(tmp_path):
    def handler(request):
        return httpx.Response(200, json={'result': {'item': {'path': 'file.gcode'}, 'print_started': True}})
    path = tmp_path / 'file.gcode'
    path.write_text('G28\n')
    gateway = LiveGateway('', transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match='unexpectedly'):
        asyncio.run(gateway.upload(PRINTER, path, 'file.gcode'))
