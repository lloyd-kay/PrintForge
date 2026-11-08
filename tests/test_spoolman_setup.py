"""Spoolman setup exercises real files with only host/network boundaries replaced."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import zipfile

import pytest


@pytest.fixture
def module():
    assert (Path(__file__).resolve().parents[1] / "scripts/spoolman_setup.py").is_file(), "Spoolman setup is not implemented"
    return importlib.import_module("scripts.spoolman_setup")


class Host:
    def __init__(self, root):
        self.root = root
        self.commands = []
        self.user = False
        self.active = False
        self.enabled = False
        self.fail_pip = False
        self.health_ok = True

    def run(self, args, *, check=True, env=None):
        args = list(map(str, args))
        self.commands.append((args, env))
        status, output = 0, ""
        if args[:2] == ["getent", "passwd"]:
            status = 0 if self.user else 2
            if self.user:
                output = f"printforge-spoolman:x:994:994:Spoolman:{self.root / 'var/lib/printforge-spoolman'}:/usr/sbin/nologin\n"
        elif args[:2] == ["getent", "group"]:
            status = 0 if self.user else 2
            output = "printforge-spoolman:x:994:\n" if self.user else ""
        elif args[0] == "useradd":
            self.user = True
        elif args[0] == "systemctl":
            action = args[1]
            if action == "is-active":
                status = 0 if self.active else 3
            elif action == "is-enabled":
                status = 0 if self.enabled else 1
            elif action == "show":
                unit = self.root / "etc/systemd/system/printforge-spoolman.service"
                output = str(unit) if unit.exists() else ""
            elif action in ("start", "restart"):
                self.active = True
            elif action == "stop":
                self.active = False
            elif action == "enable":
                self.enabled = True
            elif action == "disable":
                self.enabled = False
            elif action != "daemon-reload":
                raise AssertionError(args)
        elif "venv" in args:
            target = Path(args[-1]) / "bin"
            target.mkdir(parents=True)
            (target / "python").write_text("python")
        elif "pip" in args:
            status = 1 if self.fail_pip else 0
        elif args[0] != "chown":
            raise AssertionError(args)
        if check and status:
            raise subprocess.CalledProcessError(status, args)
        return subprocess.CompletedProcess(args, status, stdout=output, stderr="")

    def probe(self, url):
        return "healthy" if self.active and self.health_ok else "unavailable"


@pytest.fixture
def setup(tmp_path, module, monkeypatch):
    archive = tmp_path / "spoolman.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        for name, text in {
            "pyproject.toml": 'version = "0.27.1"\n',
            "spoolman/main.py": "# backend",
            "requirements.txt": "uvicorn==0.54.0\n",
            "alembic.ini": "[alembic]\n",
            "migrations/env.py": "# migrations",
            "client_v2/build/index.html": "<html>Spoolman</html>",
            "client_v2/build/200.html": "<html>Spoolman</html>",
        }.items():
            handle.writestr(name, text)
    monkeypatch.setattr(module, "SHA256", hashlib.sha256(archive.read_bytes()).hexdigest())
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    root = tmp_path / "host"
    host = Host(root)
    downloads = []

    def download(url, destination):
        downloads.append(url)
        shutil.copyfile(archive, destination)

    def install(**kwargs):
        return module.ensure_spoolman(root=root, run=host.run, download=download, probe=host.probe, **kwargs)

    return module, host, root, downloads, install


@pytest.fixture
def symlinks(tmp_path):
    link = tmp_path / "test-link"
    try:
        link.symlink_to(tmp_path, target_is_directory=True)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Symlink contract runs in Linux CI")
        raise
    link.unlink()


@pytest.mark.parametrize("explicit", [None, "http://spoolman.local:7912/"])
def test_existing_spoolman_is_reused_without_any_host_changes(tmp_path, module, explicit):
    calls = []
    url = module.ensure_spoolman(explicit, root=tmp_path / "host", run=lambda *a, **k: pytest.fail("host mutation"),
                                 download=lambda *a: pytest.fail("download"), probe=lambda url: calls.append(url) or "healthy")
    assert url == ("http://127.0.0.1:7912" if explicit is None else "http://spoolman.local:7912")
    assert calls == [url]
    assert not (tmp_path / "host").exists()


@pytest.mark.parametrize("url", ["file:///tmp/spoolman", "http://name:7912/path?token=secret", "http://user:secret@name:7912", "http://name\n/path"])
def test_invalid_existing_urls_fail_without_requests_or_changes(tmp_path, module, url):
    with pytest.raises(module.InstallError):
        module.ensure_spoolman(url, root=tmp_path, probe=lambda _: pytest.fail("invalid URL was requested"))


@pytest.mark.parametrize("existing,status", [(None, "occupied"), ("http://remote:7912", "unavailable"), ("http://remote:7912", "occupied"),
                                           ("http://127.0.0.1:7912", "unavailable")])
def test_occupied_port_and_invalid_remote_fail_before_changes(tmp_path, module, existing, status):
    with pytest.raises(module.InstallError):
        module.ensure_spoolman(existing, root=tmp_path / "host", run=lambda *a, **k: pytest.fail("changed host"), probe=lambda _: status)
    assert not (tmp_path / "host").exists()


@pytest.mark.parametrize("path", ["opt/printforge-spoolman", "etc/printforge-spoolman", "var/lib/printforge-spoolman"])
def test_unmanaged_state_is_never_overwritten(setup, path):
    module, host, root, downloads, install = setup
    existing = root / path
    existing.mkdir(parents=True)
    (existing / "inventory.db").write_bytes(b"keep")
    with pytest.raises(module.InstallError):
        install()
    assert (existing / "inventory.db").read_bytes() == b"keep"
    assert not host.user and not downloads


def test_existing_unmanaged_account_is_not_adopted(setup):
    module, host, _, downloads, install = setup
    host.user = True
    with pytest.raises(module.InstallError):
        install()
    assert not downloads


def test_hash_failure_prevents_user_creation_and_dependency_execution(setup, monkeypatch):
    module, host, root, _, install = setup
    monkeypatch.setattr(module, "SHA256", "0" * 64)
    with pytest.raises(module.InstallError, match="[Cc]hecksum|SHA"):
        install()
    assert not host.user and not host.active
    assert not (root / "opt/printforge-spoolman/current").exists()
    assert not any("pip" in args for args, _ in host.commands)


def test_failed_dependencies_do_not_activate_service_and_allow_retry(setup, symlinks):
    module, host, root, _, install = setup
    host.fail_pip = True
    with pytest.raises((module.InstallError, subprocess.CalledProcessError)):
        install()
    assert not host.active and not host.enabled
    assert not (root / "etc/systemd/system/printforge-spoolman.service").exists()
    host.fail_pip = False
    assert install() == "http://127.0.0.1:7912"


def test_native_install_preserves_inventory_configuration_and_reuses_download(setup, symlinks):
    _, host, root, downloads, install = setup
    assert install() == "http://127.0.0.1:7912"
    release = root / "opt/printforge-spoolman/releases/v0.27.1"
    assert (root / "opt/printforge-spoolman/current").resolve() == release.resolve()
    config = root / "etc/printforge-spoolman/env"
    config.write_text(config.read_text() + "SPOOLMAN_LOGGING_LEVEL=WARNING\n")
    original = config.read_bytes()
    inventory = root / "var/lib/printforge-spoolman/spoolman.db"
    inventory.write_bytes(b"my inventory")
    assert host.active and host.enabled
    host.active = False  # Reinstall must recover a stopped managed service.
    assert install() == "http://127.0.0.1:7912"
    assert config.read_bytes() == original and inventory.read_bytes() == b"my inventory"
    assert len(downloads) == 1
    assert sum("pip" in args for args, _ in host.commands) == 1
    unit = (root / "etc/systemd/system/printforge-spoolman.service").read_text()
    assert "--host 0.0.0.0 --port 7912" in unit
    assert f"Environment=PATH={root / 'opt/printforge-spoolman/current'}/.venv/bin:" in unit
    assert f"WorkingDirectory={root / 'opt/printforge-spoolman/current'}" in unit
    pip = next(args for args, _ in host.commands if "pip" in args)
    assert "--only-binary=:all:" in pip and pip[0] == str(release / ".venv/bin/python")


def test_failed_readiness_removes_new_unit_and_keeps_durable_state(setup, symlinks):
    module, host, root, _, install = setup
    host.health_ok = False
    with pytest.raises(module.InstallError):
        install()
    assert not host.active and not host.enabled
    assert not (root / "opt/printforge-spoolman/current").exists()
    assert not (root / "etc/systemd/system/printforge-spoolman.service").exists()
    assert (root / "etc/printforge-spoolman/env").is_file()


def test_saved_local_url_recovers_stopped_managed_spoolman_without_reinstall(setup, symlinks):
    _, host, root, downloads, install = setup
    install()
    inventory = root / "var/lib/printforge-spoolman/spoolman.db"
    inventory.write_bytes(b"existing spools")
    host.active = False
    assert install(existing_url="http://127.0.0.1:7912/") == "http://127.0.0.1:7912"
    assert host.active and inventory.read_bytes() == b"existing spools"
    assert len(downloads) == 1
    assert sum("pip" in args for args, _ in host.commands) == 1


@pytest.mark.parametrize("name,mode", [("../escape", 0), ("/escape", 0), ("C:/escape", 0), ("nested/../../escape", 0), ("nested\\escape", 0), ("link", stat.S_IFLNK | 0o777)])
def test_archive_rejects_escape_paths_and_symlinks_before_extracting(tmp_path, module, name, mode):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("innocent.txt", "no partial extraction")
        entry = zipfile.ZipInfo(name)
        entry.filename = name  # Windows ZipInfo otherwise rewrites backslashes on creation.
        entry.external_attr = mode << 16
        handle.writestr(entry, "target")
    target = tmp_path / "extract"
    with pytest.raises(module.InstallError):
        module.extract_archive(archive, target)
    assert not (target / "innocent.txt").exists()
    assert not (tmp_path / "escape").exists()


def test_http_probe_requires_both_spoolman_health_and_info(module):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    responses = {"/api/v1/health": {"status": "healthy"}, "/api/v1/info": {"version": "0.27.1", "data_dir": "/data", "db_type": "sqlite"},
                 "/api/v1/spool?limit=1": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(responses.get(self.path, {})).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        assert module.probe_spoolman(url) == "healthy"
        responses["/api/v1/spool?limit=1"] = {"detail": "database unavailable"}
        assert module.probe_spoolman(url) == "occupied"
        responses["/api/v1/spool?limit=1"] = []
        responses["/api/v1/info"] = {"service": "something else"}
        assert module.probe_spoolman(url) == "occupied"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert module.probe_spoolman(url) == "unavailable"
