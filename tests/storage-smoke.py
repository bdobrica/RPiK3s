#!/usr/bin/env python3
"""Run on the control-plane node with sudo. Creates and removes test-only objects.
Checks local provisioning on each SSD node, NFS rescheduling/expansion, and
snapshot restore for each driver. On failure, leaves resources for inspection.
"""
import json
import subprocess
import time

PREFIX = 'storage-smoke-' + str(int(time.time()))
NAMESPACE = PREFIX


def kubectl(*args):
    return subprocess.check_output(['k3s', 'kubectl', *args], text=True).strip()


def apply(obj):
    subprocess.run(['k3s', 'kubectl', 'apply', '-f', '-'], input=json.dumps(obj), text=True, check=True, stdout=subprocess.DEVNULL)


def get(kind, name):
    return json.loads(kubectl('-n', NAMESPACE, 'get', kind, name, '-o', 'json'))


def wait_value(kind, name, path, expected, timeout=240):
    kubectl('-n', NAMESPACE, 'wait', '--for=jsonpath='+path+'='+expected, kind+'/'+name, '--timeout='+str(timeout)+'s')


def claim(name, mode, snapshot=None, source_pvc=None):
    spec = {'storageClassName': PREFIX+'-'+mode, 'accessModes': ['ReadWriteOnce'] if mode == 'local' else ['ReadWriteMany'], 'resources': {'requests': {'storage': '64Mi'}}}
    if snapshot:
        spec['dataSource'] = {'name': snapshot, 'kind': 'VolumeSnapshot', 'apiGroup': 'snapshot.storage.k8s.io'}
    if source_pvc:
        spec['dataSource'] = {'name': source_pvc, 'kind': 'PersistentVolumeClaim', 'apiGroup': ''}
    apply({'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim', 'metadata': {'name': name, 'namespace': NAMESPACE}, 'spec': spec})


def pod(name, pvc, node):
    apply({'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': name, 'namespace': NAMESPACE}, 'spec': {
        'nodeSelector': {'kubernetes.io/hostname': node},
        'terminationGracePeriodSeconds': 1,
        'containers': [{'name': 'test', 'image': 'busybox:1.37.0', 'command': ['sh', '-c', 'sleep 3600'], 'volumeMounts': [{'name': 'data', 'mountPath': '/data'}]}],
        'volumes': [{'name': 'data', 'persistentVolumeClaim': {'claimName': pvc}}]}})
    kubectl('-n', NAMESPACE, 'wait', '--for=condition=Ready', 'pod/'+name, '--timeout=240s')


def exec_pod(name, command):
    return kubectl('-n', NAMESPACE, 'exec', name, '--', 'sh', '-c', command)


def delete_pod(name):
    kubectl('-n', NAMESPACE, 'delete', 'pod', name, '--wait=true', '--timeout=90s')


def main():
    nodes = json.loads(kubectl('get', 'nodes', '-l', 'storage.k3spi.io/zfs=true', '-o', 'json'))['items']
    nodes = sorted(n['metadata']['name'] for n in nodes)
    assert nodes, 'No ZFS nodes'
    apply({'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': NAMESPACE}})
    classes = []
    for mode in ['local', 'nfs']:
        source = json.loads(kubectl('get', 'storageclass', 'zfs-'+mode, '-o', 'json'))
        source['metadata'] = {'name': PREFIX+'-'+mode}
        source['reclaimPolicy'] = 'Delete'
        apply(source)
        classes.append(source['metadata']['name'])
        apply({'apiVersion': 'snapshot.storage.k8s.io/v1', 'kind': 'VolumeSnapshotClass',
               'metadata': {'name': PREFIX+'-'+mode}, 'driver': source['provisioner'], 'deletionPolicy': 'Delete'})

    for index, node in enumerate(nodes):
        name = 'local-'+str(index)
        claim(name, 'local')
        pod(name, name, node)
        exec_pod(name, 'echo '+PREFIX+' > /data/marker; sync')
        assert exec_pod(name, 'cat /data/marker') == PREFIX
        pv_name = get('pvc', name)['spec']['volumeName']
        pv = json.loads(kubectl('get', 'pv', pv_name, '-o', 'json'))
        terms = pv['spec']['nodeAffinity']['required']['nodeSelectorTerms']
        assert any(expr['key'] == 'org.democratic-csi.topology/node' and expr['values'] == [node]
                   for term in terms for expr in term['matchExpressions']), 'Local volume must be pinned to its SSD node'
        print('PASS local provisioning/read-write and node affinity on', node, flush=True)

    claim('nfs', 'nfs')
    pod('nfs-writer', 'nfs', nodes[0])
    exec_pod('nfs-writer', 'echo '+PREFIX+' > /data/marker; sync')
    delete_pod('nfs-writer')
    pod('nfs-reader', 'nfs', nodes[-1])
    assert exec_pod('nfs-reader', 'cat /data/marker') == PREFIX
    print('PASS NFS rescheduling between', nodes[0], 'and', nodes[-1], flush=True)

    for mode, source, node in [('local', 'local-0', nodes[0]), ('nfs', 'nfs', nodes[-1])]:
        snap = mode+'-snapshot'
        apply({'apiVersion': 'snapshot.storage.k8s.io/v1', 'kind': 'VolumeSnapshot',
               'metadata': {'name': snap, 'namespace': NAMESPACE},
               'spec': {'volumeSnapshotClassName': PREFIX+'-'+mode, 'source': {'persistentVolumeClaimName': source}}})
        wait_value('volumesnapshot', snap, '{.status.readyToUse}', 'true')
        claim(mode+'-restored', mode, snap)
        pod(mode+'-restored', mode+'-restored', node)
        assert exec_pod(mode+'-restored', 'cat /data/marker') == PREFIX
        print('PASS', mode, 'snapshot and restored contents', flush=True)

    claim('nfs-clone', 'nfs', source_pvc='nfs')
    pod('nfs-clone', 'nfs-clone', nodes[-1])
    assert exec_pod('nfs-clone', 'cat /data/marker') == PREFIX
    print('PASS NFS PVC clone', flush=True)

    kubectl('-n', NAMESPACE, 'patch', 'pvc', 'nfs', '--type=merge', '-p', json.dumps({'spec': {'resources': {'requests': {'storage': '128Mi'}}}}))
    wait_value('pvc', 'nfs', '{.status.capacity.storage}', '128Mi')
    print('PASS NFS expansion', flush=True)
    # Remove restored clones before deleting snapshots and source volumes.
    kubectl('-n', NAMESPACE, 'delete', 'pods', '--all', '--wait=true', '--timeout=120s')
    kubectl('-n', NAMESPACE, 'delete', 'pvc', 'nfs-clone', '--wait=true')
    for mode in ['local', 'nfs']:
        kubectl('-n', NAMESPACE, 'delete', 'pvc', mode+'-restored', '--wait=true')
    kubectl('-n', NAMESPACE, 'delete', 'volumesnapshot', '--all', '--wait=true', '--timeout=120s')
    kubectl('delete', 'namespace', NAMESPACE, '--wait=true', '--timeout=180s')
    for name in classes:
        kubectl('delete', 'storageclass', name)
        kubectl('delete', 'volumesnapshotclass', name)
    print('PASS cleanup; test namespace:', NAMESPACE, flush=True)


if __name__ == '__main__':
    print('Test namespace:', NAMESPACE, flush=True)
    main()
