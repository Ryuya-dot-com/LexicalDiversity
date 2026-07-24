# syntax=docker/dockerfile:1.7

# The default is the reviewed linux/amd64 child manifest, not the mutable tag or
# multi-platform index.  An override remains fail-closed and is an explicit
# release-contract change.
ARG PYTHON_IMAGE=python:3.12.10-slim-bookworm@sha256:97983fa8cc88343512862c62307159a82261c3528dc025f79e5a3f7af43e50b4
FROM --platform=linux/amd64 ${PYTHON_IMAGE}

ARG PYTHON_IMAGE
RUN set -eu; \
    case "${PYTHON_IMAGE}" in \
      *python:*@sha256:*) ;; \
      *) echo "PYTHON_IMAGE must name a Python base pinned by sha256 digest" >&2; exit 64 ;; \
    esac; \
    ldfreq_base_digest="${PYTHON_IMAGE##*@sha256:}"; \
    test "${#ldfreq_base_digest}" -eq 64; \
    case "${ldfreq_base_digest}" in \
      *[!0-9a-f]*) echo "PYTHON_IMAGE digest must be 64 lowercase hex characters" >&2; exit 64 ;; \
    esac

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LDFREQ_RUNTIME_ROOT=/tmp/ldfreq \
    XDG_CACHE_HOME=/tmp/ldfreq/cache \
    XDG_CONFIG_HOME=/tmp/ldfreq/config \
    TMPDIR=/tmp/ldfreq/tmp

WORKDIR /opt/ldfreq

COPY --chown=0:0 deploy/cloud-run/requirements-prod.txt /opt/ldfreq/requirements-prod.txt
COPY --chown=0:0 deploy/cloud-run/requirements-prod-linux-x86_64.lock /opt/ldfreq/requirements-prod-linux-x86_64.lock
RUN python -m pip install --no-cache-dir --no-deps --only-binary=:all: \
      --require-hashes --requirement /opt/ldfreq/requirements-prod-linux-x86_64.lock

RUN groupadd --gid 10001 ldfreq \
    && useradd --uid 10001 --gid 10001 --no-create-home \
         --home-dir /nonexistent --shell /usr/sbin/nologin ldfreq \
    && install -d -o 10001 -g 10001 -m 0700 \
         /tmp/ldfreq /tmp/ldfreq/tmp /tmp/ldfreq/cache /tmp/ldfreq/config \
    && chmod 0555 /tmp /var/tmp

# Only reviewed runtime code, configuration, and redistributable public
# artifacts enter the image. Nation resources are supplied at runtime by the
# read-only /mnt/nation volume.
COPY --chown=0:0 app.py LICENSE /opt/ldfreq/
COPY --chown=0:0 ldfreq/ /opt/ldfreq/ldfreq/
COPY --chown=0:0 pages/ /opt/ldfreq/pages/
COPY --chown=0:0 .streamlit/config.toml /opt/ldfreq/.streamlit/config.toml
COPY --chown=0:0 data/resource_registry.json /opt/ldfreq/data/resource_registry.json
COPY --chown=0:0 data/NJ8/ /opt/ldfreq/data/NJ8/
COPY --chown=0:0 data/ngsl/ /opt/ldfreq/data/ngsl/
COPY --chown=0:0 data/open/ /opt/ldfreq/data/open/
COPY --chown=0:0 deploy/cloud-run/entrypoint.sh /usr/local/bin/ldfreq-entrypoint

RUN chmod 0555 /usr/local/bin/ldfreq-entrypoint \
    && chmod -R a-w /opt/ldfreq

EXPOSE 8080
USER 10001:10001
ENTRYPOINT ["/usr/local/bin/ldfreq-entrypoint"]
