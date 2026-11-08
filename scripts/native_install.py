#!/usr/bin/env python3
"""Install a verified PrintForge bundle. Standard library only; Linux/systemd."""
import argparse
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request


MARKER = "PrintForge native installer v1\n"
UNIT_MARKER = "# Managed by PrintForge native installer v1\n"
SERVICE = "printforge.service"


class InstallError(RuntimeError):
    pass


class Paths:
    def __init__(self, root=Path("/")):
        self.base = root / "opt/printforge"
        self.releases = self.base / "releases"
        self.current = self.base / "current"
        self.config = root / "etc/printforge"
        self.env = self.config / "printforge.env"
        self.settings = self.config / "installer.json"
        self.data = root / "var/lib/printforge"
        self.unit = root / "etc/systemd/system/printforge.service"


def validate_platform(os_release, machine, python_version):
    fields = {}
    for line in os_release.splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            fields[key] = value.strip().strip('"').strip("'")
    if fields.get("ID") not in ("debian", "raspbian") or fields.get("VERSION_ID") not in ("12", "13"):
        raise InstallError("Supported systems: Debian 12/13 or 64-bit Raspberry Pi OS 12/13.")
    if machine not in ("x86_64", "aarch64", "amd64", "arm64"):
        raise InstallError("PrintForge requires a 64-bit amd64 or arm64 operating system.")
    if tuple(python_version[:2]) < (3, 11):
        raise InstallError("Python 3.11 or newer is required; upgrade the OS before installing.")


def validate_bind(value):
    try:
        if "%" in value:
            raise ValueError("Scoped addresses are not supported")
        return str(ipaddress.ip_address(value))
    except ValueError as exc:
        raise InstallError("--bind must be a numeric IPv4 or IPv6 address.") from exc


def validate_source(source, version=None):
    if not source.is_dir() or source.is_symlink():
        raise InstallError("The extracted bundle must be a real directory.")
    for path in source.rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise InstallError(f"Unsupported bundle entry: {path.name}")
    for name in ("app/main.py", "frontend/dist/index.html", "requirements-runtime.txt", "requirements.lock", "LICENSE", "VERSION", "scripts/native_install.py"):
        if not (source / name).is_file():
            raise InstallError(f"Release bundle is missing {name}.")
    if version and (source / "VERSION").read_text().strip() != version:
        raise InstallError("Bundle VERSION does not match the requested release.")


def extract_archive(archive, destination):
    """Extract only regular files/directories under printforge; never trust tar paths."""
    destination = Path(destination)
    if destination.exists() and (destination.is_symlink() or any(destination.iterdir())):
        raise InstallError("Extraction destination must be an empty real directory.")
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        total = 0
        seen = set()
        if len(members) > 50000:
            raise InstallError("Release contains too many archive entries.")
        for member in members:
            name = member.name.rstrip("/")
            path = PurePosixPath(name)
            if (not name or "\\" in name or path.is_absolute() or ".." in path.parts
                    or path.parts[0] != "printforge" or name != path.as_posix()
                    or not (member.isfile() or member.isdir()) or name in seen):
                raise InstallError(f"Unsafe or duplicate archive member: {member.name!r}")
            seen.add(name)
            total += member.size
            if total > 512 * 1024 * 1024:
                raise InstallError("Release exceeds the 512 MiB extraction limit.")
        # Validate all entries before creating even the first bundle file.
        for member in members:
            target = destination.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(0o755)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as reader, target.open("xb") as writer:
                    shutil.copyfileobj(reader, writer)
                target.chmod(0o644)
    source = destination / "printforge"
    validate_source(source)
    return source


