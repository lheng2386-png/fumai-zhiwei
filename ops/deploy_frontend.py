#!/usr/bin/env python3
"""Deploy the static frontend of an exact GitHub commit, with health rollback."""

import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile
import time
import urllib.request
import uuid


REPOSITORY = 'lheng2386-png/fumai-zhiwei'
REQUIRED_FILES = ('index.html', 'app.js', 'data.js', 'styles.css', 'favicon.svg')
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_EXTRACTED_BYTES = 100 * 1024 * 1024


def atomic_link(target: Path, link: Path) -> None:
    """Replace only a symlink; refuse to overwrite a real directory or file."""
    if os.path.lexists(link) and not link.is_symlink():
        raise RuntimeError(f'Refusing to replace non-symlink: {link}')
    temporary = link.with_name(f'.{link.name}-{uuid.uuid4().hex}')
    try:
        temporary.symlink_to(target, target_is_directory=True)
        os.replace(temporary, link)
    finally:
        if temporary.is_symlink():
            temporary.unlink()


def download_archive(commit: str, target: Path) -> None:
    url = f'https://codeload.github.com/{REPOSITORY}/tar.gz/{commit}'
    request = urllib.request.Request(url, headers={'User-Agent': 'fumai-release/1.0'})
    with urllib.request.urlopen(request, timeout=60) as response, target.open('wb') as output:
        count = 0
        while chunk := response.read(1024 * 1024):
            count += len(chunk)
            if count > MAX_ARCHIVE_BYTES:
                raise ValueError('Source archive exceeds the deployment size limit')
            output.write(chunk)


def prepare_release(commit: str, releases: Path) -> Path:
    release = releases / commit
    if release.exists():
        marker = json.loads((release / 'dist/deployment.json').read_text())
        if marker.get('commit') != commit:
            raise ValueError('Existing release marker does not match the requested commit')
        return release
    stage = Path(tempfile.mkdtemp(prefix='.release-', dir=releases))
    try:
        dist = stage / 'dist'
        dist.mkdir(mode=0o755)
        with tempfile.TemporaryDirectory(prefix='fumai-download-') as scratch:
            archive = Path(scratch) / 'source.tar.gz'
            download_archive(commit, archive)
            prefix = f'fumai-zhiwei-{commit}/frontend/dist/'
            total = 0
            seen = set()
            with tarfile.open(archive, 'r:gz') as source:
                for member in source:
                    if not member.name.startswith(prefix):
                        continue
                    relative = PurePosixPath(member.name[len(prefix):])
                    if not relative.parts or relative.is_absolute() or '..' in relative.parts:
                        raise ValueError('Invalid path in frontend archive')
                    if member.isdir():
                        continue
                    if not member.isfile() or relative.as_posix() in seen:
                        raise ValueError('Unsupported or duplicate frontend archive member')
                    seen.add(relative.as_posix())
                    total += member.size
                    if member.size < 0 or total > MAX_EXTRACTED_BYTES:
                        raise ValueError('Frontend exceeds the extracted size limit')
                    destination = dist.joinpath(*relative.parts)
                    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                    with source.extractfile(member) as content, destination.open('xb') as output:
                        shutil.copyfileobj(content, output)
                    destination.chmod(0o644)
        for name in REQUIRED_FILES:
            if not (dist / name).is_file() or (dist / name).stat().st_size == 0:
                raise ValueError(f'Required frontend file is missing or empty: {name}')
        marker = {'repository': REPOSITORY, 'commit': commit,
                  'prepared_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                  'data_mode': 'constructed_demo', 'component': 'frontend'}
        (dist / 'deployment.json').write_text(json.dumps(marker, indent=2) + '\n')
        (dist / 'deployment.json').chmod(0o644)
        stage.chmod(0o755)
        stage.rename(release)
        return release
    finally:
        if stage.exists() and stage.parent.resolve() == releases.resolve() and stage.name.startswith('.release-'):
            shutil.rmtree(stage)


def check_health(base_url: str, commit: str) -> None:
    last_error = None
    for attempt in range(6):
        try:
            headers = {'Cache-Control': 'no-cache'}
            request = urllib.request.Request(base_url.rstrip('/') + '/deployment.json', headers=headers)
            with urllib.request.urlopen(request, timeout=8) as response:
                marker = json.load(response)
            if marker.get('commit') != commit:
                raise ValueError('HTTP version marker does not match the requested commit')
            for name in ('', 'app.js', 'data.js', 'styles.css', 'favicon.svg'):
                with urllib.request.urlopen(base_url.rstrip('/') + '/' + name, timeout=8) as response:
                    if response.status != 200 or not response.read(64):
                        raise ValueError(f'Empty or failed HTTP response: {name or "index.html"}')
            return
        except Exception as error:
            last_error = error
            if attempt < 5:
                time.sleep(1)
    raise RuntimeError(f'Frontend health check failed: {last_error}')


def deploy(commit: str, root: Path, health_url: str) -> dict:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o755)
    releases = root / 'releases'
    releases.mkdir(exist_ok=True, mode=0o755)
    current = root / 'current'
    with (root / '.deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = current.resolve() if current.is_symlink() else None
        if previous and previous.parent != releases.resolve():
            raise ValueError('Current release points outside this frontend release directory')
        release = prepare_release(commit, releases)
        atomic_link(release, current)
        try:
            check_health(health_url, commit)
        except Exception:
            if previous:
                atomic_link(previous, current)
            elif current.is_symlink() and current.resolve() == release.resolve():
                current.unlink()
            raise
        if previous and previous != release.resolve():
            atomic_link(previous, root / 'previous')
        result = {'status': 'deployed', 'component': 'frontend', 'commit': commit,
                  'previous_commit': previous.name if previous else None,
                  'verified_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                  'health_url': health_url}
        with (root / 'deployment-history.jsonl').open('a') as history:
            history.write(json.dumps(result) + '\n')
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True, help='Full 40-character GitHub commit SHA')
    parser.add_argument('--root', type=Path, default=Path('/srv/fumai-zhiwei/frontend'))
    parser.add_argument('--health-url', default='http://127.0.0.1')
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-f]{40}', args.commit):
        parser.error('--commit requires a full lowercase Git commit SHA')
    print(json.dumps(deploy(args.commit, args.root, args.health_url), indent=2))


if __name__ == '__main__':
    main()
