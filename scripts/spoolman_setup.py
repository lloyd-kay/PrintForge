"""Install the pinned native Spoolman release, or verify and reuse an existing one."""
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

try:
    from .native_install import InstallError, atomic_write, refuse_symlinks, run_command, switch_link
except ImportError:  # Standalone script in an extracted release.
    from native_install import InstallError, atomic_write, refuse_symlinks, run_command, switch_link


VERSION = "v0.27.1"
DOWNLOAD_URL = "https://github.com/Donkie/Spoolman/releases/download/v0.27.1/spoolman.zip"
SHA256 = "77ba10fa505a42cef606eabec1a7c9a5b93c51dc40067456a29a7d9480ef27d6"
LOCAL_URL = "http://127.0.0.1:7912"
ACCOUNT = "printforge-spoolman"
SERVICE = "printforge-spoolman.service"
MARKER = "PrintForge managed Spoolman v1\n"
UNIT_MARKER = "# Managed by PrintForge Spoolman installer v1\n"
REQUIRED_FILES = ("pyproject.toml", "requirements.txt", "alembic.ini", "migrations/env.py",
                  "spoolman/main.py", "client_v2/build/index.html", "client_v2/build/200.html")


def normalize_url(value):
    if not isinstance(value, str) or any(ord(char) < 33 for char in value):
        raise InstallError("Spoolman URL must be an HTTP(S) address without whitespace.")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment or (port is not None and port < 1)):
            raise ValueError
    except ValueError as exc:
        raise InstallError("Spoolman URL must be an HTTP(S) base address without credentials, query or fragment.") from exc
    return value.rstrip("/")


