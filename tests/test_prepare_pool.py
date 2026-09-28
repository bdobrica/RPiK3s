"""Safety regressions: exercise the helper without any real disk commands."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('pool', Path(__file__).parents[1] / 'roles/zfs/files/prepare-pool.py')
pool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pool)


class PoolSafety(unittest.TestCase):
    def attempt(self, *, flags=(), active=False, signature='ext4', mounts=(), children=False,
                pool_device='/dev/sda1', kind='part', transport='usb'):
        commands = []

        def fake(*args, check=True):
            commands.append(args)
            out, rc = '', 0
            if args[0] == 'lsblk':
                out = json.dumps({'blockdevices': [{'type': kind, 'pkname': '/dev/sda', 'mountpoints': list(mounts)}]}) if '-Jp' in args else transport
            elif args[:2] == ('zpool', 'list'):
                rc = 0 if active else 1
            elif args[:2] == ('zpool', 'import'):
                rc = 1
            elif args[0] == 'blkid':
                out = signature
            elif args[0] == 'findmnt':
                out = json.dumps({'filesystems': [{'children': [{}] if children else []}]})
            elif args[:2] == ('zpool', 'status'):
                out = pool_device
            elif args[:2] == ('zfs', 'get'):
                out = '/mnt/ssd' if 'mountpoint' in args else 'yes'
            return SimpleNamespace(returncode=rc, stdout=out)

        argv = ['prepare-pool', '--device', '/dev/disk/by-id/test-part1', '--pool', 'ssd', '--mountpoint', '/mnt/ssd', *flags]
        with patch('sys.argv', argv), patch.object(pool, 'run', side_effect=fake), \
                patch.object(pool.os.path, 'realpath', return_value='/dev/sda1'), \
                patch.object(pool.os.path, 'exists', return_value=False), \
                patch('builtins.open', unittest.mock.mock_open(read_data='')):
            try:
                pool.main()
            except SystemExit:
                self.assertFalse(any(c[:2] == ('zpool', 'create') for c in commands))
                raise
        return commands

    def test_existing_filesystem_needs_force(self):
        with self.assertRaisesRegex(SystemExit, 'ssd_force_format'):
            self.attempt(flags=['--allow-format'])

    def test_blank_still_needs_authorization(self):
        with self.assertRaisesRegex(SystemExit, 'authorization'):
            self.attempt(signature='')

    def test_never_overwrite_zfs_member(self):
        with self.assertRaisesRegex(SystemExit, 'Existing ZFS member'):
            self.attempt(signature='zfs_member', flags=['--force-format'])

    def test_never_erase_other_mount(self):
        with self.assertRaisesRegex(SystemExit, 'other mounts'):
            self.attempt(mounts=['/'], flags=['--force-format'])

    def test_nested_mounts_block_conversion(self):
        with self.assertRaisesRegex(SystemExit, 'nested'):
            self.attempt(mounts=['/mnt/ssd'], children=True, flags=['--force-format'])

    def test_rerun_with_force_does_not_recreate(self):
        commands = self.attempt(active=True, flags=['--force-format'])
        self.assertFalse(any(c[:2] == ('zpool', 'create') for c in commands))

    def test_existing_pool_must_match_selected_disk(self):
        with self.assertRaisesRegex(SystemExit, 'configured device'):
            self.attempt(active=True, pool_device='/dev/sdb1', flags=['--force-format'])

    def test_authorized_blank_partition_is_created_without_force(self):
        commands = self.attempt(signature='', flags=['--allow-format'])
        create = next(c for c in commands if c[:2] == ('zpool', 'create'))
        self.assertNotIn('-f', create)

    def test_reject_whole_disks_and_non_usb(self):
        for args in [{'kind': 'disk'}, {'transport': 'mmc'}]:
            with self.assertRaises(SystemExit):
                self.attempt(flags=['--force-format'], **args)


if __name__ == '__main__':
    unittest.main()
