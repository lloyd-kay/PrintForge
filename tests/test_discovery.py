import asyncio

import httpx
import pytest
from fastapi import HTTPException

from app.config import Settings
from app.engine import Engine
from app.gateways import LiveGateway
from app.models import DiscoveryInput


def gateway():
    def handle(request):
        assert request.headers['X-Api-Key'] == 'local-secret'
        values = {
            '/server/config': {'config': {'server': {}}},
            '/server/info': {'klippy_connected': True, 'klippy_state': 'ready'},
            '/printer/info': {'hostname': 'my-klipper'},
            '/printer/objects/query': {'status': {'configfile': {'settings': {'extruder': {'nozzle_diameter': .6}}},
                'toolhead': {'extruder': 'extruder', 'axis_minimum': [0, 0, 0, 0], 'axis_maximum': [300, 300, 400, 0]}}},
            '/server/webcams/list': {'webcams': [{'stream_url': '/webcam/?action=stream'}, {'stream_url': 'http://camera.local/stream'}]},
        }
        return httpx.Response(200, json={'result': values[request.url.path]})
    return LiveGateway('', transport=httpx.MockTransport(handle))


def test_discovery_is_disabled_redacted_and_idempotent(tmp_path):
    engine = Engine(Settings(data_dir=tmp_path, mode='live', token='a' * 40), gateway=gateway())
    data = DiscoveryInput(moonraker_url='http://localhost:7125/', api_key='local-secret')
    printer = asyncio.run(engine.discover_printer(data))
    assert printer['name'] == 'my-klipper'
    assert printer['nozzle'] == .6
    assert printer['volume'] == [300, 300, 400]
    assert printer['camera_url'] == 'http://camera.local/stream'
    assert not printer['enabled'] and not printer['bed_clear'] and printer['spool_id'] is None
    assert 'api_key' not in printer
    assert not engine.state['scheduler_enabled']
    internal = engine.get('printers', printer['id'])
    internal.update(profile='operator-profile', nozzle=.4, enabled=True, spool_id=7)
    again = asyncio.run(engine.discover_printer(data))
    assert again['id'] == printer['id']
    assert again['profile'] == 'operator-profile' and again['nozzle'] == .4 and again['spool_id'] == 7
    assert len(engine.state['printers']) == 7


def test_discovery_preserves_connection_even_when_klipper_is_disconnected(tmp_path):
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={'result': {'config': {}, 'klippy_connected': False}}))
    engine = Engine(Settings(data_dir=tmp_path, mode='live', token='a' * 40), gateway=LiveGateway('', transport))
    printer = asyncio.run(engine.discover_printer(DiscoveryInput(moonraker_url='http://localhost:7125')))
    assert printer['volume'] == [0, 0, 0] and printer['nozzle'] == 0
    assert not printer['enabled']


def test_public_info_does_not_validate_credentials_or_replace_existing_key(tmp_path):
    engine = Engine(Settings(data_dir=tmp_path, mode='live', token='a' * 40), gateway=gateway())
    data = DiscoveryInput(moonraker_url='http://localhost:7125', api_key='local-secret')
    printer = asyncio.run(engine.discover_printer(data))
    def handle(request):
        if request.url.path == '/server/info':
            return httpx.Response(200, json={'result': {'klippy_connected': True}})
        return httpx.Response(401)
    engine.gateway = LiveGateway('', httpx.MockTransport(handle))
    data.api_key = 'wrong-secret'
    with pytest.raises(HTTPException):
        asyncio.run(engine.discover_printer(data))
    assert engine.get('printers', printer['id'])['api_key'] == 'local-secret'


def test_demo_discovery_never_connects(tmp_path):
    engine = Engine(Settings(data_dir=tmp_path))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(engine.discover_printer(DiscoveryInput(moonraker_url='http://localhost:7125')))
    assert exc.value.status_code == 400
