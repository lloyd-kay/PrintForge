"""Native installer contracts; no root access or real service changes."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "native_install.py"
SHELL_SCRIPT = SCRIPT.with_name("install.sh")


@pytest.fixture
def symlink_support(tmp_path):
    target = tmp_path / "symlink-target"
    target.mkdir()
    link = tmp_path / "symlink-check"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows account lacks symlink privilege; exercised on Linux CI")
        raise
    link.unlink()


@pytest.fixture
def installer_module():
    assert SCRIPT.is_file(), "Native installer has not been implemented"
    spec = importlib.util.spec_from_file_location("native_install", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bundle(directory):
    source = directory / "source"
    for name, content in {
        "app/main.py": "# app\n",
        "frontend/dist/index.html": "<!doctype html><html>PrintForge</html>",
        "requirements-runtime.txt": "-c requirements.lock\nuvicorn==0.34.0\n",
        "requirements.lock": "uvicorn==0.34.0\n",
        "LICENSE": "MIT\n",
        "VERSION": "v0.1.0\n",
        "scripts/native_install.py": "# installer\n",
    }.items():
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return source


class Host:
    """Only system boundaries are replaced; the installer writes real files."""

    def __init__(self, paths):
        self.paths = paths
        self.commands = []
        self.user = False
        self.active = False
        self.enabled = False
        self.fail_pip = False
        self.health_ok = True
        self.restarted = []

    def run(self, args, *, check=True, env=None):
        args = list(map(str, args))
        self.commands.append((args, env))
        status, out = 0, ""
        if args[:2] == ["getent", "passwd"]:
            status = 0 if self.user else 2
            out = f"printforge:x:995:995:PrintForge:{self.paths.data}:/usr/sbin/nologin\n" if self.user else ""
        elif args[:2] == ["getent", "group"]:
            status = 0 if self.user else 2
            out = "printforge:x:995:\n" if self.user else ""
        elif args[0] == "useradd":
            self.user = True
        elif args[0] == "systemctl":
            action = args[1]
            if action == "is-active":
                status = 0 if self.active else 3
            elif action == "is-enabled":
                status = 0 if self.enabled else 1
            elif action == "show":
                out = str(self.paths.unit) if self.paths.unit.exists() else ""
            elif action in ("start", "restart"):
                self.active = True
                self.restarted.append(args[-1])
            elif action == "stop":
                self.active = False
            elif action == "enable":
                self.enabled = True
            elif action == "disable":
                self.enabled = False
            elif action != "daemon-reload":
                raise AssertionError(f"Unexpected systemctl action: {args}")
        elif "venv" in args:
            target = Path(args[-1])
            (target / "bin").mkdir(parents=True)
            (target / "bin" / "python").write_text("fake python")
        elif "pip" in args:
            if self.fail_pip:
                status = 1
        elif args[0] not in ("chown", "apt-get"):
            raise AssertionError(f"Unexpected host command: {args}")
        result = subprocess.CompletedProcess(args, status, stdout=out, stderr="")
        if check and status:
            raise subprocess.CalledProcessError(status, args, output=out)
        return result

    def probe(self, bind, port, version):
        return self.health_ok


@pytest.fixture
def setup_host(tmp_path, installer_module):
    paths = installer_module.Paths(tmp_path / "host")
    host = Host(paths)
    installer = installer_module.Installer(paths, run=host.run, probe=host.probe, sleep=lambda _: None)
    return installer_module, installer, host, paths, bundle(tmp_path)


def install(setup, digest="a" * 64):
    _, installer, _, _, source = setup
    return installer.install(source, "v0.1.0", digest, "0.0.0.0", 8088)


def test_install_creates_live_service_without_exposing_token(setup_host, capsys, symlink_support):
    _, _, host, paths, _ = setup_host
    release = install(setup_host)
    config = paths.env.read_text()
    token = next(line.split("=", 1)[1] for line in config.splitlines() if line.startswith("PRINTFORGE_TOKEN="))
    assert len(token) >= 43
    assert "PRINTFORGE_MODE=live\n" in config
    assert paths.current.resolve() == release.resolve()
    assert (release / ".venv/bin/python").is_file()
    assert host.active and host.enabled
    assert token not in capsys.readouterr().out
    assert token not in repr(host.commands)
    assert all(name == "printforge.service" for name in host.restarted)
    pip_args = next(args for args, _ in host.commands if "pip" in args)
    assert "--only-binary=:all:" in pip_args
    assert pip_args[0] == str(release / ".venv/bin/python")
    if os.name != "nt":
        assert paths.env.stat().st_mode & 0o777 == 0o600


def test_reinstall_preserves_token_config_and_data(setup_host, symlink_support):
    _, _, _, paths, _ = setup_host
    first = install(setup_host)
    original = paths.env.read_bytes() + b"SPOOLMAN_URL=http://spoolman.local:7912\n"
    paths.env.write_bytes(original)
    (paths.data / "state.json").write_text('{"important": true}')
    second = install(setup_host, "b" * 64)
    assert paths.env.read_bytes() == original
    assert (paths.data / "state.json").read_text() == '{"important": true}'
    assert first.is_dir() and second != first
    assert install(setup_host, "b" * 64) == second


def test_failed_dependency_install_leaves_running_release_untouched(setup_host, symlink_support):
    module, _, host, paths, _ = setup_host
    previous = install(setup_host)
    unit = paths.unit.read_bytes()
    host.fail_pip = True
    host.commands.clear()
    with pytest.raises((module.InstallError, subprocess.CalledProcessError)):
        install(setup_host, "b" * 64)
    assert paths.current.resolve() == previous.resolve()
    assert paths.unit.read_bytes() == unit
    assert host.active
    assert not any(args[:2] == ["systemctl", "stop"] for args, _ in host.commands)


def test_failed_activation_restores_previous_symlink_unit_and_service(setup_host, symlink_support):
    module, _, host, paths, _ = setup_host
    previous = install(setup_host)
    unit = paths.unit.read_bytes()
    config = paths.env.read_bytes()
    host.health_ok = False
    with pytest.raises(module.InstallError, match="[Rr]oll|[Hh]ealth|[Aa]ctivat"):
        install(setup_host, "b" * 64)
    assert paths.current.resolve() == previous.resolve()
    assert paths.unit.read_bytes() == unit
    assert paths.env.read_bytes() == config
    assert host.active and host.enabled
    assert all(name == "printforge.service" for name in host.restarted)


def test_failed_first_activation_removes_broken_service(setup_host, symlink_support):
    module, _, host, paths, _ = setup_host
    host.health_ok = False
    with pytest.raises(module.InstallError):
        install(setup_host)
    assert not paths.current.exists()
    assert not paths.unit.exists()
    assert not host.active and not host.enabled


@pytest.mark.parametrize("conflict", ["base", "config", "data", "unit"])
def test_unmanaged_paths_are_never_overwritten(setup_host, conflict):
    module, _, host, paths, _ = setup_host
    path = getattr(paths, conflict)
    path.parent.mkdir(parents=True, exist_ok=True)
    if conflict == "unit":
        path.write_text("An unrelated service")
    else:
        path.mkdir()
        (path / "unrelated").write_text("keep")
    with pytest.raises(module.InstallError, match="[Uu]nmanaged|[Cc]onflict|[Rr]efus"):
        install(setup_host)
    assert not host.user
    assert not host.restarted


def test_existing_unmanaged_user_is_rejected(setup_host):
    module, _, host, _, _ = setup_host
    host.user = True
    with pytest.raises(module.InstallError, match="[Uu]ser|[Aa]ccount"):
        install(setup_host)


@pytest.mark.parametrize("member,kind", [("../escape", "file"), ("/escape", "file"), ("printforge/../../escape", "file"), ("printforge/link", "symlink"), ("printforge/hard", "hardlink"), ("printforge/device", "device"), ("printforge/..\\escape", "file")])
def test_archive_rejects_unsafe_members(tmp_path, installer_module, member, kind):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo(member)
        if kind == "symlink":
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
        elif kind == "hardlink":
            info.type = tarfile.LNKTYPE
            info.linkname = "/etc/passwd"
        elif kind == "device":
            info.type = tarfile.CHRTYPE
        else:
            info.size = 1
        tar.addfile(info, io.BytesIO(b"x") if kind == "file" else None)
    with pytest.raises((installer_module.InstallError, ValueError)):
        installer_module.extract_archive(archive, tmp_path / "extract")
    assert not (tmp_path / "escape").exists()


def test_archive_extracts_required_bundle(tmp_path, installer_module):
    source = bundle(tmp_path)
    archive = tmp_path / "ok.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname="printforge")
    extracted = installer_module.extract_archive(archive, tmp_path / "extract")
    assert (extracted / "frontend/dist/index.html").read_text().startswith("<!doctype html>")


@pytest.mark.parametrize("machine,version", [("x86_64", "12"), ("aarch64", "13")])
def test_supported_platforms(installer_module, machine, version):
    installer_module.validate_platform(f'ID=debian\nVERSION_ID="{version}"\n', machine, (3, 11))


@pytest.mark.parametrize("release,machine,python", [("ID=ubuntu\nVERSION_ID=24\n", "x86_64", (3, 12)), ("ID=debian\nVERSION_ID=11\n", "x86_64", (3, 11)), ("ID=raspbian\nVERSION_ID=12\n", "armv7l", (3, 11)), ("ID=debian\nVERSION_ID=12\n", "x86_64", (3, 10))])
def test_unsupported_platforms_fail_before_changes(installer_module, release, machine, python):
    with pytest.raises(installer_module.InstallError):
        installer_module.validate_platform(release, machine, python)


@pytest.mark.parametrize("bind", ["0.0.0.0\nExecStart=/bad", "localhost;bad", "--help", ""])
def test_bind_cannot_inject_unit_configuration(installer_module, bind):
    with pytest.raises(installer_module.InstallError):
        installer_module.validate_bind(bind)


@pytest.mark.skipif(os.name == "nt", reason="Bootstrap is a Linux shell script")
def test_shell_help_needs_no_root_or_network():
    result = subprocess.run(["bash", str(SHELL_SCRIPT), "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "--archive" in result.stdout and "--version" in result.stdout


@pytest.mark.skipif(os.name == "nt", reason="Bootstrap is a Linux shell script")
def test_shell_rejects_missing_checksum_before_installation(tmp_path):
    archive = tmp_path / "printforge-v0.1.0.tar.gz"
    archive.write_bytes(b"not a valid release")
    result = subprocess.run(["bash", str(SHELL_SCRIPT), "--archive", str(archive)], capture_output=True, text=True)
    assert result.returncode != 0
    assert "SHA256SUMS" in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="Bootstrap is a Linux shell script")
def test_shell_rejects_tampered_archive_before_installation(tmp_path):
    archive = tmp_path / "printforge-v0.1.0.tar.gz"
    archive.write_bytes(b"modified content")
    (tmp_path / "SHA256SUMS").write_text("a" * 64 + "  " + archive.name + "\n")
    result = subprocess.run(["bash", str(SHELL_SCRIPT), "--archive", str(archive)], capture_output=True, text=True)
    assert result.returncode != 0
    assert "checksum" in result.stderr.lower()


def test_bundle_version_mismatch_is_rejected_before_host_changes(setup_host):
    module, _, host, _, source = setup_host
    (source / "VERSION").write_text("v9.0.0\n")
    with pytest.raises(module.InstallError, match="VERSION"):
        install(setup_host)
    assert host.commands == []


def test_repeat_install_preserves_custom_listener_until_explicit_override(setup_host, symlink_support):
    _, installer, _, paths, source = setup_host
    installer.install(source, "v0.1.0", "a" * 64, "127.0.0.1", 18088)
    installer.install(source, "v0.1.0", "b" * 64)
    assert "--host 127.0.0.1 --port 18088" in paths.unit.read_text()
    installer.install(source, "v0.1.0", "b" * 64, port=18089)
    assert "--host 127.0.0.1 --port 18089" in paths.unit.read_text()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission contract")
def test_restrictive_invoker_umask_does_not_block_service_access(setup_host, symlink_support):
    _, _, _, paths, _ = setup_host
    old = os.umask(0o077)
    try:
        release = install(setup_host)
    finally:
        os.umask(old)
    for directory in (paths.base, paths.releases, release, release / ".venv", release / ".venv/bin"):
        assert directory.stat().st_mode & 0o777 == 0o755
    assert paths.env.stat().st_mode & 0o777 == 0o600
