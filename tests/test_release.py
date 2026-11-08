import hashlib
import tarfile

import pytest

from scripts.build_release import build_release


def source_tree(tmp_path):
    source = tmp_path / 'source'
    for path, content in {
        'app/__init__.py': '', 'app/main.py': '# app\n',
        'frontend/dist/index.html': '<html>PrintForge</html>',
        'frontend/dist/assets/app.js': 'console.log("PrintForge")',
        'requirements-runtime.txt': '-c requirements.lock\nfastapi\n',
        'requirements.lock': 'fastapi==0.162.2\n',
        'LICENSE': 'AGPL', 'README.md': '# PrintForge',
        'scripts/install.sh': '#!/usr/bin/env bash\n',
        'scripts/native_install.py': '# installer\n',
        'scripts/setup_stack.py': '# stack\n',
        'scripts/spoolman_setup.py': '# spoolman\n',
        'scripts/moonraker_setup.py': '# moonraker\n',
        'docs/INSTALLATION.md': '# Install',
    }.items():
        target = source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
    (source / '.env').write_text('SECRET=must-not-ship')
    return source


def test_release_has_compiled_dashboard_and_no_local_secrets(tmp_path):
    source = source_tree(tmp_path)
    output = tmp_path / 'release'
    archive = build_release(source, output, 'v0.1.0')
    with tarfile.open(archive) as bundle:
        names = bundle.getnames()
        assert 'printforge/frontend/dist/index.html' in names
        assert 'printforge/scripts/native_install.py' in names
        assert 'printforge/scripts/setup_stack.py' in names
        assert 'printforge/scripts/spoolman_setup.py' in names
        assert 'printforge/scripts/moonraker_setup.py' in names
        assert 'printforge/.env' not in names
        assert bundle.extractfile('printforge/VERSION').read() == b'v0.1.0\n'
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert f'{checksum}  {archive.name}' in (output / 'SHA256SUMS').read_text()
    assert (output / 'install.sh').read_bytes() == (source / 'scripts/install.sh').read_bytes()
    assert build_release(source, tmp_path / 'again', 'v0.1.0').read_bytes() == archive.read_bytes()


def test_missing_frontend_fails_before_publishing_bundle(tmp_path):
    source = source_tree(tmp_path)
    (source / 'frontend/dist/index.html').unlink()
    with pytest.raises(ValueError, match='frontend'):
        build_release(source, tmp_path / 'release', 'v0.1.0')


def test_version_cannot_escape_release_directory(tmp_path):
    with pytest.raises(ValueError, match='version'):
        build_release(source_tree(tmp_path), tmp_path / 'release', '../bad')
