#!/usr/bin/env python3
"""Dynamic inventory generated from .env/hosts; the first node is the server."""
import ipaddress
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOSTS_FILE = Path(os.environ.get("K3SPI_HOSTS_FILE", ROOT / ".env/hosts"))
KEY_FILE = Path(os.environ.get("K3SPI_SSH_KEY", ROOT / ".env/id_k3spi"))


def load_vars():
    # Parse the two simple scalar overrides without making inventory depend on PyYAML.
    private_vars = ROOT / ".env/group_vars.yml"
    values = {}
    if private_vars.is_file():
        for raw in private_vars.read_text().splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" in line and not line.startswith(("-", " ")):
                key, value = line.split(":", 1)
                values[key.strip()] = value.strip().strip("'\"")
    network = ipaddress.ip_network(os.environ.get(
        "K3SPI_INTERNAL_CIDR", values.get("internal_network_cidr", "10.10.10.0/24")))
    first = int(os.environ.get(
        "K3SPI_INTERNAL_FIRST_HOST", values.get("internal_network_first_host", "10")))
    return network, first


def main():
    if not HOSTS_FILE.is_file():
        raise SystemExit(f"Missing {HOSTS_FILE}; copy .env/hosts.example and edit it")
    entries = []
    for number, raw in enumerate(HOSTS_FILE.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) != 2:
            raise SystemExit(f"{HOSTS_FILE}:{number}: expected '<ip> <hostname>'")
        ipaddress.ip_address(fields[0])
        entries.append(tuple(fields))
    if not entries:
        raise SystemExit(f"{HOSTS_FILE} contains no hosts")
    network, first = load_vars()
    if first + len(entries) >= network.num_addresses - 1:
        raise SystemExit("Internal subnet is too small for the inventory")
    hostvars = {}
    names = []
    for index, (address, name) in enumerate(entries):
        names.append(name)
        hostvars[name] = {
            "ansible_host": address,
            "ansible_user": os.environ.get("K3SPI_SSH_USER", "pi"),
            "ansible_ssh_private_key_file": str(KEY_FILE),
            "internal_ip": str(network.network_address + first + index),
            "node_index": index,
        }
    print(json.dumps({
        "all": {"children": ["control_plane", "workers"]},
        "control_plane": {"hosts": names[:1]},
        "workers": {"hosts": names[1:]},
        "k3s_cluster": {"hosts": names},
        "_meta": {"hostvars": hostvars},
    }))


if __name__ == "__main__":
    main()
