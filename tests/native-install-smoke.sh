#!/usr/bin/env bash
# Runs ONLY in the disposable Debian systemd container launched by CI.
set -euo pipefail
umask 077
test -f /.dockerenv || { echo 'This smoke test is container-only.' >&2; exit 1; }
for attempt in $(seq 1 40); do
    systemctl is-active --quiet multi-user.target && break
    sleep 1
done
systemctl is-active --quiet multi-user.target

python3 /tests/fake_moonraker.py >/tmp/printforge-moonraker-fixture.log 2>&1 &
fixture_pid=$!
cleanup() {
    kill "$fixture_pid" 2>/dev/null || true
    wait "$fixture_pid" 2>/dev/null || true
}
trap cleanup EXIT
for attempt in $(seq 1 40); do
    test -S /home/printer/printer_data/comms/moonraker.sock && \
        curl --fail --silent -H 'X-Api-Key: ci-moonraker-api-key' \
        http://127.0.0.1:7125/server/info >/dev/null && break
    kill -0 "$fixture_pid" 2>/dev/null || { cat /tmp/printforge-moonraker-fixture.log; exit 1; }
    sleep 0.25
done
test -S /home/printer/printer_data/comms/moonraker.sock
cp /home/printer/printer_data/config/moonraker.conf /tmp/printforge-original-moonraker.conf

# No Spoolman or Moonraker options: exercise automatic installation/discovery.
bash /release/install.sh --version v0.2.0 --archive /release/printforge-v0.2.0.tar.gz --port 18088 --bind 127.0.0.1
systemctl is-active --quiet printforge
systemctl is-active --quiet printforge-spoolman
test "$(systemctl show printforge -p User --value)" = printforge
test "$(systemctl show printforge-spoolman -p User --value)" = printforge-spoolman
test "$(stat -c '%a' /etc/printforge/printforge.env)" = 600
first_settings=$(sha256sum /etc/printforge/printforge.env)
first_release=$(readlink /opt/printforge/current)
first_spoolman_release=$(readlink /opt/printforge-spoolman/current)

python3 - <<'PY'
import configparser
import json
from pathlib import Path
import urllib.error
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
env = dict(line.split('=', 1) for line in Path('/etc/printforge/printforge.env').read_text().splitlines()
           if line and not line.startswith('#'))
assert env['SPOOLMAN_URL'] == 'http://127.0.0.1:7912'
token = env['PRINTFORGE_TOKEN']

def request(url, body=None, headers=None):
    headers = dict(headers or {})
    data = None if body is None else json.dumps(body).encode()
    if data is not None:
        headers['Content-Type'] = 'application/json'
    with opener.open(urllib.request.Request(url, data=data, headers=headers), timeout=10) as response:
        return json.load(response)

base = 'http://127.0.0.1:18088'
auth = {'Authorization': 'Bearer ' + token}
health = request(base + '/api/health')
assert health == {'status': 'ok', 'mode': 'live', 'version': '0.2.0'}, health
assert 'PrintForge' in opener.open(base + '/').read().decode()
try:
    opener.open(base + '/api/state')
except urllib.error.HTTPError as error:
    assert error.code == 401
else:
    raise AssertionError('Unauthenticated API access was allowed')

state = request(base + '/api/state', headers=auth)
assert state['scheduler_enabled'] is False
assert all(printer['enabled'] is False for printer in state['printers'])
printers = [p for p in state['printers'] if p['moonraker_url'] == 'http://127.0.0.1:7125']
assert len(printers) == 1, printers
printer = printers[0]
assert printer['volume'] == [300, 300, 400], printer
assert printer['nozzle'] == 0.4, printer
assert printer['bed_clear'] is False
assert 'api_key' not in printer
assert 'ci-moonraker-api-key' not in json.dumps(state)

config_file = Path('/home/printer/printer_data/config/moonraker.conf')
config = configparser.ConfigParser(interpolation=None)
config.read(config_file)
assert config['spoolman']['server'] == env['SPOOLMAN_URL']
assert config.getboolean('authorization', 'force_logins') is True
assert config_file.read_text().count('[spoolman]') == 1
original = Path('/tmp/printforge-original-moonraker.conf').read_bytes()
assert config_file.read_bytes().startswith(original)
backups = list(config_file.parent.glob('moonraker.conf.printforge-backup-*'))
assert len(backups) == 1 and backups[0].read_bytes() == original
fixture = json.loads(Path('/tmp/printforge-moonraker-fixture/state.json').read_text())
assert fixture['restart_count'] == 1, fixture
assert fixture['forbidden_actions'] == [], fixture
moonraker = request('http://127.0.0.1:7125/server/spoolman/status', headers={'X-Api-Key': 'ci-moonraker-api-key'})
assert moonraker['result']['spoolman_connected'] is True

