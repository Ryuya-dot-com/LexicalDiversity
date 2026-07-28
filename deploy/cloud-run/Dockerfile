# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e

# The default is the reviewed linux/amd64 child manifest, not the mutable tag or
# multi-platform index.  An override remains fail-closed and is an explicit
# release-contract change.
ARG SOURCE_DATE_EPOCH
ARG PYTHON_IMAGE=python:3.12.13-alpine3.23@sha256:efc8538b7449b6d893de5d852c87a0dc2cffd0ec27b07dd98ba3e7edaadc26af
FROM --platform=linux/amd64 ${PYTHON_IMAGE} AS application

# The global argument drives BuildKit's exporter. Redeclare it in the stage so
# pip's bytecode compiler uses deterministic hash-based invalidation as well.
ARG SOURCE_DATE_EPOCH
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
COPY --chown=0:0 deploy/cloud-run/requirements-watchdog-pure-linux-x86_64.lock /opt/ldfreq/requirements-watchdog-pure-linux-x86_64.lock
COPY --chown=0:0 scripts/check_pure_watchdog_wheel.py /opt/ldfreq/check_pure_watchdog_wheel.py
RUN python -m pip install --no-cache-dir --no-deps --only-binary=:all: \
      --require-hashes --requirement /opt/ldfreq/requirements-prod-linux-x86_64.lock
RUN install -d -m 0700 /tmp/ldfreq-watchdog-wheel \
    && python -m pip download --isolated --disable-pip-version-check \
         --no-cache-dir --no-deps --only-binary=:all: --require-hashes \
         --platform manylinux2014_x86_64 --implementation cp \
         --python-version 3.12 --abi none \
         --dest /tmp/ldfreq-watchdog-wheel \
         --requirement /opt/ldfreq/requirements-watchdog-pure-linux-x86_64.lock \
    && python /opt/ldfreq/check_pure_watchdog_wheel.py \
         /tmp/ldfreq-watchdog-wheel/watchdog-6.0.0-py3-none-manylinux2014_x86_64.whl \
    && python -m pip install --isolated --disable-pip-version-check \
         --no-index --no-cache-dir --no-deps --no-compile \
         --only-binary=:all: --require-hashes \
         --platform manylinux2014_x86_64 --implementation cp \
         --python-version 3.12 --abi none \
         --target /usr/local/lib/python3.12/site-packages \
         --find-links /tmp/ldfreq-watchdog-wheel \
         --requirement /opt/ldfreq/requirements-watchdog-pure-linux-x86_64.lock \
    && test -z "$(find /usr/local/lib/python3.12/site-packages/watchdog \
         -type f -name '*.pyc' -print -quit)" \
    && rm -rf /tmp/ldfreq-watchdog-wheel

RUN addgroup -S -g 10001 ldfreq \
    && adduser -S -D -H -u 10001 -G ldfreq \
         -h /nonexistent -s /sbin/nologin ldfreq \
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

# This stage is never deployed. It combines the exact production filesystem
# and dependency graph with public golden fixtures and verification scripts.
FROM application AS verification
USER 0:0
COPY --chown=0:0 requirements.txt /verification/requirements.txt
COPY --chown=0:0 \
    deploy/cloud-run/requirements-prod.txt \
    deploy/cloud-run/requirements-prod-linux-x86_64.lock \
    deploy/cloud-run/requirements-watchdog-pure-linux-x86_64.lock \
    deploy/cloud-run/base-image.json \
    /verification/deploy/cloud-run/
COPY --chown=0:0 \
    scripts/build_v1_golden_fixtures.py \
    scripts/check_runtime_environment.py \
    /verification/scripts/
COPY --chown=0:0 tests/fixtures/v1_golden/ /verification/tests/fixtures/v1_golden/
RUN chmod -R a-w /verification
ENV PYTHONPATH=/opt/ldfreq
USER 10001:10001

# Keep the last/default stage deployable and free of verification material.
FROM application AS production
