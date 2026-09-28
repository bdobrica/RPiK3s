SHELL := /bin/bash
VENV_BIN := $(HOME)/.venvs/py-ansible/bin
ANSIBLE_PLAYBOOK ?= $(if $(wildcard $(VENV_BIN)/ansible-playbook),$(VENV_BIN)/ansible-playbook,ansible-playbook)
ANSIBLE ?= $(if $(wildcard $(VENV_BIN)/ansible),$(VENV_BIN)/ansible,ansible)
ANSIBLE_GALAXY ?= $(if $(wildcard $(VENV_BIN)/ansible-galaxy),$(VENV_BIN)/ansible-galaxy,ansible-galaxy)
export ANSIBLE_CONFIG := $(CURDIR)/ansible.cfg
USB_FORMAT_ARG := $(if $(filter true yes 1,$(USB_ALLOW_FORMAT)),-e usb_allow_format=true,)
SSD_FORMAT_ARGS := $(if $(filter true yes 1,$(SSD_ALLOW_FORMAT)),-e ssd_allow_format=true,) $(if $(filter true yes 1,$(SSD_FORCE_FORMAT)),-e ssd_force_format=true,)
USB_FORCE_FORMAT_ARG := $(if $(filter true yes 1,$(USB_FORCE_FORMAT)),-e usb_force_format=true,)

.PHONY: help check deps ping prepare k3s addons storage install status kubeconfig

help:
	@echo "Targets: check deps ping prepare k3s addons storage install status kubeconfig"

check:
	@test -f .env/hosts || (echo "Missing .env/hosts"; exit 1)
	@test -f .env/id_k3spi || (echo "Missing .env/id_k3spi"; exit 1)
	@chmod 600 .env/id_k3spi
	@inventory/hosts.py --list >/dev/null
	@$(ANSIBLE_PLAYBOOK) playbooks/site.yml --syntax-check

deps:
	$(ANSIBLE_GALAXY) collection install -r requirements.yml

ping: check
	$(ANSIBLE) k3s_cluster -m ping

prepare: check
	$(ANSIBLE_PLAYBOOK) playbooks/prepare.yml $(USB_FORMAT_ARG) $(USB_FORCE_FORMAT_ARG)

k3s: check
	$(ANSIBLE_PLAYBOOK) playbooks/k3s.yml

storage: check
	$(ANSIBLE_PLAYBOOK) playbooks/storage.yml $(SSD_FORMAT_ARGS)

addons: check
	$(ANSIBLE_PLAYBOOK) playbooks/addons.yml

install: check
	$(ANSIBLE_PLAYBOOK) playbooks/site.yml $(USB_FORMAT_ARG) $(USB_FORCE_FORMAT_ARG) $(SSD_FORMAT_ARGS)

status:
	$(ANSIBLE_PLAYBOOK) playbooks/status.yml

kubeconfig:
	$(ANSIBLE_PLAYBOOK) playbooks/kubeconfig.yml
	@echo "Wrote .env/kubeconfig.yaml"