def probe_spoolman(base_url):
    """Return healthy, unavailable (no listener), or occupied (not verified Spoolman)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    reached = False
    try:
        responses = []
        for suffix in ("/api/v1/health", "/api/v1/info", "/api/v1/spool?limit=1"):
            with opener.open(base_url + suffix, timeout=3) as response:
                reached = True
                if response.status != 200:
                    return "occupied"
                responses.append(json.loads(response.read(64 * 1024)))
        health, info, spools = responses
        if (isinstance(health, dict) and health.get("status") == "healthy" and isinstance(info, dict)
                and isinstance(info.get("version"), str) and re.fullmatch(r"\d+\.\d+\.\d+(?:[-+].*)?", info["version"])
                and isinstance(info.get("data_dir"), str) and isinstance(info.get("db_type"), str)
                and isinstance(spools, list) and len(spools) <= 1 and all(isinstance(spool, dict) for spool in spools)):
            return "healthy"
    except urllib.error.HTTPError:
        pass
    except urllib.error.URLError as exc:
        reason = exc.reason
        if not reached and (isinstance(reason, (ConnectionRefusedError, socket.gaierror))
                            or getattr(reason, "errno", None) == errno.ECONNREFUSED):
            return "unavailable"
    except (OSError, ValueError):
        pass
    return "occupied"


def download_archive(url, destination):
    """Download public release bytes without forwarding GitHub credentials."""
    request = urllib.request.Request(url, headers={"User-Agent": "PrintForge-native-installer"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, Path(destination).open("xb") as output:
            size = 0
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > 32 * 1024 * 1024:
                    raise InstallError("Spoolman download exceeds the permitted archive size.")
                output.write(chunk)
    except (OSError, urllib.error.URLError) as exc:
        raise InstallError("Could not download the pinned Spoolman release; check network access to GitHub.") from exc


def extract_archive(archive, destination):
    """Validate the whole ZIP before extracting; accept regular files and directories only."""
    destination = Path(destination)
    refuse_symlinks(destination)
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise InstallError("Spoolman extraction requires an empty real directory.")
    try:
        with zipfile.ZipFile(archive) as zipped:
            entries = zipped.infolist()
            seen, total = set(), 0
            if len(entries) > 20000:
                raise InstallError("Spoolman archive contains too many entries.")
            for entry in entries:
                name = entry.filename.rstrip("/")
                path = PurePosixPath(name)
                kind = stat.S_IFMT(entry.external_attr >> 16)
                if (not name or entry.orig_filename.rstrip("/") != name
                        or "\\" in name or ":" in name or path.is_absolute() or ".." in path.parts
                        or name != path.as_posix() or name in seen or entry.flag_bits & 1
                        or kind not in (0, stat.S_IFREG, stat.S_IFDIR)
                        or (kind == stat.S_IFDIR and not entry.is_dir())):
                    raise InstallError("Unsafe or duplicate member in Spoolman archive.")
                seen.add(name)
                total += entry.file_size
                if total > 128 * 1024 * 1024:
                    raise InstallError("Spoolman archive exceeds the extraction size limit.")
            destination.mkdir(parents=True, exist_ok=True)
            for entry in entries:
                target = destination.joinpath(*PurePosixPath(entry.filename).parts)
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    target.chmod(0o755)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zipped.open(entry) as reader, target.open("xb") as writer:
                        shutil.copyfileobj(reader, writer)
                    target.chmod(0o644)
    except zipfile.BadZipFile as exc:
        raise InstallError("Spoolman release is not a valid ZIP archive.") from exc


class Paths:
    def __init__(self, root):
        self.base = root / "opt/printforge-spoolman"
        self.releases = self.base / "releases"
        self.release = self.releases / VERSION
        self.current = self.base / "current"
        self.cache = self.base / "cache"
        self.config = root / "etc/printforge-spoolman"
        self.env = self.config / "env"
        self.data = root / "var/lib/printforge-spoolman"
        self.unit = root / "etc/systemd/system/printforge-spoolman.service"


class SpoolmanInstaller:
    def __init__(self, root, run, download, probe):
        self.p = Paths(root)
        self.run, self.download, self.probe = run, download, probe

    def preflight(self):
        p = self.p
        for directory in (p.base, p.config, p.data):
            refuse_symlinks(directory)
            if directory.exists():
                marker = directory / ".printforge-managed"
                if (not directory.is_dir() or marker.is_symlink() or not marker.is_file()
                        or marker.read_text() != MARKER):
                    raise InstallError("Unmanaged Spoolman directory conflict; existing files were left untouched.")
                if directory != p.data and directory.stat().st_uid != getattr(os, "getuid", lambda: 0)():
                    raise InstallError("Spoolman application and configuration directories must be owned by root.")
        for path in (p.releases, p.release, p.cache, p.env, p.unit, p.data / "logs", p.data / "backups"):
            refuse_symlinks(path)
        if p.env.exists() and not p.env.is_file():
            raise InstallError("Spoolman environment path is not a regular file.")
        if p.unit.exists() and (not p.unit.is_file() or not p.unit.read_text().startswith(UNIT_MARKER)):
            raise InstallError("Unmanaged Spoolman service conflict; refusing to replace it.")
        if p.current.is_symlink():
            target = p.current.resolve()
            marker = target / ".printforge-release.json"
            if target.parent != p.releases.resolve() or marker.is_symlink() or not marker.is_file():
                raise InstallError("Unmanaged Spoolman current link; refusing to replace it.")
        elif p.current.exists():
            raise InstallError("Spoolman current path must be an installer-owned symbolic link.")
        fragment = self.run(["systemctl", "show", SERVICE, "--property=FragmentPath", "--value"], check=False).stdout.strip()
        if fragment and fragment != str(p.unit):
            raise InstallError("An existing Spoolman service is loaded from a conflicting location.")
        user = self.run(["getent", "passwd", ACCOUNT], check=False)
        group = self.run(["getent", "group", ACCOUNT], check=False)
        if user.returncode == 0:
            fields, group_fields = user.stdout.strip().split(":"), group.stdout.strip().split(":")
            if (not (p.base / ".printforge-managed").is_file() or len(fields) != 7 or fields[2] == "0"
                    or fields[5] != str(p.data) or fields[6] not in ("/usr/sbin/nologin", "/sbin/nologin")
                    or group.returncode != 0 or len(group_fields) < 3 or fields[3] != group_fields[2]):
                raise InstallError("Existing Spoolman account is not managed by this installer.")
        elif group.returncode == 0:
            raise InstallError("An unmanaged Spoolman group already exists.")
        return user.returncode == 0

    def prepare_archive(self):
        p = self.p
        p.base.mkdir(parents=True, exist_ok=True)
        p.base.chmod(0o755)
        if not (p.base / ".printforge-managed").exists():
            atomic_write(p.base / ".printforge-managed", MARKER.encode())
        p.cache.mkdir(exist_ok=True)
        p.cache.chmod(0o700)
        archive = p.cache / f"spoolman-{VERSION}.zip"
        refuse_symlinks(archive)
        if not archive.exists():
            temporary = p.cache / f"download-{os.getpid()}-{time.time_ns()}.zip"
            try:
                print(f"Downloading and verifying Spoolman {VERSION}...")
                self.download(DOWNLOAD_URL, temporary)
                with temporary.open("rb") as reader:
                    if hashlib.file_digest(reader, "sha256").hexdigest() != SHA256:
                        raise InstallError("Spoolman SHA-256 checksum verification failed.")
                os.replace(temporary, archive)
            finally:
                temporary.unlink(missing_ok=True)
        with archive.open("rb") as reader:
            if hashlib.file_digest(reader, "sha256").hexdigest() != SHA256:
                raise InstallError("Cached Spoolman SHA-256 checksum verification failed.")
        return archive

    def prepare_directories(self, user_exists):
        p = self.p
        for directory, mode in ((p.config, 0o700), (p.data, 0o750)):
            directory.mkdir(parents=True, exist_ok=True)
            directory.chmod(mode)
            if not (directory / ".printforge-managed").exists():
                atomic_write(directory / ".printforge-managed", MARKER.encode())
        p.releases.mkdir(exist_ok=True)
        p.releases.chmod(0o755)
        p.unit.parent.mkdir(parents=True, exist_ok=True)
        for name in ("logs", "backups"):
            (p.data / name).mkdir(exist_ok=True)
        if not user_exists:
            self.run(["useradd", "--system", "--user-group", "--home-dir", str(p.data), "--no-create-home",
                      "--shell", "/usr/sbin/nologin", ACCOUNT])
        self.run(["chown", "root:root", str(p.base), str(p.releases), str(p.cache), str(p.config)])
        self.run(["chown", f"{ACCOUNT}:{ACCOUNT}", str(p.data), str(p.data / "logs"), str(p.data / "backups")])

    def prepare_release(self, archive):
        p = self.p
        marker = p.release / ".printforge-release.json"
        metadata = {"version": VERSION, "sha256": SHA256, "complete": False}
        if p.release.exists():
            if marker.is_symlink() or not marker.is_file():
                raise InstallError("Unmanaged Spoolman release directory; refusing to overwrite it.")
            existing = json.loads(marker.read_text())
            if existing.get("version") != VERSION or existing.get("sha256") != SHA256:
                raise InstallError("Spoolman release identity conflict.")
            if existing.get("complete") and (p.release / ".venv/bin/python").is_file():
                if all((p.release / name).is_file() for name in REQUIRED_FILES):
                    return
            if p.current.is_symlink() and p.current.resolve() == p.release.resolve():
                raise InstallError("The current Spoolman release is incomplete; repair is required.")
            # Only this exact inactive, marked release may be replaced after a failed install.
            if p.release.resolve().parent != p.releases.resolve():
                raise InstallError("Spoolman release is outside the managed release directory.")
            shutil.rmtree(p.release)
        p.release.mkdir()
        p.release.chmod(0o755)
        try:
            extract_archive(archive, p.release)
        finally:
            atomic_write(marker, json.dumps(metadata).encode())
        if not all((p.release / name).is_file() for name in REQUIRED_FILES):
            raise InstallError("Spoolman release is missing its backend, migrations or prebuilt web client.")
        if not re.search(r'^version\s*=\s*"0\.27\.1"\s*$', (p.release / "pyproject.toml").read_text(), re.MULTILINE):
            raise InstallError("Spoolman archive has an unexpected version.")
        print("Installing Spoolman's Python dependencies...")
        venv = p.release / ".venv"
        self.run([sys.executable, "-m", "venv", str(venv)])
        self.run([str(venv / "bin/python"), "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
                  "--only-binary=:all:", "-r", str(p.release / "requirements.txt")],
                 env={"PIP_CONFIG_FILE": os.devnull, "PIP_NO_CACHE_DIR": "1"})
        self.run(["chown", "-R", "root:root", str(p.release)])
        metadata["complete"] = True
        atomic_write(marker, json.dumps(metadata).encode())

    def prepare_config(self):
        p = self.p
        if not p.env.exists():
            content = (f"SPOOLMAN_DB_TYPE=sqlite\nSPOOLMAN_DIR_DATA={p.data}\n"
                       f"SPOOLMAN_DIR_LOGS={p.data}/logs\nSPOOLMAN_DIR_BACKUPS={p.data}/backups\n"
                       "SPOOLMAN_AUTOMATIC_BACKUP=TRUE\nSPOOLMAN_UPDATE_CHECK=FALSE\n")
            atomic_write(p.env, content.encode(), 0o600)
        p.env.chmod(0o600)
        self.run(["chown", "root:root", str(p.env)])

    def unit_text(self):
        p = self.p
        return UNIT_MARKER + f"""[Unit]
