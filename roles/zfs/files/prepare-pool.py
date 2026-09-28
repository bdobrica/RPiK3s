#!/usr/bin/env python3
"""Provision only an explicitly selected USB partition; never force-import a pool."""
import argparse
import json
import os
import re
import subprocess


def run(*args, check=True):
    result = subprocess.run(args, check=False, text=True, capture_output=True)
    if check and result.returncode:
        raise SystemExit(f'{args[0]} failed: {result.stderr.strip()}')
    return result


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--device', required=True)
    parser.add_argument('--pool', required=True)
    parser.add_argument('--mountpoint', required=True)
    parser.add_argument('--allow-format', action='store_true')
    parser.add_argument('--force-format', action='store_true')
    args = parser.parse_args()
    require(re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]*', args.pool), 'Invalid pool name')
    require(args.device.startswith('/dev/disk/by-id/'), 'Use a persistent /dev/disk/by-id partition path')
    require(args.mountpoint.startswith('/mnt/') and '..' not in args.mountpoint, 'Mountpoint must be below /mnt')
    device = os.path.realpath(args.device)
    info = json.loads(run('lsblk', '-Jp', '-o', 'NAME,TYPE,PKNAME,MOUNTPOINTS', device).stdout)['blockdevices'][0]
    require(info['type'] == 'part', 'Select a partition, not a whole disk or root device')
    require(run('lsblk', '-dnro', 'TRAN', info['pkname']).stdout.strip() == 'usb', 'Device must be USB backed')
    active = run('zpool', 'list', '-H', '-o', 'name', args.pool, check=False)
    if active.returncode:
        # Import only a pool discoverable on the selected device (no force).
        imported = run('zpool', 'import', '-d', args.device, args.pool, check=False)
        if imported.returncode == 0:
            print('changed: imported pool')
        else:
            signature = run('blkid', '-p', '-o', 'value', '-s', 'TYPE', device, check=False).stdout.strip()
            require(signature != 'zfs_member', 'Existing ZFS member could not be imported; inspect manually')
            require(args.allow_format or args.force_format, 'Pool absent: explicit format authorization required')
            require(not signature or args.force_format, 'Existing filesystem: ssd_force_format=true required')
            mounts = [m for m in info.get('mountpoints', []) if m]
            require(all(m == args.mountpoint for m in mounts), 'Selected device has other mounts; refusing erase')
            if mounts:
                tree = json.loads(run('findmnt', '-J', '-R', args.mountpoint).stdout)['filesystems'][0]
                require(not tree.get('children'), 'Unmount nested filesystems before conversion')
                run('umount', args.mountpoint)  # Busy mounts fail; never lazy/force unmount.
            require(not os.path.exists(args.mountpoint) or not os.listdir(args.mountpoint), 'Mountpoint contains files on underlying filesystem')
            command = ['zpool', 'create']
            if args.force_format:
                command.append('-f')
            run(*command, '-o', 'ashift=12', '-o', 'cachefile=/etc/zfs/zpool.cache',
                '-O', 'compression=lz4', '-O', 'atime=off', '-O', 'mountpoint='+args.mountpoint,
                args.pool, args.device)
            print('changed: created pool')
    status = run('zpool', 'status', '-LP', args.pool).stdout
    require(device in status.split(), 'Existing pool does not contain the configured device')
    actual = run('zfs', 'get', '-H', '-o', 'value', 'mountpoint', args.pool).stdout.strip()
    require(actual == args.mountpoint, 'Existing pool mountpoint differs; inspect manually')
    if run('zfs', 'get', '-H', '-o', 'value', 'mounted', args.pool).stdout.strip() != 'yes':
        run('zfs', 'mount', args.pool)
        print('changed: mounted pool')
    run('zpool', 'set', 'cachefile=/etc/zfs/zpool.cache', args.pool)
    # Remove legacy ext4 fstab entry only after successful creation/import.
    with open('/etc/fstab') as stream:
        old = stream.readlines()
    new = [line for line in old if line.lstrip().startswith('#') or len(line.split()) < 2 or line.split()[1] != args.mountpoint]
    if new != old:
        if not os.path.exists('/etc/fstab.k3spi-before-zfs'):
            with open('/etc/fstab.k3spi-before-zfs', 'x') as backup:
                backup.writelines(old)
        with open('/etc/fstab', 'w') as stream:
            stream.writelines(new)
        print('changed: removed legacy fstab entry')


if __name__ == '__main__':
    main()
