# The Ansible executor.
#
# The only workload permitted to reach the management network, and the only
# place ansible-playbook runs. It is not the runner: the runner exists to
# execute model-authored code and its egress to every RFC1918 range is denied,
# which is the property that makes running that code acceptable at all. Putting
# Ansible there would mean opening it. See ADR 0030.
#
# Nothing installs at runtime. Everything is pinned here and verified in the
# build, because a container that must reach an index to do its job is one whose
# egress rules can never be tightened.
#
# Build from the repository root:
#   docker build -f executor/Dockerfile -t rezztor/eagent-ansible:latest \
#     --build-arg CATALOGUE_COMMIT="$(git rev-parse HEAD)" .
FROM debian:stable-slim

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    EXECUTOR_USER=ansible \
    EXECUTOR_UID=1000 \
    VENV=/opt/ansible-venv \
    ANSIBLE_COLLECTIONS_PATH=/usr/share/ansible/collections \
    REZZTOR_ANSIBLE_PORT=8500

# The catalogue this image ships is pinned to one commit, and the API refuses a
# job drafted against a different one. An image built without it says `unpinned`
# out loud rather than skipping the check quietly.
ARG CATALOGUE_COMMIT=""
ENV REZZTOR_ANSIBLE_CATALOGUE_COMMIT=${CATALOGUE_COMMIT}

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip ca-certificates openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Build toolchain for the wheels that need one, removed in the same layer so it
# is not in the image that reaches production infrastructure.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libssl-dev libffi-dev libssh-dev python3-dev \
    && python3 -m venv ${VENV} \
    && ${VENV}/bin/pip install --no-cache-dir --upgrade pip \
    && ${VENV}/bin/pip install --no-cache-dir \
        ansible-pylibssh==1.4.0 \
        paramiko==3.5.1 \
        requests==2.32.3 \
        jmespath==1.0.1 \
        netaddr==1.3.0 \
    && apt-get purge -y build-essential python3-dev libssl-dev libffi-dev \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY executor/requirements.txt /tmp/requirements.txt
RUN ${VENV}/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# The estate, and only the estate. Every version is the current release at
# 2026-09-09 and every one declares requires_ansible >= 2.16; SONiC needs 2.17,
# which is why ansible-core is pinned at 2.18.4 rather than lower.
#
# cisco.nxos 13.0.0, arista.eos 12.3.0, junipernetworks.junos 11.1.1 and
# juniper.device 2.0.6 are the current releases at 2026-10-02, read from Galaxy. Since
# 11.x junipernetworks.junos ships only the cliconf and terminal plugins and redirects
# every module to juniper.device (which is why that is pinned too); its junos_command
# runs over network_cli with nothing but ansible.netcommon, where PyEZ is only needed
# for netconf and `ofacts`, so none is installed. None of the three has met a device.
RUN mkdir -p ${ANSIBLE_COLLECTIONS_PATH} \
    && ${VENV}/bin/ansible-galaxy collection install \
        --collections-path ${ANSIBLE_COLLECTIONS_PATH} \
        ansible.netcommon:==8.6.2 \
        ansible.utils:==6.1.0 \
        cisco.ios:==11.5.1 \
        cisco.nxos:==13.0.0 \
        arista.eos:==12.3.0 \
        junipernetworks.junos:==11.1.1 \
        juniper.device:==2.0.6 \
        dellemc.enterprise_sonic:==4.1.0 \
        fortinet.fortios:==2.6.0 \
        fortinet.fortimanager:==2.15.0 \
        cisco.fmcansible:==1.1.1 \
    && chmod -R 0755 ${ANSIBLE_COLLECTIONS_PATH}

ENV PATH="${VENV}/bin:${PATH}"

# The API, the catalogue and the playbooks. `helpers/` comes across so the
# executor validates jobs with the same code the application builds them with;
# two implementations would drift, and the gap would be the way in.
WORKDIR /app

