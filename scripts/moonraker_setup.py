"""Discover local Moonraker installations and connect Spoolman without auth changes.

Only standard-library dependencies. Connections and command execution are injectable;
the default implementation operates on local Unix sockets and verified HTTP APIs.
"""
import configparser
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import socket
import stat
import time
import urllib.parse
import urllib.request

try:
    from .native_install import InstallError, run_command
except ImportError:
    from native_install import InstallError, run_command


class MoonrakerSetupError(InstallError):
    pass


def unix_rpc(socket_path, method, params=None):
    """Send one ETX-framed JSON-RPC request, ignoring unrelated notifications."""
    if not hasattr(socket, "AF_UNIX"):
        raise MoonrakerSetupError("Local Moonraker setup requires Unix socket support.")
    request_id = secrets.randbits(48)
    request = {"jsonrpc": "2.0", "method": method, "id": request_id}
    if params is not None:
        request["params"] = params
    deadline = time.monotonic() + 5
    buffer = b""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(str(socket_path))
        connection.sendall(json.dumps(request).encode() + b"\x03")
        while time.monotonic() < deadline:
            connection.settimeout(max(0.01, deadline - time.monotonic()))
            chunk = connection.recv(65536)
            if not chunk:
                raise MoonrakerSetupError("Moonraker closed its local connection.")
            buffer += chunk
            if len(buffer) > 4 * 1024 * 1024:
                raise MoonrakerSetupError("Moonraker returned an oversized response.")
            while b"\x03" in buffer:
                frame, buffer = buffer.split(b"\x03", 1)
                response = json.loads(frame)
                if not isinstance(response, dict) or response.get("id") != request_id:
                    continue
                if "error" in response or "result" not in response:
                    # Do not expose arbitrary server errors, which may contain secrets.
                    raise MoonrakerSetupError(f"Moonraker could not complete {method}.")
                return response["result"]
    raise MoonrakerSetupError("Moonraker local request timed out.")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_query(url, api_key, endpoint="/server/config", payload=None):
    """Read a protected API without proxies, redirects, or relaxed TLS checks."""
    headers = {"Accept": "application/json"}
    if api_key:
        headers["X-Api-Key"] = api_key
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url.rstrip("/") + endpoint, data=data, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open(request, timeout=5) as response:
        content = response.read(4 * 1024 * 1024 + 1)
        if len(content) > 4 * 1024 * 1024:
            raise MoonrakerSetupError("Moonraker returned an oversized HTTP response.")
        value = json.loads(content)
    if not isinstance(value, dict) or "result" not in value or "error" in value:
        raise MoonrakerSetupError("Moonraker did not return a valid authenticated response.")
    return value["result"]


class MoonrakerInstance:
    def __init__(self, name, data_path, socket_path, config_path, url, api_key,
                 *, rpc=unix_rpc, http=http_query):
        self.name = name
        self.data_path = Path(data_path)
        self.socket_path = Path(socket_path)
        self.config_path = Path(config_path)
        self.url = url
        self.api_key = api_key
        self._rpc = rpc
        self._http = http

    def __repr__(self):
        return f"MoonrakerInstance(name={self.name!r}, data_path={str(self.data_path)!r})"

    def rpc(self, method, params=None):
        return self._rpc(self.socket_path, method, params)


