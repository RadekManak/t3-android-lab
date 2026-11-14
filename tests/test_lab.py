import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import lab


class WorktreeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.make_repo('t3')
        self.config = {'checkout': str(self.repo)}
        self.lab_root = self.root / 'lab'
        (self.lab_root / 'skills/t3-android-lab').mkdir(parents=True)
        self.state = self.lab_root / '.state'
        self.state.mkdir()
        self.patcher = patch.multiple(lab, ROOT=self.lab_root, STATE=self.state)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def make_repo(self, name):
        directory = self.root / name
        directory.mkdir()
        subprocess.run(['git','init','-q',str(directory)],check=True)
        (directory / 'package.json').write_text(json.dumps({'name':'@t3tools/monorepo'}))
        subprocess.run(['git','-C',str(directory),'add','package.json'],check=True)
        subprocess.run(['git','-C',str(directory),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','Initial'],check=True)
        return directory

    def test_linked_worktree_gets_ignored_skill_and_other_projects_do_not(self):
        linked = self.root / 'linked'
        subprocess.run(['git','-C',str(self.repo),'worktree','add','-qb','test-worktree',str(linked)],check=True)
        for _ in range(2):
            lab.worktree_setup(linked,self.config)
        target=linked / '.agents/skills/t3-android-lab'
        self.assertEqual(target.resolve(),self.lab_root / 'skills/t3-android-lab')
        subprocess.run(['git','-C',str(linked),'check-ignore','-q',str(target)],check=True)
        other=self.make_repo('other')
        with self.assertRaisesRegex(RuntimeError,'not part'):
            lab.worktree_setup(other,self.config)
        self.assertFalse((other / '.agents/skills').exists())

    def test_reservation_prevents_another_owner_or_worktree_from_switching(self):
        lab.claim(self.repo,'thread-a')
        lab.claim(self.repo,'thread-a')
        original=(self.state / 'session.json').read_text()
        with self.assertRaisesRegex(RuntimeError,'reserved'):
            lab.claim(self.repo,'thread-b')
        with self.assertRaisesRegex(RuntimeError,'another worktree'):
            lab.claim(self.root / 'another','thread-a')
        with self.assertRaisesRegex(RuntimeError,'reserved'):
            lab.stop('thread-b')
        self.assertEqual((self.state / 'session.json').read_text(),original)

    def test_existing_skill_is_preserved(self):
        existing=self.repo / '.agents/skills/t3-android-lab'
        existing.mkdir(parents=True)
        (existing / 'SKILL.md').write_text('Existing skill')
        with self.assertRaisesRegex(RuntimeError,'overwrite'):
            lab.worktree_setup(self.repo,self.config)
        self.assertEqual((existing / 'SKILL.md').read_text(),'Existing skill')

    def test_reserved_image_cannot_be_replaced_by_other_owner_or_worktree(self):
        lab.claim(self.repo,'thread-a')
        lab.check_owner('thread-a',self.repo)
        for owner, worktree in [(None,self.repo),('thread-b',self.repo),('thread-a',self.root / 'other')]:
            with self.assertRaisesRegex(RuntimeError,'reserved'):
                lab.build(worktree,self.config,owner)


if __name__=='__main__':
    unittest.main()


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.config_path = root / 'config.local.json'
        self.config = {'adb': 'adb', 'serial': '100.64.0.1:33767', 'checkout': '/t3'}
        self.config_path.write_text(json.dumps(self.config))
        self.patcher = patch.multiple(lab, STATE=root / '.state', CONFIG=self.config_path)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_saves_the_port_adb_accepts(self):
        with patch.object(lab, 'open_ports', return_value=[35000, 40773]), \
             patch.object(lab, 'adb_ready', side_effect=lambda adb, serial, attach=False: serial.endswith(':40773')):
            self.assertEqual(lab.discover_serial(dict(self.config)), '100.64.0.1:40773')
        self.assertEqual(json.loads(self.config_path.read_text())['serial'], '100.64.0.1:40773')

    def test_timer_never_scans_and_leaves_an_unreserved_phone_alone(self):
        with patch.object(lab, 'open_ports') as scan, patch.object(lab, 'adb_ready', return_value=False) as ready:
            self.assertEqual(lab.keep_alive(dict(self.config)), {'idle': True})
            ready.assert_not_called()
            (lab.STATE).mkdir(parents=True, exist_ok=True)
            lab.claim('/w', 'thread-a')
            with self.assertRaises(RuntimeError):
                lab.keep_alive(dict(self.config))
        scan.assert_not_called()


class ReservationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name) / '.state'
        self.state.mkdir()
        self.patcher = patch.multiple(lab, STATE=self.state, stop_metro=lambda: None)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_owner_extends_others_wait_and_expiry_frees_the_phone(self):
        lab.claim('/w/a', 'thread-a', duration=1)
        self.assertEqual(lab.free(), '')
        with self.assertRaisesRegex(RuntimeError, 'reserved by thread-a'):
            lab.claim('/w/b', 'thread-b')
        with self.assertRaises(RuntimeError):
            lab.extend('thread-b', 1)
        self.assertGreater(lab.extend('thread-a', 3)['expires_in_minutes'], 170)
        with patch.object(lab.time, 'time', return_value=lab.time.time() + 4 * 3600):
            self.assertEqual(json.loads(lab.free()), {'free': True})
            self.assertEqual(lab.keep_alive({}), {'expired': 'thread-a'})
            lab.claim('/w/b', 'thread-b')
        self.assertEqual(json.loads((self.state / 'session.json').read_text())['owner'], 'thread-b')
