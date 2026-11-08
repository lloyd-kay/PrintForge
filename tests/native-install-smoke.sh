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

bash /release/install.sh --archive /release/printforge-v0.1.0.tar.gz --port 18088 --bind 127.0.0.1
systemctl is-active --quiet printforge
test "$(systemctl show printforge -p User --value)" = printforge
test "$(stat -c '%a' /etc/printforge/printforge.env)" = 600
first_settings=$(sha256sum /etc/printforge/printforge.env)
first_release=$(readlink /opt/printforge/current)

python3 - <<'PY'
import json
import urllib.error
import urllib.request
health = json.load(urllib.request.urlopen('http://127.0.0.1:18088/api/health'))
assert health == {'status': 'ok', 'mode': 'live', 'version': '0.1.0'}, health
assert 'PrintForge' in urllib.request.urlopen('http://127.0.0.1:18088/').read().decode()
try:
    urllib.request.urlopen('http://127.0.0.1:18088/api/state')
except urllib.error.HTTPError as error:
    assert error.code == 401
else:
    raise AssertionError('Unauthenticated API access was allowed')
PY

bash /release/install.sh --archive /release/printforge-v0.1.0.tar.gz
test "$first_settings" = "$(sha256sum /etc/printforge/printforge.env)"
test "$first_release" = "$(readlink /opt/printforge/current)"
systemctl is-active --quiet printforge
curl --fail --silent http://127.0.0.1:18088/api/health
echo 'Debian 13 native installation, authenticated UI, and repeat installation verified.'
