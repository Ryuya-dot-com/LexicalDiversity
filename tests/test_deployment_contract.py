import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "cloud-run"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _canonical_package(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def test_docker_build_context_excludes_private_and_local_payloads():
    rules = {
        line.strip()
        for line in _read(ROOT / ".dockerignore").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    required = {
        ".git/**",
        ".env.*",
        ".research",
        ".research/**",
        ".streamlit/runtime_lists/**",
        ".streamlit/secrets.toml",
        "LexicalSophistication/**",
        "TAALED/**",
        "NationBNCCOCA/**",
        "data/antbnc/**",
        "data/bnc_coca/**",
        "data/raw/**",
        "data/sources/**",
        "sources/**",
        "benchmarks",
        "benchmarks/**",
        "*.xlsx",
        "*.pdf",
        "*.zip",
    }
    assert required <= rules
    assert "!.streamlit/config.toml" in rules
    assert {
        "!tests/fixtures/v1_golden/**",
        "!scripts/build_v1_golden_fixtures.py",
        "!scripts/check_runtime_environment.py",
        "!scripts/check_pure_watchdog_wheel.py",
    } <= rules


def test_dockerfile_is_fail_closed_and_copies_only_reviewed_runtime_inputs():
    dockerfile = _read(DEPLOY / "Dockerfile")

    identity = __import__("json").loads(_read(DEPLOY / "base-image.json"))
    exact_image = f"python:{identity['tag']}@{identity['manifest_digest']}"
    assert dockerfile.startswith(
        "# syntax=docker/dockerfile:1.7@sha256:"
        "a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e\n"
    )
    assert dockerfile.count("ARG SOURCE_DATE_EPOCH") == 2
    assert f"ARG PYTHON_IMAGE={exact_image}" in dockerfile
    assert "FROM --platform=linux/amd64 ${PYTHON_IMAGE} AS application" in dockerfile
    application_stage = dockerfile.split(
        "FROM --platform=linux/amd64 ${PYTHON_IMAGE} AS application",
        1,
    )[1].split("FROM application AS verification", 1)[0]
    assert application_stage.count("ARG SOURCE_DATE_EPOCH") == 1
    assert application_stage.index("ARG SOURCE_DATE_EPOCH") < application_stage.index(
        "RUN python -m pip install"
    )
    assert "FROM application AS verification" in dockerfile
    assert dockerfile.rstrip().endswith("FROM application AS production")
    assert "@sha256:" in dockerfile
    assert "test \"${#ldfreq_base_digest}\" -eq 64" in dockerfile
    assert not re.search(r"(?m)^COPY(?:\s+--\S+)*\s+\.\s", dockerfile)
    assert "requirements-prod-linux-x86_64.lock" in dockerfile
    assert "requirements-watchdog-pure-linux-x86_64.lock" in dockerfile
    assert "check_pure_watchdog_wheel.py" in dockerfile
    assert "--no-deps --only-binary=:all:" in dockerfile
    assert "--require-hashes" in dockerfile
    assert "--platform manylinux2014_x86_64" in dockerfile
    assert "--target /usr/local/lib/python3.12/site-packages" in dockerfile
    assert "addgroup -S -g 10001 ldfreq" in dockerfile
    assert "adduser -S -D -H -u 10001" in dockerfile
    assert "groupadd" not in dockerfile
    assert "useradd" not in dockerfile

    copy_lines = [
        line.strip() for line in dockerfile.splitlines() if line.lstrip().startswith("COPY ")
    ]
    copied = "\n".join(copy_lines)
    for public_source in (
        "app.py",
        "ldfreq/",
        "pages/",
        ".streamlit/config.toml",
        "data/resource_registry.json",
        "data/NJ8/",
        "data/ngsl/",
        "data/open/",
    ):
        assert public_source in copied
    for forbidden_source in (
        "data/antbnc",
        "data/bnc_coca",
        ".streamlit/runtime_lists",
        ".streamlit/secrets.toml",
        ".research",
        "LexicalSophistication",
        "TAALED",
        "benchmarks",
    ):
        assert forbidden_source not in copied

    verification = dockerfile.split("FROM application AS verification", 1)[1].split(
        "FROM application AS production", 1
    )[0]
    assert "tests/fixtures/v1_golden/" in verification
    assert "scripts/build_v1_golden_fixtures.py" in verification
    assert "scripts/check_runtime_environment.py" in verification
    assert "ENV PYTHONPATH=/opt/ldfreq" in verification
    assert "USER 10001:10001" in verification

    assert "USER 10001:10001" in dockerfile
    assert "--chown=0:0" in dockerfile
    assert "chmod -R a-w /opt/ldfreq" in dockerfile
    assert "chmod 0555 /tmp /var/tmp" in dockerfile
    assert "XDG_CACHE_HOME=/tmp/ldfreq/cache" in dockerfile
    assert "XDG_CONFIG_HOME=/tmp/ldfreq/config" in dockerfile
    assert "TMPDIR=/tmp/ldfreq/tmp" in dockerfile
    assert not re.search(r"(?m)^\s*(?:ENV|ARG)\s+(?:HOME|CODEX_HOME)(?:=|\s)", dockerfile)


def test_entrypoint_keeps_temporary_data_private_and_binds_port_8080():
    entrypoint = _read(DEPLOY / "entrypoint.sh")
    assert "set -eu" in entrypoint
    assert "umask 077" in entrypoint
    assert "ulimit -c 0" in entrypoint
    assert "${TMPDIR:=/tmp/ldfreq/tmp}" in entrypoint
    assert "${XDG_CACHE_HOME:=/tmp/ldfreq/cache}" in entrypoint
    assert "${XDG_CONFIG_HOME:=/tmp/ldfreq/config}" in entrypoint
    assert 'test -d "${ldfreq_runtime_dir}" && test -w "${ldfreq_runtime_dir}"' in entrypoint
    assert 'if [ "$#" -gt 0 ]; then' in entrypoint
    assert 'exec "$@"' in entrypoint
    assert entrypoint.index('test -d "${ldfreq_runtime_dir}"') < entrypoint.index(
        'exec "$@"'
    )
    assert "exec python -m streamlit run app.py" in entrypoint
    assert "--server.address=0.0.0.0" in entrypoint
    assert "--server.port=8080" in entrypoint
    assert ">/dev/null 2>&1" in entrypoint
    assert not re.search(r"(?m)^\s*(?:HOME|CODEX_HOME)=", entrypoint)


def test_streamlit_hides_error_details_from_clients():
    config = _read(ROOT / ".streamlit" / "config.toml")

    assert 'showErrorDetails = "none"' in config
    assert "showErrorDetails = false" not in config
    assert 'fileWatcherType = "none"' in config


def test_production_requirements_pin_complete_runtime_dependency_graph():
    lines = [
        line.strip()
        for line in _read(DEPLOY / "requirements-prod.txt").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert all(line.count("==") == 1 for line in lines)
    assert len(lines) == len(set(lines))

    packages = {_canonical_package(line.split("==", 1)[0]) for line in lines}
    expected = {
        "streamlit",
        "simplemma",
        "pandas",
        "numpy",
        "plotly",
        "openpyxl",
        "altair",
        "blinker",
        "cachetools",
        "click",
        "gitpython",
        "packaging",
        "pillow",
        "pydeck",
        "protobuf",
        "pyarrow",
        "requests",
        "tenacity",
        "toml",
        "tornado",
        "typing-extensions",
        "python-dateutil",
        "pytz",
        "tzdata",
        "narwhals",
        "et-xmlfile",
        "jinja2",
        "jsonschema",
        "gitdb",
        "charset-normalizer",
        "idna",
        "urllib3",
        "certifi",
        "six",
        "markupsafe",
        "attrs",
        "jsonschema-specifications",
        "referencing",
        "rpds-py",
        "smmap",
        "watchdog",
        "nltk",
        "joblib",
        "regex",
        "tqdm",
        "defusedxml",
    }
    assert packages == expected
    versions = {
        _canonical_package(line.split("==", 1)[0]): line.split("==", 1)[1]
        for line in lines
    }
    assert versions["nltk"] == "3.10.0"
    assert "nltk==3.10.0" in _read(ROOT / "requirements.txt").splitlines()


def test_base_image_identity_is_platform_specific_and_offline_verifiable():
    from scripts import check_base_image_identity as base_image

    identity = base_image.read_identity()
    assert identity["platform"] == {"os": "linux", "architecture": "amd64"}
    assert identity["python_version"] == "3.12.13"
    assert identity["tag"] == "3.12.13-alpine3.23"
    assert identity["index_digest"] != identity["manifest_digest"]
    assert base_image.offline_violations(identity) == []


def test_cloud_run_template_fixes_tokyo_and_least_privilege_runtime_contract():
    service = _read(DEPLOY / "service.template.yaml")

    assert "PLACEHOLDER PILOT TEMPLATE" in service
    assert "cloud.googleapis.com/location: asia-northeast1" in service
    assert "run.googleapis.com/ingress: internal-and-cloud-load-balancing" in service
    assert 'run.googleapis.com/default-url-disabled: "true"' in service
    assert 'autoscaling.knative.dev/minScale: "0"' in service
    assert 'autoscaling.knative.dev/maxScale: "3"' in service
    assert "run.googleapis.com/execution-environment: gen2" in service
    assert "run.googleapis.com/network-interfaces:" in service
    assert "run.googleapis.com/vpc-access-egress: all-traffic" in service
    assert "containerConcurrency: 1" in service
    assert "timeoutSeconds: 1800" in service
    assert "@sha256:IMAGE_DIGEST" in service

    assert "value: bnc_coca,nation_bnc_coca_families" in service
    assert "name: LDFREQ_SERVER_ONLY_RIGHTS_ACKNOWLEDGED" in service
    assert re.search(
        r"name: LDFREQ_SERVER_ONLY_RIGHTS_ACKNOWLEDGED[\s\S]{0,220}value: \"0\"",
        service,
    )
    assert "name: LDFREQ_SERVING_MODE" in service
    assert "value: public" in service
    assert "name: LDFREQ_ALLOW_LOCAL_RESTRICTED" in service
    assert "name: LDFREQ_ANALYSIS_DEADLINE_SECONDS" in service
    assert "name: LDFREQ_REAL_WRITING_APPROVED" in service
    assert 'value: "120"' in service
    assert "value: /mnt/nation/headwords-10000" in service
    assert "value: /mnt/nation/nation_bnc_coca_25000_family_index.csv.gz" in service

    assert "driver: gcsfuse.run.googleapis.com" in service
    assert "readOnly: true" in service
    assert "bucketName: NATION_BUCKET" in service
    assert "only-dir=nation" in service
    assert "uid=10001,gid=10001,file-mode=0400,dir-mode=0500" in service
    assert "mountPath: /mnt/nation" in service
    assert "mountPath: /tmp/ldfreq" in service
    assert "medium: Memory" in service
    assert "sizeLimit: 64Mi" in service


def test_tokyo_pilot_docs_preserve_non_approval_and_zero_runtime_log_storage():
    pilot = _read(ROOT / "docs" / "cloud-run-tokyo-pilot.md")
    privacy = _read(ROOT / "docs" / "privacy-and-data-handling.md")

    assert "not deployment approval" in pilot
    assert "there is no anonymous public endpoint for real learner writing" in pilot
    assert "The current application does not yet set" in pilot
    assert "`Cache-Control: no-store`" in pilot
    assert "default Cloud Run URL as a\nPreview feature" in pilot
    assert "one reviewed wheel SHA-256 per package" in pilot
    assert "resulting application-image digest" in pilot
    assert "`--require-hashes`" in pilot

    for document in (pilot, privacy):
        assert "`_Default`" in document
        assert "`_Required`" in document
        assert "400 days" in document
        assert "not stored or queryable" in document
        assert "roles/storage.objectViewer" in document
        assert "VPC Service Controls" in document
        assert "IAP" in document

    legacy_guarantees = (
        "deletion-processing interval must not exceed 24",
        "physical deletion within 24 hours",
        "seven-day application-log",
        "7-day application-log",
        "custom application/security bucket",
        "source ip addresses may be retained",
    )
    combined = f"{pilot}\n{privacy}".lower()
    assert not any(claim in combined for claim in legacy_guarantees)
