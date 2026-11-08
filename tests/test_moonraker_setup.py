"""Local Moonraker integration: temporary files and simulated service boundaries."""
import importlib
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest


@pytest.fixture
def setup_module():
    path = Path(__file__).resolve().parents[1] / "scripts/moonraker_setup.py"
    assert path.is_file(), "Moonraker setup helper is not implemented"
    return importlib.import_module("scripts.moonraker_setup")


class Moonraker:
    def __init__(self, data):
        self.data = data
        self.calls = []
        self.config = {"config": {"server": {"host": "0.0.0.0", "port": 7125}, "authorization": {"enable_api_key": True}},
                       "orig": {"server": {"port": "7125"}}, "files": [{"filename": "moonraker.conf", "sections": ["server"]}]}
        self.status = {"webhooks": {"state": "ready"}, "print_stats": {"state": "standby"},
                       "virtual_sdcard": {"is_active": False}, "pause_resume": {"is_paused": False},
                       "idle_timeout": {"state": "Ready"}}
        self.statuses = []
        self.restarts = 0
        self.connected = False

    def rpc(self, socket_path, method, params=None):
        self.calls.append((method, params))
        if method == "server.info":
            return {"klippy_connected": True, "klippy_state": "ready", "failed_components": []}
        if method == "server.config":
            return self.config
        if method == "access.get_api_key":
            return "existing-secret"
        if method == "printer.objects.query":
            return {"status": self.statuses.pop(0) if self.statuses else self.status, "eventtime": 123.0}
        if method == "server.restart":
            self.restarts += 1
            self.connected = True
            self.config["orig"]["spoolman"] = {"server": "http://127.0.0.1:7912"}
            return "ok"
        if method == "server.spoolman.status":
            return {"spoolman_connected": self.connected, "spool_id": None, "pending_reports": []}
        raise AssertionError(method)

    def http(self, url, api_key, endpoint="/server/config", payload=None):
        assert url == "http://127.0.0.1:7125"
        assert api_key == "existing-secret"
        assert endpoint == "/server/config"
        assert payload is None
        return self.config


def fixture_host(tmp_path, mod):
    data = tmp_path / "printer_data"
    (data / "config").mkdir(parents=True)
    (data / "comms").mkdir()
    cfg = data / "config/moonraker.conf"
    cfg.write_bytes(b"# existing comment\n[server]\nport: 7125\n")
    host = Moonraker(data)
    instance = mod.MoonrakerInstance("moonraker", data, data / "comms/moonraker.sock", cfg,
                                    "http://127.0.0.1:7125", "existing-secret", rpc=host.rpc, http=host.http)
    return host, instance, cfg


def test_installs_once_with_byte_exact_backup_and_permissions(tmp_path, setup_module):
    host, instance, cfg = fixture_host(tmp_path, setup_module)
    cfg.chmod(0o640)
    before = cfg.read_bytes()
    original_mode = stat.S_IMODE(cfg.stat().st_mode)
    result = setup_module.configure_spoolman(instance, "http://127.0.0.1:7912", sleep=lambda _: None)
    assert result["applied"] is True and result["pending_restart"] is False
    assert result["ready"] is True
    assert Path(result["backup"]).read_bytes() == before
    assert stat.S_IMODE(cfg.stat().st_mode) == original_mode
    assert stat.S_IMODE(Path(result["backup"]).stat().st_mode) == original_mode
    assert cfg.read_bytes().startswith(before)
    assert host.restarts == 1
    second = setup_module.configure_spoolman(instance, "http://127.0.0.1:7912/", sleep=lambda _: None)
    assert second["pending_restart"] is False
    assert second["ready"] is True and second["applied"] is False
    assert cfg.read_text().count("[spoolman]") == 1
    assert host.restarts == 1