def _env_file(path):
    values = {}
    if not path.is_file():
        raise MoonrakerSetupError(f"Moonraker environment file is missing: {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if "=" not in line:
            raise MoonrakerSetupError("Unsupported Moonraker environment file; use an explicit data path.")
        key, value = line.split("=", 1)
        if key.strip() not in {"MOONRAKER_DATA_PATH", "MOONRAKER_CONFIG_PATH", "MOONRAKER_UDS_PATH", "MOONRAKER_ARGS", "HOME"}:
            continue
        # Parse literal values, never source user-owned files as the installer user.
        words = shlex.split(value, posix=True)
        values[key.strip()] = " ".join(words)
    return values


def _path(value, home=None):
    if value.startswith("~/"):
        if home is None:
            raise MoonrakerSetupError("Cannot determine Moonraker's service-user home directory.")
        value = str(Path(home) / value[2:])
    result = Path(value)
    if not result.is_absolute():
        raise MoonrakerSetupError("Moonraker paths must be absolute for automatic setup.")
    return result.resolve()


def _options(env, argv=None):
    result = {"data": env.get("MOONRAKER_DATA_PATH"), "config": env.get("MOONRAKER_CONFIG_PATH"),
              "socket": env.get("MOONRAKER_UDS_PATH")}
    args = argv if argv is not None else shlex.split(env.get("MOONRAKER_ARGS", ""))
    aliases = {"-d": "data", "--datapath": "data", "--data-path": "data",
               "-c": "config", "--configfile": "config", "-u": "socket", "--unixsocket": "socket"}
    index = 0
    while index < len(args):
        arg = args[index]
        key, separator, value = arg.partition("=")
        if key in aliases:
            if not separator:
                index += 1
                if index >= len(args):
                    raise MoonrakerSetupError("Incomplete Moonraker service arguments.")
                value = args[index]
            result[aliases[key]] = value
        elif arg[:2] in ("-d", "-c", "-u") and len(arg) > 2 and not arg.startswith("--"):
            result[aliases[arg[:2]]] = arg[2:]
        index += 1
    return result


def _service_candidates(run):
    try:
        listed = run(["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--no-pager"], check=False)
    except OSError:
        return []
    if listed.returncode:
        return []
    candidates = []
    for line in listed.stdout.splitlines():
        fields = line.lstrip("● ").split()
        if not fields or not re.fullmatch(r"moonraker[\w@.-]*\.service", fields[0]):
            continue
        unit = fields[0]
        detail = run(["systemctl", "show", unit, "--property=EnvironmentFiles,User,MainPID,ExecStart", "--no-pager"], check=False)
        if detail.returncode:
            continue
        props = dict(line.split("=", 1) for line in detail.stdout.splitlines() if "=" in line)
        env, argv = {}, None
        for env_name in shlex.split(props.get("EnvironmentFiles", "")):
            if env_name.startswith("("):
                continue
            env.update(_env_file(Path(env_name)))
        pid = props.get("MainPID", "0")
        if pid.isdigit() and int(pid) > 0:
            try:
                raw_env = (Path("/proc") / pid / "environ").read_bytes()
                for pair in raw_env.split(b"\0"):
                    key, sep, value = pair.partition(b"=")
                    if sep and key.decode(errors="replace") in ("MOONRAKER_DATA_PATH", "MOONRAKER_CONFIG_PATH", "MOONRAKER_UDS_PATH", "HOME"):
                        env[key.decode()] = value.decode()
                argv = [part.decode() for part in (Path("/proc") / pid / "cmdline").read_bytes().split(b"\0") if part]
            except OSError:
                pass
        if argv is None:
            match = re.search(r"argv\[\]=(.+?)(?:\s;|\s\})", props.get("ExecStart", ""))
            if match:
                command = shlex.split(match.group(1))
                argv = []
                for arg in command:
                    argv.extend(shlex.split(env.get("MOONRAKER_ARGS", "")) if arg in ("$MOONRAKER_ARGS", "${MOONRAKER_ARGS}") else [arg])
        options = _options(env, argv)
        if not options["data"]:
            continue
        home = env.get("HOME")
        if any(value and value.startswith("~/") for value in options.values()) and home is None:
            user = props.get("User", "")
            lookup = run(["getent", "passwd", user], check=False)
            parts = lookup.stdout.strip().split(":")
            if lookup.returncode == 0 and len(parts) == 7:
                home = parts[5]
        data = _path(options["data"], home)
        candidates.append((unit.removesuffix(".service"), data, options, home))
    return candidates


def _explicit_candidate(data_path):
    data = Path(data_path).expanduser().resolve()
    env_file = data / "systemd/moonraker.env"
    env = _env_file(env_file) if env_file.exists() else {}
    options = _options(env)
    # The explicit selection chooses the data directory, not another env-file instance.
    options["data"] = str(data)
    return (data.name, data, options, env.get("HOME"))


def _api_files(config, main):
    files = config.get("files")
    if not isinstance(files, list) or not files or not isinstance(files[0], dict):
        raise MoonrakerSetupError("Moonraker did not identify its configuration files.")
    paths = []
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("filename"), str):
            raise MoonrakerSetupError("Moonraker returned unknown configuration sources.")
        path = Path(item["filename"])
        paths.append((path if path.is_absolute() else main.parent / path).resolve())
    if paths[0] != main.resolve():
        raise MoonrakerSetupError("Moonraker's active configuration does not match the discovered file; supply its data path.")
    return paths


