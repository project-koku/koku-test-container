FROM registry.access.redhat.com/ubi9-minimal:9.4-1227.1726694542

RUN microdnf install -y \
    jq \
    python3.11 \
    && rm -rf /var/cache/yum/*

COPY files/bin /usr/local/bin/
COPY requirements/requirements.txt /usr/share/container-setup/requirements.txt


ENV VENV=/opt/venvs/koku-test
ENV PYTHON="${VENV}/bin/python"
ENV PATH="${VENV}/bin:$PATH"

RUN python3.11 -m venv "$VENV" \
    && "$PYTHON" -m pip install -U pip setuptools \
    && "$PYTHON" -m pip install -r /usr/share/container-setup/requirements.txt

COPY files/install-tools.py /usr/share/container-setup/
RUN /usr/share/container-setup/install-tools.py

RUN useradd -r -m koku-test

USER koku-test
RUN mkdir -p /home/koku-test/.config/bonfire

WORKDIR /home/koku-test