Description=PrintForge Spoolman filament inventory
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={ACCOUNT}
Group={ACCOUNT}
WorkingDirectory={p.current}
EnvironmentFile={p.env}
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PATH={p.current}/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart={p.current}/.venv/bin/python -m uvicorn spoolman.main:app --host 0.0.0.0 --port 7912 --workers 1
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

    def activate(self):
        p = self.p
        old_link = os.readlink(p.current) if p.current.is_symlink() else None
        old_unit = p.unit.read_bytes() if p.unit.exists() else None
        was_active = self.run(["systemctl", "is-active", "--quiet", SERVICE], check=False).returncode == 0
        was_enabled = self.run(["systemctl", "is-enabled", "--quiet", SERVICE], check=False).returncode == 0
        try:
            if was_active:
                self.run(["systemctl", "stop", SERVICE])
            switch_link(p.current, p.release)
            atomic_write(p.unit, self.unit_text().encode())
            self.run(["systemctl", "daemon-reload"])
            self.run(["systemctl", "enable", SERVICE])
            self.run(["systemctl", "start", SERVICE])
            for _ in range(30):
                if (self.run(["systemctl", "is-active", "--quiet", SERVICE], check=False).returncode == 0
                        and self.probe(LOCAL_URL) == "healthy"):
                    return
                time.sleep(1)
            raise InstallError("Spoolman did not become healthy after startup.")
        except BaseException as exc:
            try:
                self.run(["systemctl", "stop", SERVICE], check=False)
                if not was_enabled:
                    self.run(["systemctl", "disable", SERVICE], check=False)
                if old_link is None:
                    p.current.unlink(missing_ok=True)
                else:
                    switch_link(p.current, old_link)
                if old_unit is None:
                    p.unit.unlink(missing_ok=True)
                else:
                    atomic_write(p.unit, old_unit)
                self.run(["systemctl", "daemon-reload"])
                if was_active and old_unit is not None:
                    self.run(["systemctl", "start", SERVICE])
            except Exception as rollback_error:
                raise InstallError("Spoolman activation failed; inspect printforge-spoolman.service before retrying.") from rollback_error
            raise InstallError("Spoolman activation failed; previous service configuration was restored and data preserved.") from exc


