# ZFS SSD storage and democratic-csi

Run `make storage` independently of OS preparation and existing add-ons. A full
`make install` also includes storage. Storage is disabled by default; configure
`.env/group_vars.yml` using the example file.

| Mode | StorageClass | Placement | Expansion | Snapshots / restore |
|---|---|---|---|---|
| `local` | `zfs-local` | Any selected SSD node, volume stays on its node | Not enabled upstream for distributed local driver | Yes, on the original node |
| `nfs` | `zfs-nfs` | Workloads on any node; data on selected NFS server | Yes | Yes |

Enable either mode or both with `democratic_csi_modes: [local, nfs]`. Each selected
node has its own independent ZFS pool; this is **not distributed or replicated
storage**, and free capacity is not combined into a single volume. Nodes without
SSDs can consume NFS volumes. Local volumes use delayed binding and node topology;
a workload using one cannot move to another node while retaining access to it.

NFS allows a workload to be rescheduled onto another compute node with the same
PVC, including ReadWriteMany access. This does not migrate data between SSDs.
The selected storage node and its SSD remain a single point of failure. Moving
the NFS backend requires a planned ZFS send/receive and endpoint migration, or an
application-level copy to a new PVC. CSI snapshots are on the same pool and are
not backups. Existing local-path PVCs are not automatically migrated.

## SSD preparation

Use `lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,TRAN,MODEL` and
`ls -l /dev/disk/by-id/usb-*` on each node. Select an existing SSD **partition**,
not the boot disk, USB stick, or a whole disk. Partition a new unpartitioned SSD
manually first. No SSD is selected automatically, because USB transport cannot
reliably distinguish an SSD from the existing USB storage stick.

```yaml
ssd_devices:
  k3spi-00: /dev/disk/by-id/usb-YOUR_SSD_SERIAL-part1
  k3spi-01: /dev/disk/by-id/usb-ANOTHER_SSD_SERIAL-part1
democratic_csi_modes: [local, nfs]
democratic_csi_nfs_host: k3spi-00
```

For a blank partition: `make storage SSD_ALLOW_FORMAT=true`.
To erase a selected partition with an existing filesystem:
`make storage SSD_FORCE_FORMAT=true`. These flags are one-shot; keep their YAML
settings false. Only explicitly listed partitions are eligible. Unmount nested
filesystems first; busy mounts fail instead of being forcibly detached. Container
bind mounts (including Kata host mounts) can retain an old filesystem in another
mount namespace and must also be released before conversion. There is
no in-place ext4-to-ZFS conversion. Back up any wanted data before formatting.

The role installs headers for the running kernel, ZFS DKMS and utilities, then
loads the ZFS module before touching disks. Debian package sources must provide
compatible ZFS packages (normally `contrib`; Raspberry Pi repositories can also
provide them). An unsupported kernel or missing headers stops preparation. Use
64-bit Raspberry Pi OS / Debian. USB bridges must provide reliable flushes and
stable device identifiers; keep adequate power and cooling.

Pools use compression=lz4, atime=off and ashift=12, with the root mounted at
`/mnt/ssd`. A successful conversion removes its old fstab entry and backs up
fstab. ZFS imports and mounts through systemd at boot. Reruns verify the configured
device belongs to the existing pool and never recreate it, even with the format
flag. Existing ZFS members that cannot be imported are never overwritten.

## Driver and NFS service

The k3s Helm controller installs pinned democratic-csi releases. Snapshot CRDs
and the upstream snapshot controller are installed first. Local mode enables
distributed snapshot routing and grants the controller read access to nodes. Parents are separate:
`ssd/local`, `ssd/local-snapshots`, `ssd/nfs`, and `ssd/nfs-snapshots`.

The NFS driver creates and exports one child ZFS dataset per PVC below
`/mnt/ssd/nfs`; the whole `/mnt/ssd` tree is deliberately not a blanket export.
It uses NFSv4.1, ZFS quotas, and exports restricted to the private node subnet.
`no_root_squash` and dataset mode 0777 support container ownership changes: this
assumes trusted cluster nodes and a private storage network. Do not expose NFS
or the CSI SSH account outside that network. The dedicated SSH account has
passwordless sudo for ZFS and ownership commands; treat its key as privileged.

The private key stays in ignored `.env/democratic-csi` and a Kubernetes Secret,
not in Helm values or Git. A configuration checksum rolls the NFS driver pods
when its configuration or credentials change. Back the key up securely. K3s Secret encryption at rest and
restricted Kubernetes RBAC are recommended. Removing a mode from configuration
does not uninstall its release or delete data: migrate workloads and explicitly
uninstall the HelmChart only when it is safe. Likewise, removing a node from
`ssd_devices` does not erase its pool or evict existing volumes; remove its
`storage.k3spi.io/zfs` label manually after migrating workloads.

Neither new StorageClass becomes default. Both PVC reclaim and snapshot deletion
policies are `Retain`, so deleted claims/snapshots require deliberate storage
cleanup. Do not delete retained datasets until their data is no longer needed.
Choose `storageClassName: zfs-nfs` or `zfs-local` in workload claims. See
[example claims and snapshots](../examples/storage/README.md).

## Validation and recovery

```sh
kubectl -n democratic-csi get pods
kubectl get storageclass,volumesnapshotclass
kubectl get volumesnapshot -A
sudo zpool status
sudo zfs list
```

Validate a snapshot with a restored PVC and a checksum, not only `readyToUse`.
For NFS rescheduling, write data on one compute node and read the same PVC on
another. Reboot nodes one at a time during a maintenance window to check pool
import; inspect `zpool status`, `findmnt /mnt/ssd` and CSI pods afterward. An NFS
server outage stalls all NFS-backed workloads even when compute nodes are healthy.

Upstream references: [driver examples](https://github.com/democratic-csi/democratic-csi/tree/master/examples),
[chart examples](https://github.com/democratic-csi/charts/tree/master/stable/democratic-csi/examples),
[snapshot controller](https://github.com/kubernetes-csi/external-snapshotter).

The repository includes `tests/storage-smoke.py` for an installed cluster with
both modes enabled. Run it on the control-plane node using `sudo python3`; it
creates uniquely named test classes and a namespace, validates both drivers,
and deletes test resources on success. Failures deliberately leave resources
for inspection. Those temporary test classes use `Delete` policies, unlike the
production classes. Disk safety checks run locally with
`python3 -m unittest discover -s tests -v`.

## Homelab validation

Validated on four ARM64 Debian 13 Raspberry Pi nodes with k3s v1.36.4+k3s1,
OpenZFS 2.4.4, democratic-csi v1.9.5, and chart 0.15.1. The live smoke test
passed local provisioning/read-write and node-affinity checks on all four nodes,
NFS access after moving a pod between nodes, snapshot restore with matching
contents for both drivers, NFS PVC cloning, and NFS expansion. Test resources
were removed. A storage playbook rerun verified existing pools without format
permission. Boot import/mount services are enabled; a reboot test was not run.
