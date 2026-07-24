#!/bin/sh
set -eu

umask 077
ulimit -c 0

# Cloud Run mounts a size-limited in-memory volume at /tmp/ldfreq. Keep every
# process-created temporary file under that mount and fail closed if it is not
# writable by the non-root runtime identity.
: "${TMPDIR:=/tmp/ldfreq/tmp}"
: "${XDG_CACHE_HOME:=/tmp/ldfreq/cache}"
: "${XDG_CONFIG_HOME:=/tmp/ldfreq/config}"
mkdir -p -- "${TMPDIR}" "${XDG_CACHE_HOME}" "${XDG_CONFIG_HOME}"
chmod 0700 -- "${TMPDIR}" "${XDG_CACHE_HOME}" "${XDG_CONFIG_HOME}"
for ldfreq_runtime_dir in "${TMPDIR}" "${XDG_CACHE_HOME}" "${XDG_CONFIG_HOME}"; do
  test -d "${ldfreq_runtime_dir}" && test -w "${ldfreq_runtime_dir}"
done

cd /opt/ldfreq
exec python -m streamlit run app.py \
  --server.address=0.0.0.0 \
  --server.port=8080 \
  --browser.gatherUsageStats=false \
  >/dev/null 2>&1