def ensure_spoolman(existing_url=None, *, root=Path("/"), run=run_command, download=download_archive, probe=probe_spoolman):
    """Return a verified Spoolman base URL, installing the pinned local service if absent.

    Boundaries are injectable: run(args, check=True, env=None), download(url, Path),
    and probe(base_url) -> 'healthy' | 'unavailable' | 'occupied'.
    The caller provides Linux/root/platform checks and the installation lock.
    """
    base_url = normalize_url(existing_url) if existing_url else LOCAL_URL
    status = probe(base_url)
    if status == "healthy":
        print("Verified and reused the existing Spoolman service.")
        return base_url
    if existing_url:
        marker = Path(root) / "opt/printforge-spoolman/.printforge-managed"
        recover_owned_local = (base_url == LOCAL_URL and status == "unavailable" and not marker.is_symlink()
                               and marker.is_file() and marker.read_text() == MARKER)
        if not recover_owned_local:
            raise InstallError("The supplied Spoolman address did not pass its health and identity checks.")
    if status != "unavailable":
        raise InstallError("Port 7912 is occupied or unresponsive; refusing to replace an existing service.")
    installer = SpoolmanInstaller(Path(root), run, download, probe)
    user_exists = installer.preflight()
    old_umask = os.umask(0o022)
    try:
        archive = installer.prepare_archive()
        installer.prepare_directories(user_exists)
        installer.prepare_release(archive)
        installer.prepare_config()
        print("Starting Spoolman and checking its API...")
        installer.activate()
    finally:
        os.umask(old_umask)
    print(f"Spoolman {VERSION} is ready on port 7912.")
    return LOCAL_URL
