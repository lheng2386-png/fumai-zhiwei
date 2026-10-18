"""Linux tests for frontend release safety and rollback, without network access."""

import importlib.util
import io
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch


MODULE = Path(__file__).resolve().parents[1] / 'deploy_frontend.py'
SPEC = importlib.util.spec_from_file_location('deploy_frontend', MODULE)
deployment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deployment)
OLD = '1' * 40
NEW = '2' * 40


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / 'frontend'
        self.releases = self.root / 'releases'
        self.releases.mkdir(parents=True)
        self.old = self.make_release(OLD, self.releases)
        (self.root / 'current').symlink_to(self.old)

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def make_release(commit, releases):
        directory = releases / commit
        (directory / 'dist').mkdir(parents=True, exist_ok=True)
        (directory / 'dist/deployment.json').write_text(json.dumps({'commit': commit}))
        return directory

    def test_success_retains_previous_and_records_exact_commit(self):
        with patch.object(deployment, 'prepare_release', side_effect=self.make_release), patch.object(deployment, 'check_health'):
            result = deployment.deploy(NEW, self.root, 'http://127.0.0.1')
        self.assertEqual((self.root / 'current').resolve().name, NEW)
        self.assertEqual((self.root / 'previous').resolve(), self.old)
        self.assertEqual(result['commit'], NEW)
        self.assertEqual(json.loads((self.root / 'deployment-history.jsonl').read_text())['previous_commit'], OLD)

    def test_failed_health_restores_previous_and_does_not_log_success(self):
        with patch.object(deployment, 'prepare_release', side_effect=self.make_release), patch.object(deployment, 'check_health', side_effect=RuntimeError('test failure')):
            with self.assertRaises(RuntimeError):
                deployment.deploy(NEW, self.root, 'http://127.0.0.1')
        self.assertEqual((self.root / 'current').resolve(), self.old)
        self.assertFalse((self.root / 'deployment-history.jsonl').exists())

    def test_real_directory_is_never_overwritten_by_a_release_link(self):
        (self.root / 'current').unlink()
        (self.root / 'current').mkdir()
        marker = self.root / 'current/keep.txt'
        marker.write_text('keep')
        with patch.object(deployment, 'prepare_release', side_effect=self.make_release):
            with self.assertRaises(RuntimeError):
                deployment.deploy(NEW, self.root, 'http://127.0.0.1')
        self.assertEqual(marker.read_text(), 'keep')

    def make_archive(self, name, link=False):
        archive = Path(self.temporary.name) / 'source.tar.gz'
        with tarfile.open(archive, 'w:gz') as source:
            entry = tarfile.TarInfo(f'fumai-zhiwei-{NEW}/frontend/dist/{name}')
            if link:
                entry.type = tarfile.SYMTYPE
                entry.linkname = '/etc/passwd'
                source.addfile(entry)
            else:
                content = b'not allowed'
                entry.size = len(content)
                source.addfile(entry, io.BytesIO(content))
        return archive

    def test_archive_path_traversal_and_symlinks_are_rejected(self):
        for name, is_link in [('../escape.txt', False), ('index.html', True)]:
            with self.subTest(name=name, is_link=is_link):
                archive = self.make_archive(name, is_link)
                with patch.object(deployment, 'download_archive', side_effect=lambda commit, target: shutil.copyfile(archive, target)):
                    with self.assertRaises(ValueError):
                        deployment.prepare_release(NEW, self.releases)
                self.assertFalse((self.releases / NEW).exists())
                self.assertFalse((self.root / 'escape.txt').exists())
                self.assertEqual(list(self.releases.glob('.release-*')), [])


if __name__ == '__main__':
    unittest.main()
