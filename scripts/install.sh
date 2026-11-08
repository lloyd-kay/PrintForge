#!/usr/bin/env bash
# Fetch and verify as the invoking user; elevate only the local verified helper.
set -euo pipefail
umask 077

version=v0.1.0
archive=
listener=()
usage() {
    cat <<'HELP'
Usage: bash install.sh [--version v0.1.0] [--archive FILE] [--port PORT] [--bind IP]

Install PrintForge on Debian 12/13 or 64-bit Raspberry Pi OS 12/13 with systemd.
The first installation listens on 0.0.0.0:8088; subsequent runs preserve the
listener unless --bind or --port is supplied. Use on a trusted LAN only.
--archive FILE uses a local release archive and its sibling SHA256SUMS file.
Downloads use public HTTPS, with your existing GitHub CLI login as a fallback.
Run as your normal user: sudo is requested only after download and verification.
HELP
}
fail() { printf 'PrintForge installation stopped: %s\n' "$*" >&2; exit 1; }
while (($#)); do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --version|--archive|--port|--bind)
            (($# >= 2)) || fail "$1 needs a value."
            case "$1" in
                --version) version=$2 ;;
                --archive) archive=$2 ;;
                --port|--bind) listener+=("$1" "$2") ;;
            esac
            shift 2 ;;
        *) fail "Unknown option: $1 (use --help)." ;;
    esac
done
[[ "$version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.-]+)?$ ]] || fail "Invalid release tag."
command -v python3 >/dev/null || fail "Python 3.11+ is required."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' || fail "Python 3.11+ is required."
if [[ ${EUID:-$(id -u)} == 0 && -n ${SUDO_USER:-} && ${SUDO_USER} != root ]]; then
    fail "Run this bootstrap without sudo so downloads use your own GitHub login; it will request sudo when needed."
fi
temp=$(mktemp -d "${TMPDIR:-/tmp}/printforge-install.XXXXXXXX")
cleanup() { rm -rf -- "$temp"; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

asset="printforge-${version}.tar.gz"
if [[ -n "$archive" ]]; then
    [[ -f "$archive" ]] || fail "Local archive does not exist."
    checksums="$(dirname -- "$archive")/SHA256SUMS"
    [[ -f "$checksums" ]] || fail "Local archive requires a sibling SHA256SUMS file."
    # Copy before validation: all subsequent reads use the private owned snapshot.
    cp -- "$archive" "$temp/$asset"
    cp -- "$checksums" "$temp/SHA256SUMS"
    checksum_name=$(basename -- "$archive")
else
    base="https://github.com/lloyd-kay/PrintForge/releases/download/$version"
    fetched=false
    if command -v curl >/dev/null; then
        if curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
            --retry 2 --connect-timeout 15 --max-time 300 "$base/$asset" -o "$temp/$asset" && \
           curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
            --retry 2 --connect-timeout 15 --max-time 60 "$base/SHA256SUMS" -o "$temp/SHA256SUMS"; then
            fetched=true
        fi
    fi
    if [[ "$fetched" != true ]] && command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
        rm -f -- "$temp/$asset" "$temp/SHA256SUMS"
        if gh release download "$version" --repo lloyd-kay/PrintForge \
            --pattern "$asset" --pattern SHA256SUMS --dir "$temp"; then
            fetched=true
        fi
    fi
    [[ "$fetched" == true ]] || fail "Release $version could not be downloaded. Check the tag and network. A GitHub 404 can mean a private repository: run 'gh auth login' as your normal user with repository access, then retry. Install curl or the GitHub CLI if neither is available."
    checksum_name=$asset
fi

# Execute no release code until its archive matches the published SHA-256 sum.
# Copy just the verified regular helper; it securely validates/extracts the bundle
# as the original user, rejecting traversal, links and special files.
digest=$(python3 - "$temp/$asset" "$temp/SHA256SUMS" "$checksum_name" "$temp/native_install.py" <<'PY'
import hashlib
from pathlib import Path
import re
import sys
import tarfile

archive, sums, name, helper = map(Path, sys.argv[1:])
try:
    matches = []
    for line in sums.read_text().splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line)
        if match and match.group(2) == str(name):
            matches.append(match.group(1).lower())
    if len(matches) != 1:
        raise ValueError("SHA256SUMS must contain exactly one checksum for this archive")
    with archive.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != matches[0]:
        raise ValueError("archive checksum mismatch; download a fresh release")
    with tarfile.open(archive, "r:gz") as tar:
        candidates = [m for m in tar.getmembers() if m.name == "printforge/scripts/native_install.py"]
        if len(candidates) != 1 or not candidates[0].isfile() or candidates[0].size > 2 * 1024 * 1024:
            raise ValueError("release has no unique regular installer helper")
        with tar.extractfile(candidates[0]) as source, helper.open("xb") as target:
            target.write(source.read())
    print(digest)
except (OSError, ValueError, tarfile.TarError) as exc:
    print(f"PrintForge verification failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
)
python3 "$temp/native_install.py" --extract "$temp/$asset" --destination "$temp/extracted"
args=(python3 "$temp/native_install.py" --source "$temp/extracted/printforge" \
      --version "$version" --digest "$digest" "${listener[@]}")
if [[ ${EUID:-$(id -u)} == 0 ]]; then
    "${args[@]}"
else
    command -v sudo >/dev/null || fail "sudo is required for the installation step."
    sudo -- "${args[@]}"
fi
