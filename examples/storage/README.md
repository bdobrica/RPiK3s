# Example volume, snapshot and restore

Apply `pvc.yaml`, mount `example-data` into a workload, and write test data.
Quiesce application writes for an application-consistent snapshot (otherwise
snapshots are crash-consistent).

```sh
kubectl apply -f examples/storage/pvc.yaml
# Mount the claim and write data before continuing.
kubectl apply -f examples/storage/snapshot.yaml
kubectl wait --for=jsonpath='{.status.readyToUse}'=true \
  volumesnapshot/example-data-snapshot --timeout=120s
kubectl apply -f examples/storage/restore.yaml
```

Mount `example-data-restored` and verify its contents. For local ZFS use
`zfs-local` in both class fields and `ReadWriteOnce` access mode; provisioning
waits for a consumer Pod, and restored data remains on its source node.
Retain policies mean deleting these objects does not erase their underlying
volumes and snapshots. Clean those up deliberately after testing.

`clone.yaml` creates a separate NFS PVC directly from `example-data` in the same
namespace. Quiesce writes before cloning when application consistency matters.
To expand an NFS claim, increase its requested storage size; local ZFS expansion
is disabled because the upstream distributed local driver does not support it.