def _probe(candidate, rpc, http):
    name, data, options, home = candidate
    config_path = _path(options["config"], home) if options.get("config") else (data / "config/moonraker.conf").resolve()
    socket_path = _path(options["socket"], home) if options.get("socket") else data / "comms/moonraker.sock"
    if not config_path.is_file():
        raise MoonrakerSetupError("The discovered Moonraker configuration file is missing.")
    info = rpc(socket_path, "server.info")
    config = rpc(socket_path, "server.config")
    if not isinstance(info, dict) or not isinstance(config, dict):
        raise MoonrakerSetupError("The local socket is not a usable Moonraker API.")
    _api_files(config, config_path)
    server = config.get("config", {}).get("server", {})
    host = server.get("host", "0.0.0.0")
    host = "127.0.0.1" if host == "0.0.0.0" else "::1" if host == "::" else host
    if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]+", host):
        raise MoonrakerSetupError("Moonraker has an unsupported HTTP bind address.")
    port = server.get("port", 7125)
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise MoonrakerSetupError("Moonraker has an invalid HTTP port.")
    prefix = server.get("route_prefix", "") or ""
    if not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z0-9_/-]*", prefix):
        raise MoonrakerSetupError("Moonraker has an unsupported route prefix.")
    url = f"http://{'[' + host + ']' if ':' in host else host}:{port}"
    if prefix.strip("/"):
        url += "/" + prefix.strip("/")
    enabled = config.get("config", {}).get("authorization", {}).get("enable_api_key", True)
    key = rpc(socket_path, "access.get_api_key") if enabled is True else ""
    if not isinstance(key, str) or "\n" in key or "\r" in key or (enabled is True and not key):
        raise MoonrakerSetupError("Moonraker did not provide a usable existing API key.")
    # This protected endpoint remains available when Klipper is disconnected.
    check = http(url, key, "/server/config", None)
    if not isinstance(check, dict) or not isinstance(check.get("config"), dict):
        raise MoonrakerSetupError("Moonraker's protected HTTP configuration query did not succeed.")
    _api_files(check, config_path)
    return MoonrakerInstance(name, data, socket_path, config_path, url, key, rpc=rpc, http=http)


def discover_instances(data_path=None, *, run=run_command, roots=None, rpc=unix_rpc, http=http_query):
    """Discover known local installations; an explicit data path excludes all others.

    ``roots`` are home-directory roots, e.g. /home and /root. Passing [] disables
    conventional scanning. Invalid automatic candidates are skipped if another
    candidate verifies; if none verify, a sanitized actionable error is raised.
    """
    if data_path is not None:
        try:
            return [_probe(_explicit_candidate(data_path), rpc, http)]
        except MoonrakerSetupError:
            raise
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise MoonrakerSetupError("Could not verify this Moonraker instance and its existing HTTP credentials.") from exc
    candidates = _service_candidates(run)
    for root in (list(map(Path, roots)) if roots is not None else [Path("/home"), Path("/root")]):
        for pattern in ("*/printer_data*/comms/moonraker.sock", "printer_data*/comms/moonraker.sock"):
            for sock in sorted(root.glob(pattern)):
                candidates.append(_explicit_candidate(sock.parent.parent))
    found, seen, failures = [], set(), 0
    for candidate in candidates:
        if candidate[1] in seen:
            continue
        seen.add(candidate[1])
        try:
            found.append(_probe(candidate, rpc, http))
        except (OSError, ValueError, TypeError, KeyError, MoonrakerSetupError):
            failures += 1
    if not found and failures:
        raise MoonrakerSetupError("Local Moonraker was found but could not be verified. Check it is running, or use --moonraker-data with its data directory.")
    return found