spoolman = env['SPOOLMAN_URL'] + '/api/v1'
request(spoolman + '/health')
filament = request(spoolman + '/filament', {'name': 'Installer persistence check', 'material': 'PLA',
                                         'density': 1.24, 'diameter': 1.75, 'weight': 1000})
spool = request(spoolman + '/spool', {'filament_id': filament['id'], 'remaining_weight': 750})
assert spool['remaining_weight'] == 750, spool
for name in ('logs', 'backups'):
    assert (Path('/var/lib/printforge-spoolman') / name).is_dir()
request(base + '/api/sync', {}, auth)
updated = request(base + '/api/state', headers=auth)
assert any(item['id'] == spool['id'] and item['remaining_g'] == 750 for item in updated['spools'])
Path('/tmp/printforge-smoke-snapshot.json').write_text(json.dumps({
    'printer_id': printer['id'], 'filament_id': filament['id'], 'spool_id': spool['id'],
    'moonraker_config': config_file.read_text(),
}))
PY

# Omit listener overrides on the repeat run: existing settings must win.
bash /release/install.sh --version v0.2.0 --archive /release/printforge-v0.2.0.tar.gz
verify_persisted_stack() {
test "$first_settings" = "$(sha256sum /etc/printforge/printforge.env)"
test "$first_release" = "$(readlink /opt/printforge/current)"
test "$first_spoolman_release" = "$(readlink /opt/printforge-spoolman/current)"
systemctl is-active --quiet printforge
systemctl is-active --quiet printforge-spoolman
python3 - <<'PY'
import json
from pathlib import Path
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
env = dict(line.split('=', 1) for line in Path('/etc/printforge/printforge.env').read_text().splitlines()
           if line and not line.startswith('#'))
auth = {'Authorization': 'Bearer ' + env['PRINTFORGE_TOKEN']}
expected = json.loads(Path('/tmp/printforge-smoke-snapshot.json').read_text())
def request(url, headers=None, body=None):
    data = None if body is None else json.dumps(body).encode()
    with opener.open(urllib.request.Request(url, data=data, headers=headers or {}), timeout=10) as response:
        return json.load(response)

base = 'http://127.0.0.1:18088'
assert request(base + '/api/health')['version'] == '0.2.0'
listener = json.loads(Path('/etc/printforge/installer.json').read_text())
assert listener['bind'] == '127.0.0.1' and listener['port'] == 18088, listener
request(base + '/api/sync', auth, {})
state = request(base + '/api/state', auth)
printers = [p for p in state['printers'] if p['moonraker_url'] == 'http://127.0.0.1:7125']
assert len(printers) == 1 and printers[0]['id'] == expected['printer_id'], printers
assert printers[0]['enabled'] is False and printers[0]['bed_clear'] is False
assert state['scheduler_enabled'] is False and state['jobs'] == []
assert 'ci-moonraker-api-key' not in json.dumps(state)
assert all('api_key' not in printer for printer in state['printers'])
assert request(env['SPOOLMAN_URL'] + '/api/v1/health')['status'] == 'healthy'
spool = request(env['SPOOLMAN_URL'] + '/api/v1/spool/' + str(expected['spool_id']))
assert spool['filament']['id'] == expected['filament_id']
assert spool['filament']['name'] == 'Installer persistence check'
assert spool['remaining_weight'] == 750
assert any(item['id'] == expected['spool_id'] and item['remaining_g'] == 750 for item in state['spools'])
config = Path('/home/printer/printer_data/config/moonraker.conf').read_text()
assert config == expected['moonraker_config'] and config.count('[spoolman]') == 1
fixture = json.loads(Path('/tmp/printforge-moonraker-fixture/state.json').read_text())
assert fixture['restart_count'] == 1 and fixture['forbidden_actions'] == [], fixture
PY
}
verify_persisted_stack

# Recovery must work with the local Spoolman URL already saved in the settings.
systemctl stop printforge-spoolman
if systemctl is-active --quiet printforge-spoolman; then
    echo 'Spoolman did not stop for the managed-service recovery check.' >&2
    exit 1
fi
bash /release/install.sh --version v0.2.0 --archive /release/printforge-v0.2.0.tar.gz
verify_persisted_stack
echo 'Debian 13 native stack, authenticated discovery, inventory preservation, repeat installation, and stopped-service recovery verified.'