# The executor is a package, not a script. It was one file until it grew a job
# store, a scheduler, a credential store and a console, and copying just app.py
# left every `from executor import ...` unresolvable inside the image -- which
# the build check below caught, which is what it is for.
#
# PYTHONPATH rather than relying on the script's own directory: `python
# /app/executor/app.py` would put /app/executor on sys.path, not /app, so the
# package could not import itself. It also covers `ea-ansible`, which shells out
# to python from wherever the operator happens to be standing.
ENV PYTHONPATH=/app
COPY executor/*.py /app/executor/
COPY plugins/_ea_ansible/__init__.py /app/plugins/_ea_ansible/__init__.py
COPY plugins/_ea_ansible/helpers/ /app/plugins/_ea_ansible/helpers/
COPY executor/templates/ /app/executor/templates/
COPY executor/playbooks/ /app/executor/playbooks/
COPY plugins/_ea_ansible/catalogue.yaml /app/plugins/_ea_ansible/catalogue.yaml
COPY plugins/_ea_ansible/playbooks/ /app/plugins/_ea_ansible/playbooks/

# The shell wrapper, for an engineer who has exec'd into this container. It is a
# convenience, not a control -- see executor/cli.py.
COPY executor/ea-ansible /usr/local/bin/ea-ansible
RUN chmod 0755 /usr/local/bin/ea-ansible

# Read the result back rather than trusting an exit status: a collection that
# installed into the wrong path still exits 0, and the failure would not appear
# until a play could not find a module.
RUN set -eux; \
    ansible --version; \
    ansible-galaxy collection list --collections-path ${ANSIBLE_COLLECTIONS_PATH} \
        | grep -q 'cisco.ios'; \
    ansible-galaxy collection list --collections-path ${ANSIBLE_COLLECTIONS_PATH} \
        | grep -q 'dellemc.enterprise_sonic'; \
    ansible-doc cisco.ios.ios_vlans >/dev/null; \
    ansible-doc cisco.ios.ios_interfaces >/dev/null; \
    ansible-doc dellemc.enterprise_sonic.sonic_vlans >/dev/null; \
    ansible-doc cisco.nxos.nxos_command >/dev/null; \
    ansible-doc arista.eos.eos_command >/dev/null; \
    ansible-doc juniper.device.junos_command >/dev/null; \
    python -c "import executor.app; print('api ok'); import ansible_runner; print('runner ok')"; \
    python -c "import executor.console; print('console ok')"; \
    python -c "import executor.cli; print('cli ok')"; \
    ea-ansible list; \
    python -c "from plugins._ea_ansible.helpers import catalogue; print('catalogue', sorted(catalogue.load()))"

RUN useradd --uid ${EXECUTOR_UID} --create-home --shell /usr/sbin/nologin ${EXECUTOR_USER}

# The job history lives here, on the one volume this container writes to. It is
# created and chowned in the image rather than left to the volume driver: a
# bind mount arrives owned by root, and the executor has dropped every
# capability by the time it would need to fix that.
RUN mkdir -p /var/lib/eagent-ansible \
    && chown ${EXECUTOR_UID}:${EXECUTOR_UID} /var/lib/eagent-ansible
VOLUME ["/var/lib/eagent-ansible"]

# Where the executor's socket lives (ADR 0051). A named volume mounted here is
# seeded with this directory's owner, which is what lets the unprivileged executor
# create the socket in it.
RUN mkdir -p /run/ansible-socket \
    && chown ${EXECUTOR_UID}:${EXECUTOR_UID} /run/ansible-socket \
    && chmod 0750 /run/ansible-socket

USER ${EXECUTOR_USER}

EXPOSE 8500
# The break-glass console, when REZZTOR_ANSIBLE_PUBLIC_PORT is set. Declaring the
# port does not publish it: the compose file leaves it unmapped by default.
EXPOSE 8600
# No shell form: the container runs one process and stops when it stops.
CMD ["python", "-c", "from executor.app import main; main()"]