def _url(value):
    if not isinstance(value, str) or any(char.isspace() for char in value):
        raise MoonrakerSetupError("Spoolman server must be an HTTP(S) URL without whitespace.")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise MoonrakerSetupError("Spoolman server must be a plain HTTP(S) URL without credentials or query parameters.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise MoonrakerSetupError("Spoolman server has an invalid port.") from exc
    host = parsed.hostname.lower()
    if host in ("localhost", "127.0.0.1"):
        host = "127.0.0.1"
    host = f"[{host}]" if ":" in host else host
    authority = host + (f":{port}" if port and port != (443 if parsed.scheme == "https" else 80) else "")
    return urllib.parse.urlunsplit((parsed.scheme.lower(), authority, parsed.path.rstrip("/"), "", ""))


def _read_tree(main):
    """Resolve includes in parse order; keep exact source bytes for safe updates."""
    snapshots, merged, visited = {}, {}, set()
    def visit(path, stack):
        path = path.resolve()
        if path in stack:
            raise MoonrakerSetupError("Moonraker configuration has an include cycle.")
        if len(snapshots) >= 128:
            raise MoonrakerSetupError("Moonraker has too many included files for automatic setup.")
        if not path.is_file():
            raise MoonrakerSetupError(f"Moonraker include file is missing: {path.name}")
        before_stat = path.stat()
        identity = (before_stat.st_dev, before_stat.st_ino)
        if identity in visited:
            raise MoonrakerSetupError("Moonraker configuration contains a repeated or recursive include.")
        visited.add(identity)
        raw = path.read_bytes()
        if len(raw) > 4 * 1024 * 1024:
            raise MoonrakerSetupError("Moonraker configuration is too large for automatic setup.")
        parser = configparser.ConfigParser(interpolation=None, strict=True, inline_comment_prefixes=("#", ";"))
        try:
            parser.read_string(raw.decode("utf-8-sig"))
        except (ValueError, configparser.Error) as exc:
            raise MoonrakerSetupError(f"Cannot safely parse Moonraker configuration: {path.name}") from exc
        snapshots[path] = (raw, before_stat)
        for section in parser.sections():
            if section.startswith("include "):
                pattern = section[8:].strip()
                include = Path(pattern)
                matches = sorted(include.parent.glob(include.name) if include.is_absolute() else path.parent.glob(pattern))
                if not matches:
                    raise MoonrakerSetupError(f"Moonraker include is missing or unmatched: {pattern}")
                for match in matches:
                    visit(Path(match), stack | {path})
            else:
                merged.setdefault(section, {}).update(dict(parser[section]))
    visit(main, set())
    return merged, snapshots


def _configuration(instance):
    active = instance.rpc("server.config")
    if not isinstance(active, dict):
        raise MoonrakerSetupError("Moonraker did not return its configuration.")
    api_files = _api_files(active, instance.config_path)
    disk, snapshots = _read_tree(instance.config_path)
    if any(path not in snapshots for path in api_files):
        raise MoonrakerSetupError("Moonraker's loaded include files differ from the current files; review them before setup.")
    if not isinstance(active.get("orig"), dict) or not isinstance(active.get("config"), dict):
        raise MoonrakerSetupError("Moonraker's effective configuration is incomplete; automatic changes are disabled.")
    loaded = active["orig"].get("spoolman")
    typed = active["config"].get("spoolman")
    if typed is not None and loaded is None:
        raise MoonrakerSetupError("Spoolman is loaded but its original configuration is unknown; preserving all files.")
    configured = disk.get("spoolman")
    for section in (loaded, configured):
        if section is not None and (not isinstance(section, dict) or not section.get("server")):
            raise MoonrakerSetupError("Existing [spoolman] section has no clear server; review its configuration.")
    if loaded and configured and _url(loaded["server"]) != _url(configured["server"]):
        raise MoonrakerSetupError("Spoolman conflict: loaded and on-disk servers differ; preserve and review the existing configuration.")
    if loaded and not configured:
        raise MoonrakerSetupError("Loaded Spoolman configuration was removed on disk; review the pending change.")
    return loaded, configured, snapshots


def inspect_spoolman(instance):
    loaded, configured, _ = _configuration(instance)
    section = configured or loaded
    return section["server"].strip() if section else None


def _require_idle(instance):
    info = instance.rpc("server.info")
    if not isinstance(info, dict) or info.get("klippy_connected") is not True or info.get("klippy_state") != "ready":
        raise MoonrakerSetupError("Activation pending: Klipper is not connected and ready; retry when idle.")
    result = instance.rpc("printer.objects.query", {"objects": {
        "webhooks": ["state"], "print_stats": ["state"], "virtual_sdcard": ["is_active"],
        "pause_resume": ["is_paused"], "idle_timeout": ["state"]}})
    status = result.get("status", {}) if isinstance(result, dict) else {}
    required = ("webhooks", "print_stats", "virtual_sdcard", "pause_resume", "idle_timeout")
    if not isinstance(status, dict) or any(not isinstance(status.get(name), dict) for name in required):
        raise MoonrakerSetupError("Activation pending: printer status is incomplete or unknown. Retry when the printer is idle.")
    if (status.get("webhooks", {}).get("state") != "ready"
            or status.get("print_stats", {}).get("state") not in {"standby", "complete", "cancelled", "error"}
            or status.get("virtual_sdcard", {}).get("is_active") is not False
            or status.get("pause_resume", {}).get("is_paused") is not False
            or status.get("idle_timeout", {}).get("state") not in {"Idle", "Ready"}):
        raise MoonrakerSetupError("Activation pending: printer is active, paused, or its idle state is unknown. Retry after the printer is idle.")


def _unchanged(snapshots):
    for path, (raw, previous) in snapshots.items():
        current = path.stat()
        if (current.st_ino, current.st_dev, current.st_mtime_ns, current.st_size) != (previous.st_ino, previous.st_dev, previous.st_mtime_ns, previous.st_size) or path.read_bytes() != raw:
            raise MoonrakerSetupError("Moonraker configuration changed during setup; retry after reviewing the change.")


def _write_owned(path, raw, previous, *, replace=False):
    target = path.with_name(path.name + ".new-" + secrets.token_hex(6)) if replace else path
    try:
        with target.open("xb") as writer:
            if hasattr(os, "fchown"):
                os.fchown(writer.fileno(), previous.st_uid, previous.st_gid)
            os.chmod(target, stat.S_IMODE(previous.st_mode))
            writer.write(raw)
            writer.flush()
            os.fsync(writer.fileno())
        if replace:
            os.replace(target, path)
    finally:
        if replace:
            target.unlink(missing_ok=True)


def configure_spoolman(instance, url, *, sleep=time.sleep):
    """Return applied/pending_restart/reason; never restart Klipper or alter auth.

    A second idle check narrows the check/restart race. Moonraker offers no atomic
    restart-if-idle API, so any unavailable/uncertain status prevents activation.
    No background or blind retries are installed.
    """
    result = {"applied": False, "ready": False, "pending_restart": False, "reason": ""}
    try:
        desired = _url(url)
        loaded, configured, snapshots = _configuration(instance)
        existing = configured or loaded
        if existing and _url(existing["server"]) != desired:
            result["reason"] = "Spoolman conflict: preserving the existing server. Use that server or review the configuration manually."
            return result
        if loaded:
            connected = instance.rpc("server.spoolman.status")
            result["ready"] = isinstance(connected, dict) and connected.get("spoolman_connected") is True
            result["pending_restart"] = not result["ready"]
            result["reason"] = ("Existing Spoolman connection preserved and verified." if result["ready"]
                                else "Existing Spoolman configuration preserved; connection is not available yet.")
            return result
        result["pending_restart"] = True
        _require_idle(instance)
        if not configured:
            _unchanged(snapshots)
            main = instance.config_path.resolve()
            before, previous = snapshots[main]
            if b"# BEGIN PRINTFORGE SPOOLMAN" in before:
                raise MoonrakerSetupError("An incomplete PrintForge Spoolman block already exists; review it before retrying.")
            backup = main.with_name(main.name + ".printforge-backup-" + time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3))
            _write_owned(backup, before, previous)
            result["backup"] = str(backup)
            _unchanged(snapshots)
            newline = b"\r\n" if b"\r\n" in before else b"\n"
            addition = newline.join([b"", b"# BEGIN PRINTFORGE SPOOLMAN", b"[spoolman]",
                                     f"server: {desired}".encode(), b"# END PRINTFORGE SPOOLMAN", b""])
            _write_owned(main, before + addition, previous, replace=True)
            snapshots[main] = (before + addition, main.stat())
            result["applied"] = True
        _require_idle(instance)
        _unchanged(snapshots)
        instance.rpc("server.restart")
        # Read-only bounded verification. Never issue a second restart on failure.
        deadline = time.monotonic() + 15
        for attempt in range(20):
            if time.monotonic() >= deadline:
                break
            if attempt:
                sleep(0.5)
            try:
                info = instance.rpc("server.info")
                status = instance.rpc("server.spoolman.status")
                active = instance.rpc("server.config")
                endpoint = active.get("orig", {}).get("spoolman", {}).get("server")
                if (isinstance(info, dict) and "spoolman" not in info.get("failed_components", [])
                        and isinstance(status, dict) and status.get("spoolman_connected") is True
                        and _url(endpoint) == desired):
                    result.update(ready=True, pending_restart=False, reason="Spoolman connection configured and verified.")
                    return result
            except (OSError, ValueError, TypeError, KeyError, MoonrakerSetupError):
                continue
        result["reason"] = "Moonraker restart requested, but Spoolman connection is not verified. Inspect Moonraker before retrying."
    except MoonrakerSetupError as exc:
        result["reason"] = str(exc)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result["pending_restart"] = True
        result["reason"] = "Setup deferred: Moonraker or its files could not be verified. Retry when the printer is idle and Moonraker is available."
    return result
