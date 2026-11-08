#!/usr/bin/env python3
"""Build a portable source + prebuilt-dashboard native installer archive."""
import argparse
import gzip
import hashlib
import io
import re
import tarfile
from pathlib import Path

REQUIRED = ('app/main.py', 'frontend/dist/index.html', 'requirements-runtime.txt',
            'requirements.lock', 'LICENSE', 'README.md', 'scripts/install.sh',
            'scripts/native_install.py', 'docs/INSTALLATION.md')


def build_release(source: Path, output: Path, version: str) -> Path:
    if not re.fullmatch(r'v\d+\.\d+\.\d+(?:-[a-zA-Z0-9.]+)?', version):
        raise ValueError('Invalid release version')
    for name in REQUIRED:
        if not (source / name).is_file():
            raise ValueError(f'Missing release input: {name}; build the frontend first')
    paths = set(REQUIRED)
    paths.update(str(p.relative_to(source)).replace('\\', '/') for p in (source / 'app').rglob('*.py'))
    paths.update(str(p.relative_to(source)).replace('\\', '/') for p in (source / 'frontend/dist').rglob('*') if p.is_file())
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f'printforge-{version}.tar.gz'
    with archive.open('wb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w') as bundle:
                contents = {name: (source / name).read_bytes() for name in sorted(paths)}
                contents['VERSION'] = (version + '\n').encode()
                for name, data in contents.items():
                    info = tarfile.TarInfo('printforge/' + name)
                    info.size = len(data)
                    info.mode = 0o755 if name.endswith('.sh') else 0o644
                    info.mtime = 0
                    bundle.addfile(info, io.BytesIO(data))
    installer = output / 'install.sh'
    installer.write_bytes((source / 'scripts/install.sh').read_bytes())
    checksums = ''.join(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n' for path in (archive, installer))
    (output / 'SHA256SUMS').write_text(checksums, encoding='utf-8')
    return archive


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', default='v0.1.0')
    parser.add_argument('--output', type=Path, default=Path('release'))
    args = parser.parse_args()
    print(build_release(Path(__file__).resolve().parents[1], args.output, args.version))