@pytest.mark.parametrize("change", [
    {"print_stats": {"state": "printing"}}, {"print_stats": {"state": "paused"}},
    {"virtual_sdcard": {"is_active": True}}, {"pause_resume": {"is_paused": True}},
    {"idle_timeout": {"state": "Printing"}}, {"idle_timeout": {}},
    {"print_stats": {"state": "surprise"}}, {"webhooks": {"state": "shutdown"}},
])
def test_active_or_unknown_state_never_edits_or_restarts(tmp_path, setup_module, change):
    host, instance, cfg = fixture_host(tmp_path, setup_module)
    before = cfg.read_bytes()
    host.status.update(change)
    result = setup_module.configure_spoolman(instance, "http://127.0.0.1:7912")
    assert result["applied"] is False and result["pending_restart"] is True
    assert cfg.read_bytes() == before and host.restarts == 0
    assert len(list(cfg.parent.iterdir())) == 1


def test_print_start_between_write_and_restart_defers_restart(tmp_path, setup_module):
    host, instance, cfg = fixture_host(tmp_path, setup_module)
    host.statuses = [host.status, {**host.status, "print_stats": {"state": "printing"}}]
    result = setup_module.configure_spoolman(instance, "http://127.0.0.1:7912")
    assert result["applied"] is True and result["pending_restart"] is True
    assert "[spoolman]" in cfg.read_text() and host.restarts == 0


def test_included_conflicting_spoolman_is_preserved(tmp_path, setup_module):
    host, instance, cfg = fixture_host(tmp_path, setup_module)
    cfg.write_text("[server]\n[include extras/*.conf]\n")
    extra = cfg.parent / "extras/spoolman.conf"
    extra.parent.mkdir()
    extra.write_text("[spoolman]\nserver: http://another-host:7912\nsync_rate: 8\n")
    host.config["orig"]["spoolman"] = {"server": "http://another-host:7912", "sync_rate": "8"}
    host.config["files"].append({"filename": "extras/spoolman.conf", "sections": ["spoolman"]})
    before = (cfg.read_bytes(), extra.read_bytes())
    assert setup_module.inspect_spoolman(instance) == "http://another-host:7912"
    result = setup_module.configure_spoolman(instance, "http://127.0.0.1:7912")
    assert not result["applied"] and "conflict" in result["reason"].lower()
    assert (cfg.read_bytes(), extra.read_bytes()) == before and host.restarts == 0


def test_new_unloaded_include_spoolman_is_detected(tmp_path, setup_module):
    host, instance, cfg = fixture_host(tmp_path, setup_module)
    cfg.write_text("[server]\n[include extra.conf]\n")
    (cfg.parent / "extra.conf").write_text("[spoolman]\nserver: http://other:7912\n")
    result = setup_module.configure_spoolman(instance, "http://127.0.0.1:7912")
    assert not result["applied"] and host.restarts == 0
    assert cfg.read_text().count("[spoolman]") == 0


def test_missing_include_refuses_edit(tmp_path, setup_module):
    host, instance, cfg = fixture_host(tmp_path, setup_module)
    cfg.write_text("[server]\n[include missing.conf]\n")
    result = setup_module.configure_spoolman(instance, "http://127.0.0.1:7912")
    assert not result["applied"] and host.restarts == 0
    assert "missing" in result["reason"].lower()


def test_explicit_data_discovery_reads_key_and_checks_protected_http(tmp_path, setup_module):
    host, instance, cfg = fixture_host(tmp_path, setup_module)
    def no_service(*args, **kwargs):
        raise AssertionError("Explicit data selection must not discover other services")
    found = setup_module.discover_instances(instance.data_path, run=no_service, rpc=host.rpc, http=host.http)
    assert len(found) == 1
    assert found[0].config_path == cfg.resolve()
    assert found[0].api_key == "existing-secret"
    assert "existing-secret" not in repr(found[0])
    assert not any(name in ("server.restart", "access.post_api_key") for name, _ in host.calls)


