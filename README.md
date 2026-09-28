# Reusable Debian k3s homelab

An idempotent Ansible setup for a small, non-HA k3s cluster. The first entry in
`.env/hosts` is the server/control plane and every later entry is an agent.

It prepares Debian-family nodes, configures an isolated Ethernet network, mounts
one USB filesystem at `/mnt/usb`, creates per-node SSH keys, installs k3s, and
adds NGINX Ingress Controller, cert-manager, Sealed Secrets, Flux, Prometheus
Operator, one small Prometheus instance, ServiceMonitor support, and Grafana.

## Prerequisites

- Control machine: Python 3, Ansible Core 2.16+, `make`, and network access.
- Nodes: Debian/Raspberry Pi OS 64-bit, `pi` with passwordless sudo, wireless
  management access, an unmounted USB partition with an existing filesystem,
  and `eth0` connected to the private switch.
- The private Ethernet subnet must not overlap your LAN or k3s defaults
  (`10.42.0.0/16` pods and `10.43.0.0/16` services). Default: `10.10.10.0/24`.

## Configure and install

```sh
cp .env/hosts.example .env/hosts
cp .env/group_vars.example.yml .env/group_vars.yml
# Put the management SSH private key at .env/id_k3spi
make deps
make ping
make install
make status
make kubeconfig
```

If `~/.venvs/py-ansible` exists, the Makefile uses it automatically; otherwise
it uses Ansible from `PATH`.

Run phases independently with `make prepare`, `make k3s`, and `make addons`.
The playbooks are intended to be rerun after configuration changes.

The inventory format is `management_ip hostname`, one node per line. Override
the inventory, SSH user/key, or internal addressing with `K3SPI_HOSTS_FILE`,
`K3SPI_SSH_USER`, `K3SPI_SSH_KEY`, `K3SPI_INTERNAL_CIDR`, and
`K3SPI_INTERNAL_FIRST_HOST`.

## USB safety

Auto-detection succeeds only when exactly one unmounted partition belongs to a
USB transport device. It will not format a disk. If detection is ambiguous, set
`usb_device` to a persistent `/dev/disk/by-id/...-partN` path. Formatting a blank
device requires the explicit `usb_allow_format: true` opt-in and erases it.

For a one-time opt-in, either command form is supported:

```sh
make prepare USB_ALLOW_FORMAT=true
USB_ALLOW_FORMAT=true make install
```

The switch only permits formatting when the selected USB partition has no
filesystem. It does not reformat a partition that already has a filesystem.

To deliberately erase an existing filesystem and convert it to ext4 once:

```sh
make prepare USB_FORCE_FORMAT=true
```

The created `.k3spi-initialized` marker prevents a rerun with the same switch
from formatting the managed volume again.

The k3s local-path provisioner stores new local volumes under
`/mnt/usb/k3s-storage`. These volumes remain node-local and are not replicated.

## Optional ZFS SSDs and CSI storage

Configure SSD partitions and enable node-local ZFS, shared ZFS-backed NFS, or
both with `make storage`. Includes CSI snapshots and restore support. See
[storage setup, safety, and limitations](docs/storage.md). Existing USB local-path
volumes remain in place. NFS supports workload rescheduling across nodes; neither
mode replicates data between SSDs.

## Networking

The automation assigns consecutive static `eth0` addresses and adds every node
to `/etc/hosts`. There is deliberately no gateway or DNS on this link, so Wi-Fi
remains the management/default route. k3s API and Flannel use `eth0`.

Each node creates its own `~/.ssh/id_ed25519_cluster`; every public key is added
to every node. Private keys never leave their source node.

## Add-ons and secrets

Versions live in `group_vars/all.yml` and can be overridden in the ignored
`.env/group_vars.yml`. Upstream manifests are pinned. This project uses the
maintained F5 NGINX Ingress Controller, not the retired Kubernetes community
`ingress-nginx`. Its DaemonSet binds ports 80/443 directly on every node.

No secret is needed before installation. Automation generates the Grafana admin
password in ignored `.env/grafana-admin-password` and copies it to a Kubernetes
Secret. Retrieve the in-cluster value with:

```sh
KUBECONFIG=.env/kubeconfig.yaml kubectl -n monitoring get secret \
  grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d
```

The provided service is internal only; use port-forwarding or create an Ingress.
For a long-lived deployment, migrate this generated Secret to a SealedSecret.

Flux controllers are installed, but repository bootstrap is intentionally not
performed because that would mutate a Git provider and requires repository
details/credentials. After publishing your repository, use `flux bootstrap`
for your provider and keep its token outside Git. Back up the Sealed Secrets
controller key before relying on encrypted secrets.

## Design limits

This is a single-server development cluster. It has no control-plane HA,
distributed storage, or Prometheus/Grafana HA. Prometheus stops retaining data
at roughly 1 GB and requests a 2 GiB local PVC. The operator is included because
`ServiceMonitor` is an operator CRD; add ServiceMonitor resources for workloads
you want scraped.