def run_command(args, *, check=True, env=None):
    # Never invoke a shell, and do not propagate GitHub credentials to child tools.
    child_env = {key: value for key, value in os.environ.items() if key not in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")}
    if env:
        child_env.update(env)
    result = subprocess.run(list(map(str, args)), text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=child_env, check=False)
    if check and result.returncode:
        # Do not echo subprocess output: preserved custom settings may contain secrets.
        raise InstallError(f"{Path(str(args[0])).name} failed (exit {result.returncode}); the previous release has not been deleted.")
    return result


def probe_http(bind, port, version):
    address = "127.0.0.1" if bind == "0.0.0.0" else "::1" if bind == "::" else bind
    address = f"[{address}]" if ":" in address else address
    base = f"http://{address}:{port}"
    # Ignore proxy environment variables for this local-only readiness check.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(base + "/api/health", timeout=2) as response:
            health = json.loads(response.read(4096))
        if health != {"status": "ok", "mode": "live", "version": version.removeprefix("v")}:
            return False
        with opener.open(base + "/", timeout=2) as response:
            page = response.read(256 * 1024).lower()
            return response.status == 200 and b"<html" in page and b"<script" in page
    except (OSError, ValueError, urllib.error.URLError):
        return False


def refuse_symlinks(path):
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise InstallError(f"Refusing symlink at managed path: {candidate}")


def atomic_write(path, content, mode=0o644):
    temp = path.with_name(path.name + ".new-" + secrets.token_hex(6))
    try:
        with temp.open("xb") as handle:
            os.chmod(temp, mode)
            handle.write(content)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def switch_link(path, target):
    temp = path.with_name(path.name + ".new-" + secrets.token_hex(6))
    try:
        temp.symlink_to(target, target_is_directory=True)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


class Installer:
    def __init__(self, paths=None, *, run=run_command, probe=probe_http, sleep=time.sleep):
        self.paths = paths or Paths()
        self.run = run
        self.probe = probe
        self.sleep = sleep

    def preflight(self):
        p = self.paths
        for directory in (p.base, p.config, p.data):
            refuse_symlinks(directory)
            if directory.exists():
                marker = directory / ".printforge-managed"
                if not directory.is_dir() or not marker.is_file() or marker.is_symlink() or marker.read_text() != MARKER:
                    raise InstallError(f"Unmanaged path conflict: {directory}; refusing to overwrite it.")
                if directory.stat().st_uid != getattr(os, "getuid", lambda: 0)() and directory != p.data:
                    raise InstallError(f"Managed path must be owned by root: {directory}")
        refuse_symlinks(p.releases)
        refuse_symlinks(p.env)
        refuse_symlinks(p.settings)
        refuse_symlinks(p.unit)
        if p.unit.exists() and not p.unit.read_text().startswith(UNIT_MARKER):
            raise InstallError("Unmanaged printforge.service exists; refusing to overwrite it.")
        if p.current.is_symlink():
            target = p.current.resolve()
            if target.parent != p.releases.resolve() or not (target / ".printforge-release.json").is_file():
                raise InstallError("Unmanaged current symlink; refusing to overwrite it.")
        elif p.current.exists():
            raise InstallError("Unmanaged current path; expected an installer-owned symlink.")
        fragment = self.run(["systemctl", "show", SERVICE, "--property=FragmentPath", "--value"], check=False).stdout.strip()
        if fragment and fragment != str(p.unit):
            raise InstallError(f"Conflicting {SERVICE} loaded from another location.")
        user = self.run(["getent", "passwd", "printforge"], check=False)
        group = self.run(["getent", "group", "printforge"], check=False)
        if user.returncode == 0:
            fields = user.stdout.strip().split(":")
            if (not (p.base / ".printforge-managed").exists() or len(fields) != 7
                    or fields[2] == "0" or fields[5] != str(p.data)
                    or fields[6] not in ("/usr/sbin/nologin", "/sbin/nologin")
                    or group.returncode != 0 or group.stdout.split(":")[2] != fields[3]):
                raise InstallError("Existing printforge account is not an installer-managed service user.")
        elif group.returncode == 0:
            raise InstallError("Existing printforge group has no managed service user.")
        return user.returncode == 0

    def prepare_directories(self, user_exists):
        p = self.paths
        for directory, mode in ((p.base, 0o755), (p.config, 0o700), (p.data, 0o750)):
            directory.mkdir(parents=True, exist_ok=True)
            directory.chmod(mode)
            marker = directory / ".printforge-managed"
            if not marker.exists():
                atomic_write(marker, MARKER.encode())
        p.releases.mkdir(exist_ok=True)
        p.releases.chmod(0o755)
        p.unit.parent.mkdir(parents=True, exist_ok=True)
        if not user_exists:
            self.run(["useradd", "--system", "--user-group", "--home-dir", str(p.data),
                      "--no-create-home", "--shell", "/usr/sbin/nologin", "printforge"])
        self.run(["chown", "root:root", str(p.base), str(p.releases), str(p.config)])
        self.run(["chown", "printforge:printforge", str(p.data)])

    def prepare_release(self, source, version, digest):
        p = self.paths
        release = p.releases / f"{version}-{digest[:16]}"
        refuse_symlinks(release)
        marker = release / ".printforge-release.json"
        metadata = {"version": version, "digest": digest, "complete": False}
        if release.exists():
            if not marker.is_file() or marker.is_symlink():
                raise InstallError("Unmanaged release directory; refusing to overwrite it.")
            existing = json.loads(marker.read_text())
            if existing.get("version") != version or existing.get("digest") != digest:
                raise InstallError("Release identity conflict; refusing to overwrite it.")
            if existing.get("complete") and (release / ".venv/bin/python").is_file():
                return release
            if p.current.is_symlink() and p.current.resolve() == release.resolve():
                raise InstallError("Current release is incomplete; repair it manually before updating.")
            shutil.rmtree(release)
        release.mkdir()
        release.chmod(0o755)
        atomic_write(marker, json.dumps(metadata).encode())
        for name in ("app", "frontend", "scripts"):
            shutil.copytree(source / name, release / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".venv", "node_modules"))
        for name in ("requirements-runtime.txt", "requirements.lock", "LICENSE", "VERSION"):
            shutil.copyfile(source / name, release / name)
        for child in release.rglob("*"):
            child.chmod(0o755 if child.is_dir() else 0o644)
        # Create the venv at its final path: pip entry-point shebangs are absolute.
        venv = release / ".venv"
        result = self.run([sys.executable, "-m", "venv", str(venv)], check=False)
        if result.returncode:
            # Minimal Debian images can have Python without ensurepip/venv support.
            package_env = {"NEEDRESTART_MODE": "l", "DEBIAN_FRONTEND": "noninteractive"}
            self.run(["apt-get", "update"], env=package_env)
            self.run(["apt-get", "install", "--yes", "--no-upgrade", "--no-install-recommends", "python3-venv"], env=package_env)
            self.run([sys.executable, "-m", "venv", str(venv)])
        self.run([str(venv / "bin/python"), "-m", "pip", "install", "--disable-pip-version-check",
                  "--no-input", "--only-binary=:all:", "-r", str(release / "requirements-runtime.txt")],
                 env={"PIP_CONFIG_FILE": os.devnull, "PIP_NO_CACHE_DIR": "1"})
        self.run(["chown", "-R", "root:root", str(release)])
        metadata["complete"] = True
        atomic_write(marker, json.dumps(metadata).encode())
        return release

    def prepare_config(self):
        p = self.paths
        if not p.env.exists():
            config = (f"PRINTFORGE_MODE=live\nPRINTFORGE_DATA_DIR={p.data}\n"
                      f"PRINTFORGE_TOKEN={secrets.token_urlsafe(48)}\n"
                      "PRINTFORGE_POLL_SECONDS=5\nSPOOLMAN_URL=\n")
            atomic_write(p.env, config.encode(), 0o600)
        elif not p.env.is_file():
            raise InstallError("PrintForge environment path is not a regular file.")
        p.env.chmod(0o600)
        self.run(["chown", "root:root", str(p.env)])

    def unit_text(self, bind, port):
        p = self.paths
        return UNIT_MARKER + f"""[Unit]
Description=PrintForge print farm controller
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=printforge
Group=printforge
WorkingDirectory={p.current}
EnvironmentFile={p.env}
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart={p.current}/.venv/bin/python -m uvicorn app.main:create_app --factory --host {bind} --port {port} --workers 1
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths={p.data}
CapabilityBoundingSet=
AmbientCapabilities=
RestrictSUIDSGID=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
LockPersonality=true

[Install]
WantedBy=multi-user.target
"""

    def activate(self, release, version, bind, port):
        p = self.paths
        old_link = os.readlink(p.current) if p.current.is_symlink() else None
        old_unit = p.unit.read_bytes() if p.unit.exists() else None
        was_active = self.run(["systemctl", "is-active", "--quiet", SERVICE], check=False).returncode == 0
        was_enabled = self.run(["systemctl", "is-enabled", "--quiet", SERVICE], check=False).returncode == 0
        try:
            if was_active:
                self.run(["systemctl", "stop", SERVICE])
            switch_link(p.current, release)
            atomic_write(p.unit, self.unit_text(bind, port).encode())
            self.run(["systemctl", "daemon-reload"])
            self.run(["systemctl", "enable", SERVICE])
            self.run(["systemctl", "start", SERVICE])
            for _ in range(30):
                active = self.run(["systemctl", "is-active", "--quiet", SERVICE], check=False).returncode == 0
                if active and self.probe(bind, port, version):
                    atomic_write(p.settings, json.dumps({"bind": bind, "port": port}).encode(), 0o600)
                    return
                self.sleep(1)
            raise InstallError("Activation health checks failed.")
        except BaseException as activation_error:
            # Roll back on command failure or interruption as well as bad readiness.
            try:
                self.run(["systemctl", "stop", SERVICE], check=False)
                if not was_enabled:
                    self.run(["systemctl", "disable", SERVICE], check=False)
                if old_link is not None:
                    switch_link(p.current, old_link)
                else:
                    p.current.unlink(missing_ok=True)
                if old_unit is not None:
                    atomic_write(p.unit, old_unit)
                else:
                    p.unit.unlink(missing_ok=True)
                self.run(["systemctl", "daemon-reload"])
                if was_active and old_unit is not None:
                    self.run(["systemctl", "start", SERVICE])
            except Exception as rollback_error:
                raise InstallError("Activation failed and rollback needs attention; inspect systemctl status printforge.service.") from rollback_error
            raise InstallError("Activation failed; the previous service configuration was restored.") from activation_error

    def install(self, source, version, digest, bind=None, port=None):
        old_umask = os.umask(0o022)
        try:
            return self._install(source, version, digest, bind, port)
        finally:
            os.umask(old_umask)

    def _install(self, source, version, digest, bind, port):
        if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?", version):
            raise InstallError("Invalid release version; expected a tag such as v0.1.0.")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise InstallError("A complete SHA-256 digest is required.")
        source = Path(source)
        validate_source(source, version)
        user_exists = self.preflight()
        saved = json.loads(self.paths.settings.read_text()) if self.paths.settings.exists() else {}
        if not isinstance(saved, dict):
            raise InstallError("Installer settings must be a JSON object.")
        bind = validate_bind(bind if bind is not None else saved.get("bind", "0.0.0.0"))
        port = port if port is not None else saved.get("port", 8088)
        if not isinstance(port, int):
            raise InstallError("Saved port must be an integer.")
        if not 1024 <= port <= 65535:
            raise InstallError("--port must be between 1024 and 65535.")
        self.prepare_directories(user_exists)
        release = self.prepare_release(source, version, digest)
        self.prepare_config()
        self.activate(release, version, bind, port)
        print(f"PrintForge {version} is running on HTTP port {port} (bind {bind}).")
        print("Use only on your trusted LAN; add printers and Spoolman in the browser.")
        print(f"Administrator token is stored in {self.paths.env} (root-only).")
        print(f"Retrieve locally: sudo sed -n 's/^PRINTFORGE_TOKEN=//p' {self.paths.env}")
        return release


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--version", default="v0.1.0")
    parser.add_argument("--digest")
    parser.add_argument("--bind")
    parser.add_argument("--port", type=int)
    parser.add_argument("--extract", type=Path)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.extract:
            if not args.destination:
                raise InstallError("--extract requires --destination.")
            extract_archive(args.extract, args.destination)
            return 0
        if platform.system() != "Linux":
            raise InstallError("Native installation is supported on Linux only.")
        validate_platform(Path("/etc/os-release").read_text(), platform.machine(), sys.version_info)
        if not Path("/run/systemd/system").is_dir() or not shutil.which("systemctl"):
            raise InstallError("A running systemd host is required.")
        if os.geteuid() != 0:
            raise InstallError("Run the bootstrap as your normal user; it invokes sudo only after verification.")
        if not args.source or not args.digest:
            raise InstallError("--source and --digest are required for installation.")
        # Prevent concurrent installers from replacing each other's rollback state.
        import fcntl
        with open("/run/lock/printforge-install.lock", "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise InstallError("Another PrintForge installation is already running.") from exc
            Installer().install(args.source, args.version, args.digest, args.bind, args.port)
        return 0
    except (InstallError, OSError, ValueError, tarfile.TarError) as exc:
        print(f"PrintForge installation stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
