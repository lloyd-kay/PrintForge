"""Connect the native services without changing operator-approved printer profiles."""
import json
from pathlib import Path
import shlex
import time
import urllib.request
from urllib.parse import urlsplit, urlunsplit
import uuid

try:
    from .native_install import InstallError, Paths, atomic_write, probe_http, run_command
except ImportError:
    from native_install import InstallError, Paths, atomic_write, probe_http, run_command


def read_environment(path):
    values = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, separator, value = line.partition('=')
        if not separator:
            raise InstallError('Unsupported environment file format; keep one KEY=value per line.')
        parsed = shlex.split(value, comments=False)
        if len(parsed) > 1 or key.strip() in values:
            raise InstallError('Ambiguous environment setting; use one quoted or plain value per key.')
        values[key.strip()] = parsed[0] if parsed else ''
    return values


def register_printer(base_url, token, data):
    request = urllib.request.Request(base_url + '/api/printers/discover',
        data=json.dumps(data).encode(), headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
    # The local controller must never forward its administrator token elsewhere.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=90) as response:
        return json.load(response)


def inventory_identity(url):
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower()
    if host == 'localhost':
        host = '127.0.0.1'
    if ':' in host:
        host = '[' + host + ']'
    port = parsed.port
    if port and port != (443 if parsed.scheme == 'https' else 80):
        host += ':' + str(port)
    return urlunsplit((parsed.scheme.lower(), host, parsed.path.rstrip('/'), parsed.query, parsed.fragment))


def setup_stack(version, *, spoolman_url=None, moonraker_data=None, paths=None,
                discover=None, inspect=None, ensure=None, configure=None,
                run=run_command, probe=probe_http, sleep=time.sleep, api=register_printer):
    if discover is None or inspect is None or configure is None:
        try:
            from .moonraker_setup import discover_instances, inspect_spoolman, configure_spoolman
        except ImportError:
            from moonraker_setup import discover_instances, inspect_spoolman, configure_spoolman
        discover = discover or discover_instances
        inspect = inspect or inspect_spoolman
        configure = configure or configure_spoolman
    if ensure is None:
        try:
            from .spoolman_setup import ensure_spoolman
        except ImportError:
            from spoolman_setup import ensure_spoolman
        ensure = ensure_spoolman
    paths = paths or Paths()
    environment = read_environment(paths.env)
    instances = discover(moonraker_data)
    existing_urls = [value.rstrip('/') for i in instances if (value := inspect(i))]
    existing = {inventory_identity(value) for value in existing_urls}
    current_url = environment.get('SPOOLMAN_URL', '').rstrip('/')
    requested_url = (spoolman_url or current_url).rstrip('/')
    if current_url and inventory_identity(requested_url) != inventory_identity(current_url):
        raise InstallError('PrintForge already uses a different Spoolman inventory. Migrate it explicitly before changing servers.')
    if len(existing) > 1 or (requested_url and existing and existing != {inventory_identity(requested_url)}):
        raise InstallError('Local Moonraker instances use a different Spoolman inventory. Align those settings before rerunning.')
    chosen_url = ensure(requested_url or next(iter(existing_urls), None))
    if any(char in chosen_url for char in '\r\n\x00'):
        raise InstallError('Invalid Spoolman URL returned by setup.')
    saved = json.loads(paths.settings.read_text())
    bind, port = saved['bind'], saved['port']
    if current_url != chosen_url:
        old = paths.env.read_bytes()
        lines = old.decode().splitlines()
        lines = [line for line in lines if line.partition('=')[0].strip() != 'SPOOLMAN_URL']
        # URL validation in ensure_spoolman excludes credentials/whitespace.
        lines.append('SPOOLMAN_URL=' + chosen_url)
        backup = paths.config / ('printforge.env.backup-' + uuid.uuid4().hex[:12])
        atomic_write(backup, old, 0o600)
        atomic_write(paths.env, ('\n'.join(lines) + '\n').encode(), 0o600)
        try:
            run(['systemctl', 'restart', 'printforge.service'])
            for _ in range(30):
                if probe(bind, port, version):
                    break
                sleep(1)
            else:
                raise InstallError('PrintForge did not become healthy.')
        except BaseException:
            atomic_write(paths.env, old, 0o600)
            run(['systemctl', 'restart', 'printforge.service'])
            raise InstallError('Spoolman connection activation failed; previous PrintForge settings restored.')
    host = {'0.0.0.0': '127.0.0.1', '::': '::1'}.get(bind, bind)
    if ':' in host:
        host = '[' + host + ']'
    base_url = f'http://{host}:{port}'
    pending = not instances
    for instance in instances:
        outcome = configure(instance, chosen_url)
        print(f'Moonraker {instance.name}: {outcome["reason"]}')
        pending = pending or bool(outcome['pending_restart']) or not outcome.get('ready', outcome['applied'])
        try:
            api(base_url, environment['PRINTFORGE_TOKEN'], {'name': instance.name,
                'moonraker_url': instance.url, 'api_key': instance.api_key})
            print(f'Registered {instance.name} in PrintForge; existing printer settings preserved.')
        except Exception:
            pending = True
            print(f'Connection registration for {instance.name} needs attention; check PrintForge and rerun.')
    print('Spoolman is connected to PrintForge. New printers remain disabled until their profile and loaded spool are reviewed.')
    if not instances:
        print('No accessible local Moonraker found. Use --moonraker-data PATH for a custom installation, or add remote printers in the dashboard.')
    elif pending:
        print('Some Moonraker setup is pending. Review the messages above; rerun when each printer is idle and connected.')
    return 2 if pending else 0
