from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app

HEADERS = {'Authorization': 'Bearer printforge-demo'}


def test_api_auth_upload_and_state(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, start_scheduler=False))) as client:
        assert client.get('/api/health').status_code == 200
        assert client.get('/api/state').status_code == 401
        state = client.get('/api/state', headers=HEADERS).json()
        assert state['mode'] == 'demo'
        assert len(state['printers']) == 8
        result = client.post('/api/files', headers=HEADERS, files={'file': ('cube.gcode', b'G28\n')})
        assert result.status_code == 200
        assert result.json()['size'] == 4
        assert client.post('/api/files', headers=HEADERS, files={'file': ('bad.stl', b'bad')}).status_code == 400


def test_invalid_batch_and_unknown_resource(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, start_scheduler=False))) as client:
        assert client.post('/api/jobs/batch', headers=HEADERS, json={'quantity': -1}).status_code == 422
        assert client.post('/api/printers/missing/clear-bed', headers=HEADERS).status_code == 404


def test_live_mode_requires_strong_token(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        create_app(Settings(data_dir=tmp_path, mode='live', token='printforge-demo', start_scheduler=False))


def test_live_mode_has_no_simulators_and_redacts_credentials(tmp_path):
    token = 'this-is-a-test-live-token-with-32-characters'
    with TestClient(create_app(Settings(data_dir=tmp_path, mode='live', token=token, start_scheduler=False))) as client:
        headers = {'Authorization': f'Bearer {token}'}
        response = client.get('/api/state', headers=headers)
        assert response.status_code == 200
        state = response.json()
        assert len(state['printers']) == 6
        assert not state['files']
        assert all(not p['enabled'] and 'api_key' not in p for p in state['printers'])


def test_second_process_cannot_share_same_data_directory(tmp_path):
    import pytest
    settings = Settings(data_dir=tmp_path, start_scheduler=False)
    with TestClient(create_app(settings)):
        with pytest.raises(RuntimeError, match='already runs'):
            with TestClient(create_app(settings)):
                pass


def test_shared_spool_assignment_is_rejected(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, start_scheduler=False))) as client:
        state = client.get('/api/state', headers=HEADERS).json()
        printer = state['printers'][1]
        printer['spool_id'] = 1
        response = client.put('/api/printers/' + printer['id'], headers=HEADERS, json=printer)
        assert response.status_code == 409
