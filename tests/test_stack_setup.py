from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.native_install import InstallError, Paths
from scripts.setup_stack import setup_stack, read_environment


def fixture(tmp_path):
    paths = Paths(tmp_path)
    paths.config.mkdir(parents=True)
    paths.env.write_text('PRINTFORGE_TOKEN=secret-test\nSPOOLMAN_URL=\nCUSTOM=preserved\n')
    paths.settings.write_text('{"bind":"127.0.0.1","port":18088}')
    instance = SimpleNamespace(name='Printer', url='http://127.0.0.1:7125', api_key='moon-secret')
    calls = []
    deps = dict(paths=paths, discover=lambda path: [instance], inspect=lambda i: None,
        ensure=lambda url: url or 'http://127.0.0.1:7912',
        configure=lambda i, url: {'applied': True, 'pending_restart': False, 'reason': 'Connected'},
        run=lambda args: calls.append(args), probe=lambda *args: True, sleep=lambda seconds: None,
        api=lambda url, token, data: calls.append((url, token, data)))
    return paths, calls, deps


def test_stack_wires_services_without_exposing_credentials_and_repeat_preserves_env(tmp_path, capsys):
    paths, calls, deps = fixture(tmp_path)
    assert setup_stack('v0.2.0', **deps) == 0
    first = paths.env.read_bytes()
    assert read_environment(paths.env)['SPOOLMAN_URL'] == 'http://127.0.0.1:7912'
    assert read_environment(paths.env)['CUSTOM'] == 'preserved'
    assert calls[0] == ['systemctl', 'restart', 'printforge.service']
    assert calls[1][0] == 'http://127.0.0.1:18088'
    assert calls[1][2]['api_key'] == 'moon-secret'
    calls.clear()
    assert setup_stack('v0.2.0', **deps) == 0
    assert paths.env.read_bytes() == first
    assert len(calls) == 1
    output = capsys.readouterr().out
    assert 'secret-test' not in output and 'moon-secret' not in output


def test_stack_reuses_moonraker_inventory_and_reports_deferred_restart(tmp_path):
    paths, calls, deps = fixture(tmp_path)
    deps['inspect'] = lambda i: 'http://spools.lan:7912'
    deps['configure'] = lambda i, url: {'applied': False, 'pending_restart': True, 'reason': 'Printer busy'}
    assert setup_stack('v0.2.0', **deps) == 2
    assert read_environment(paths.env)['SPOOLMAN_URL'] == 'http://spools.lan:7912'
    assert len(calls) == 2  # Connection can still be registered, disabled.


def test_stack_inventory_conflict_stops_before_changes(tmp_path):
    paths, calls, deps = fixture(tmp_path)
    deps['inspect'] = lambda i: 'http://existing.lan:7912'
    before = paths.env.read_bytes()
    with pytest.raises(InstallError, match='different Spoolman'):
        setup_stack('v0.2.0', spoolman_url='http://new.lan:7912', **deps)
    assert paths.env.read_bytes() == before and not calls


def test_failed_new_environment_is_restored(tmp_path):
    paths, calls, deps = fixture(tmp_path)
    before = paths.env.read_bytes()
    deps['probe'] = lambda *args: False
    with pytest.raises(InstallError, match='restored'):
        setup_stack('v0.2.0', **deps)
    assert paths.env.read_bytes() == before
    assert calls[-1] == ['systemctl', 'restart', 'printforge.service']


def test_existing_loopback_aliases_reuse_same_inventory(tmp_path):
    paths, calls, deps = fixture(tmp_path)
    paths.env.write_text('PRINTFORGE_TOKEN=secret-test\nSPOOLMAN_URL=http://127.0.0.1:7912\n')
    deps['inspect'] = lambda i: 'http://localhost:7912/'
    assert setup_stack('v0.2.0', **deps) == 0
    assert len(calls) == 1