def test_env_arguments_take_priority_and_nondefault_config_is_verified(tmp_path, setup_module):
    host, instance, cfg = fixture_host(tmp_path, setup_module)
    custom = cfg.with_name("moonraker-custom.conf")
    cfg.rename(custom)
    env = instance.data_path / "systemd/moonraker.env"
    env.parent.mkdir()
    env.write_text(f'MOONRAKER_CONFIG_PATH="{cfg}"\nMOONRAKER_ARGS="-m moonraker -c {custom.as_posix()}"\n')
    host.config["files"][0]["filename"] = custom.name
    found = setup_module.discover_instances(instance.data_path, rpc=host.rpc, http=host.http)
    assert found[0].config_path == custom.resolve()


def test_discovery_rejects_wrong_config_source_and_failed_auth(tmp_path, setup_module):
    host, instance, cfg = fixture_host(tmp_path, setup_module)
    host.config["files"][0]["filename"] = "unknown.conf"
    with pytest.raises(setup_module.MoonrakerSetupError):
        setup_module.discover_instances(instance.data_path, rpc=host.rpc, http=host.http)
    host.config["files"][0]["filename"] = cfg.name
    def rejected(*args, **kwargs):
        raise OSError("Unauthorized")
    with pytest.raises(setup_module.MoonrakerSetupError):
        setup_module.discover_instances(instance.data_path, rpc=host.rpc, http=rejected)


def test_service_environment_discovers_nonstandard_data_directory(tmp_path, setup_module):
    host, instance, cfg = fixture_host(tmp_path, setup_module)
    env = tmp_path / "service.env"
    env.write_text(f'MOONRAKER_DATA_PATH="{instance.data_path.as_posix()}"\nMOONRAKER_ARGS="-m moonraker"\n')
    def run(args, **kwargs):
        if args[1] == "list-units":
            return SimpleNamespace(returncode=0, stdout="moonraker-test.service loaded active running API\n")
        if args[1] == "show":
            return SimpleNamespace(returncode=0, stdout=f"EnvironmentFiles={env.as_posix()} (ignore_errors=no)\nMainPID=0\nUser=pi\n")
        raise AssertionError(args)
    found = setup_module.discover_instances(run=run, roots=[], rpc=host.rpc, http=host.http)
    assert len(found) == 1 and found[0].name == "moonraker-test"


def test_unix_rpc_frames_responses_and_ignores_notifications(setup_module, monkeypatch):
    class Stream:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def settimeout(self, seconds): pass
        def connect(self, path): self.path = path
        def sendall(self, data):
            assert data.endswith(b"\x03")
            request = json.loads(data[:-1])
            assert request["method"] == "access.get_api_key"
            self.chunks = [b'{"jsonrpc":"2.0","method":"notify_status_update","params":[]}\x03',
                           json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": "secret"}).encode() + b"\x03"]
        def recv(self, count): return self.chunks.pop(0)
    monkeypatch.setattr(setup_module.socket, "AF_UNIX", 1, raising=False)
    monkeypatch.setattr(setup_module.socket, "socket", lambda *args: Stream())
    assert setup_module.unix_rpc(Path("test.sock"), "access.get_api_key") == "secret"


def test_disabled_api_key_policy_is_not_changed(tmp_path, setup_module):
    host, instance, cfg = fixture_host(tmp_path, setup_module)
    host.config["config"]["authorization"]["enable_api_key"] = False
    def trusted_http(url, key, endpoint, payload):
        assert key == ""
        return host.config
    found = setup_module.discover_instances(instance.data_path, rpc=host.rpc, http=trusted_http)
    assert found[0].api_key == ""
    assert not any(method.startswith("access.") for method, _ in host.calls)
    assert cfg.read_text() == "# existing comment\n[server]\nport: 7125\n"


def test_incomplete_effective_config_never_adds_duplicate_section(tmp_path, setup_module):
    host, instance, cfg = fixture_host(tmp_path, setup_module)
    host.config["config"]["spoolman"] = {"server": "http://somewhere:7912"}
    before = cfg.read_bytes()
    result = setup_module.configure_spoolman(instance, "http://127.0.0.1:7912")
    assert not result["applied"] and cfg.read_bytes() == before and host.restarts == 0


def test_malformed_status_is_safe_and_actionable(tmp_path, setup_module):
    host, instance, cfg = fixture_host(tmp_path, setup_module)
    before = cfg.read_bytes()
    host.status["print_stats"] = None
    result = setup_module.configure_spoolman(instance, "http://127.0.0.1:7912")
    assert not result["applied"] and result["pending_restart"] and result["reason"]
    assert cfg.read_bytes() == before and host.restarts == 0


def test_config_edit_during_final_idle_check_prevents_restart(tmp_path, setup_module):
    host, instance, cfg = fixture_host(tmp_path, setup_module)
    rpc = host.rpc
    idle_queries = 0
    def concurrent_change(socket_path, method, params=None):
        nonlocal idle_queries
        if method == "printer.objects.query":
            idle_queries += 1
            if idle_queries == 2:
                cfg.write_bytes(cfg.read_bytes() + b"\n# edited elsewhere\n")
        return rpc(socket_path, method, params)
    instance._rpc = concurrent_change
    result = setup_module.configure_spoolman(instance, "http://127.0.0.1:7912")
    assert result["applied"] and result["pending_restart"] and host.restarts == 0


def test_restart_does_not_claim_success_for_wrong_active_endpoint(tmp_path, setup_module):
    host, instance, cfg = fixture_host(tmp_path, setup_module)
    rpc = host.rpc
    def wrong_reload(socket_path, method, params=None):
        response = rpc(socket_path, method, params)
        if method == "server.restart":
            host.config["orig"]["spoolman"]["server"] = "http://other:7912"
        return response
    instance._rpc = wrong_reload
    result = setup_module.configure_spoolman(instance, "http://127.0.0.1:7912", sleep=lambda _: None)
    assert result["pending_restart"] and host.restarts == 1


def test_existing_disconnected_connection_is_pending_without_restart(tmp_path, setup_module):
    host, instance, cfg = fixture_host(tmp_path, setup_module)
    cfg.write_text("[server]\n[spoolman]\nserver: http://127.0.0.1:7912\n")
    host.config["orig"]["spoolman"] = {"server": "http://127.0.0.1:7912"}
    result = setup_module.configure_spoolman(instance, "http://127.0.0.1:7912")
    assert result["ready"] is False and result["pending_restart"] is True
    assert result["applied"] is False and host.restarts == 0


def test_wildcard_include_detects_hidden_spoolman_file(tmp_path, setup_module):
    host, instance, cfg = fixture_host(tmp_path, setup_module)
    cfg.write_text("[server]\n[include extras/*]\n")
    extras = cfg.parent / "extras"
    extras.mkdir()
    (extras / ".hidden").write_text("[spoolman]\nserver: http://other:7912\n")
    (extras / "ordinary.conf").write_text("[webcam test]\nlocation: printer\n")
    assert setup_module.inspect_spoolman(instance) == "http://other:7912"
    result = setup_module.configure_spoolman(instance, "http://127.0.0.1:7912")
    assert not result["applied"] and host.restarts == 0


def test_repeated_include_is_rejected_before_edit(tmp_path, setup_module):
    host, instance, cfg = fixture_host(tmp_path, setup_module)
    cfg.write_text("[server]\n[include extras/*.conf]\n[include extras/one.conf]\n")
    extras = cfg.parent / "extras"
    extras.mkdir()
    (extras / "one.conf").write_text("[webcam test]\nlocation: printer\n")
    result = setup_module.configure_spoolman(instance, "http://127.0.0.1:7912")
    assert not result["applied"] and host.restarts == 0


def test_disconnected_klipper_can_be_discovered_but_never_restarted(tmp_path, setup_module):
    host, instance, cfg = fixture_host(tmp_path, setup_module)
    rpc = host.rpc
    def disconnected(socket_path, method, params=None):
        if method == "server.info":
            return {"klippy_connected": False, "klippy_state": "disconnected", "failed_components": []}
        if method == "printer.objects.query":
            raise OSError("Klipper is unavailable")
        return rpc(socket_path, method, params)
    before = cfg.read_bytes()
    found = setup_module.discover_instances(instance.data_path, rpc=disconnected, http=host.http)
    assert len(found) == 1 and found[0].api_key == "existing-secret"
    result = setup_module.configure_spoolman(found[0], "http://127.0.0.1:7912")
    assert result["pending_restart"] and not result["ready"] and not result["applied"]
    assert cfg.read_bytes() == before and host.restarts == 0


def test_ipv6_loopback_is_not_assumed_to_share_ipv4_inventory(tmp_path, setup_module):
    host, instance, cfg = fixture_host(tmp_path, setup_module)
    cfg.write_text("[server]\n[spoolman]\nserver: http://127.0.0.1:7912\n")
    host.config["orig"]["spoolman"] = {"server": "http://127.0.0.1:7912"}
    before = cfg.read_bytes()
    result = setup_module.configure_spoolman(instance, "http://[::1]:7912")
    assert "conflict" in result["reason"].lower() and not result["applied"]
    assert cfg.read_bytes() == before and host.restarts == 0


@pytest.mark.parametrize("pending", [
    "[server]\nport: not-a-port\n",
    "[server]\nport: 7126\n",
    "[server]\nport: 7125\n[authorization]\nenable_api_key: False\n",
    "[DEFAULT]\nchanged: setting\n[server]\nport: 7125\n",
])
def test_unrelated_pending_configuration_defers_before_edit_or_restart(tmp_path, setup_module, pending):
    host, instance, cfg = fixture_host(tmp_path, setup_module)
    cfg.write_text(pending)
    before = cfg.read_bytes()
    result = setup_module.configure_spoolman(instance, "http://127.0.0.1:7912", sleep=lambda _: None)
    assert result["pending_restart"] and not result["applied"] and not result["ready"]
    assert "pending" in result["reason"].lower()
    assert cfg.read_bytes() == before and host.restarts == 0
    assert len(list(cfg.parent.iterdir())) == 1


def test_unrelated_pending_edit_does_not_prevent_healthy_existing_reuse(tmp_path, setup_module):
    host, instance, cfg = fixture_host(tmp_path, setup_module)
    cfg.write_text("[server]\nport: not-a-port\n[spoolman]\nserver: http://127.0.0.1:7912\n")
    host.config["orig"]["spoolman"] = {"server": "http://127.0.0.1:7912"}
    host.connected = True
    before = cfg.read_bytes()
    result = setup_module.configure_spoolman(instance, "http://127.0.0.1:7912")
    assert result["ready"] and not result["pending_restart"] and not result["applied"]
    assert cfg.read_bytes() == before and host.restarts == 0


def test_clean_include_defaults_and_comments_allow_activation(tmp_path, setup_module):
    host, instance, cfg = fixture_host(tmp_path, setup_module)
    cfg.write_text("[DEFAULT]\nshared: initial\n[server]\nport: 7125\n"
                   "note: Printer \\#one # ignored comment\n[include extras/*.conf]\n"
                   "[webcam main]\nenabled: True\n")
    extras = cfg.parent / "extras"
    extras.mkdir()
    include = extras / "auth.conf"
    include.write_text("[DEFAULT]\nshared: included\n[authorization]\nforce_logins: True\n"
                       "cors_domains:\n  http://printer.lan # ignored comment\n")
    host.config["orig"] = {
        "DEFAULT": {"shared": "included"},
        "server": {"shared": "included", "port": "7125", "note": "Printer #one"},
        "authorization": {"shared": "included", "force_logins": "True", "cors_domains": "\nhttp://printer.lan"},
        "webcam main": {"shared": "included", "enabled": "True"},
    }
    host.config["files"].append({"filename": "extras/auth.conf", "sections": ["DEFAULT", "authorization"]})
    before = include.read_bytes()
    result = setup_module.configure_spoolman(instance, "http://127.0.0.1:7912", sleep=lambda _: None)
    assert result["ready"] and result["applied"] and host.restarts == 1
    assert include.read_bytes() == before
