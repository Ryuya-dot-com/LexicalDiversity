import importlib.metadata
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts import check_runtime_environment as runtime_contract
from scripts import build_candidate_image_evidence as image_evidence


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ci.yml"
RELEASE_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "release.yml"
IMAGE_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "image-candidate.yml"
CI_REQUIREMENTS = ROOT / "requirements-ci.txt"
CI_WHEEL_LOCK = ROOT / "requirements-ci-linux-x86_64.lock"
PRODUCTION_REQUIREMENTS = ROOT / "deploy" / "cloud-run" / "requirements-prod.txt"
PRODUCTION_WHEEL_LOCK = (
    ROOT / "deploy" / "cloud-run" / "requirements-prod-linux-x86_64.lock"
)
DOCKERFILE = ROOT / "deploy" / "cloud-run" / "Dockerfile"

CHECKOUT_ACTION = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_PYTHON_ACTION = (
    "actions/setup-python@83679a892e2d95755f2dac6acb0bfd1e9ac5d548"
)
SBOM_ACTION = "anchore/sbom-action@e22c389904149dbc22b58101806040fa8d37a610"
SCAN_ACTION = "anchore/scan-action@e1165082ffb1fe366ebaf02d8526e7c4989ea9d2"
LOGIN_ACTION = "docker/login-action@06fb636fac595d6fb4b28a5dfcb21a6f5091859c"
ATTEST_ACTION = (
    "actions/attest-build-provenance@0f67c3f4856b2e3261c31976d6725780e5e4c373"
)
UPLOAD_ACTION = "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
CI_ONLY_PINS = {
    "pytest": "9.0.3",
    "iniconfig": "2.3.0",
    "pluggy": "1.6.0",
    "pygments": "2.19.2",
    "pyyaml": "6.0.3",
}


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _workflow() -> dict[str, object]:
    # BaseLoader follows strings conservatively and avoids YAML 1.1 treating the
    # GitHub key `on` as a boolean while still performing a real syntax parse.
    document = yaml.load(_workflow_text(), Loader=yaml.BaseLoader)
    assert isinstance(document, dict)
    return document


def _steps() -> list[dict[str, object]]:
    workflow = _workflow()
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    verify = jobs["verify"]
    assert isinstance(verify, dict)
    steps = verify["steps"]
    assert isinstance(steps, list)
    assert all(isinstance(step, dict) for step in steps)
    return steps


def test_ci_workflow_is_valid_yaml_with_least_privilege_triggers():
    workflow = _workflow()

    assert set(workflow["on"]) == {"push", "pull_request", "workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    verify = workflow["jobs"]["verify"]
    assert verify["runs-on"] == "ubuntu-24.04"
    assert verify["timeout-minutes"] == "30"
    assert verify["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert verify["env"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"


def test_ci_actions_are_sha_pinned_and_python_series_is_fixed():
    steps = _steps()
    action_steps = [step for step in steps if "uses" in step]

    assert [step["uses"] for step in action_steps] == [
        CHECKOUT_ACTION,
        SETUP_PYTHON_ACTION,
    ]
    assert action_steps[0]["with"]["persist-credentials"] == "false"
    assert action_steps[0]["with"]["fetch-depth"] == "1"
    assert action_steps[1]["with"]["python-version"] == "3.12.10"
    assert action_steps[1]["with"]["check-latest"] == "false"
    assert runtime_contract.EXPECTED_PYTHON_SERIES == (3, 12)
    assert runtime_contract.EXPECTED_PYTHON_VERSION == (3, 12, 10)
    assert "python:3.12.10-slim-bookworm@sha256:" in DOCKERFILE.read_text(
        encoding="utf-8"
    )


def test_ci_uses_one_exact_lock_and_runs_every_required_gate():
    steps = _steps()
    names = [str(step["name"]) for step in steps]
    commands = "\n".join(str(step.get("run", "")) for step in steps)

    assert "--requirement requirements-ci-linux-x86_64.lock" in commands
    assert "--no-deps" in commands
    assert "--no-cache-dir" in commands
    assert "--only-binary=:all:" in commands
    assert "--require-hashes" in commands
    assert not re.search(r"--requirement\s+requirements\.txt(?:\s|$)", commands)
    assert "python scripts/check_runtime_environment.py" in commands
    assert "python scripts/check_version_contract.py --development" in commands
    assert "python scripts/check_base_image_identity.py" in commands
    assert "python -m pip check" in commands
    assert "python -m pytest -p no:cacheprovider" in commands
    assert "python scripts/check_public_release.py" in commands
    assert "git diff --check" in commands
    assert "git diff --exit-code" in commands
    assert "git status --porcelain=v1 --untracked-files=all" in commands

    assert names.index("Run complete pytest suite") < names.index(
        "Enforce public release inventory"
    )
    assert names[-1] == "Reject whitespace errors and checkout mutations"


def test_ci_cannot_fetch_use_or_upload_human_writing_payloads():
    workflow = _workflow_text().lower()
    forbidden = (
        "ellipse-corpus",
        "ellipse_final",
        "ellipse_test_zip_password",
        "fetch_ellipse",
        "actions/upload-artifact",
        "actions/download-artifact",
        "upload-artifact",
        "download-artifact",
        "git lfs",
        "secrets.",
        "curl ",
        "wget ",
    )

    for token in forbidden:
        assert token not in workflow
    assert workflow.count(".research") == 1
    assert workflow.count("lexicalsophistication") == 1
    assert "for private_path in .research lexicalsophistication taaled" in workflow
    assert '[[ -e "${private_path}" || -l "${private_path}" ]]' in workflow
    assert "find benchmarks/ellipse -type f -print" in workflow
    assert "benchmarks/ellipse/analysis-plan.json" in workflow
    assert "benchmarks/ellipse/manifest.json" in workflow
    assert "benchmark payload appeared in ci" in workflow
    assert all(
        step.get("uses") in {CHECKOUT_ACTION, SETUP_PYTHON_ACTION}
        for step in _steps()
        if "uses" in step
    )


def test_ci_requirements_extend_production_lock_with_exact_test_graph():
    active = [
        line.strip()
        for line in CI_REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    includes = [line for line in active if line.startswith(("-r", "--requirement"))]
    pin_lines = [line for line in active if line not in includes]

    assert includes == ["-r deploy/cloud-run/requirements-prod.txt"]
    assert all(line.count("==") == 1 for line in pin_lines)
    pins = {
        runtime_contract.canonical_package_name(line.split("==", 1)[0]):
        line.split("==", 1)[1]
        for line in pin_lines
    }
    assert pins == CI_ONLY_PINS


def test_linux_wheel_locks_match_flat_graphs_and_allow_one_artifact_each():
    production = runtime_contract.read_exact_pins(PRODUCTION_REQUIREMENTS)
    production_artifacts = runtime_contract.read_hashed_lock(PRODUCTION_WHEEL_LOCK)
    ci_artifacts = runtime_contract.read_hashed_lock(CI_WHEEL_LOCK)

    assert {name: version for name, (version, _hash) in production_artifacts.items()} == production
    expected_ci = {**production, **CI_ONLY_PINS}
    assert {name: version for name, (version, _hash) in ci_artifacts.items()} == expected_ci
    assert all(re.fullmatch(r"[0-9a-f]{64}", digest) for _, digest in ci_artifacts.values())
    assert len({digest for _, digest in ci_artifacts.values()}) == len(ci_artifacts)


def test_wheel_selection_recipe_fixes_interpreter_pip_platforms_and_abis(tmp_path: Path):
    from scripts import download_linux_wheels as recipe

    assert recipe.EXPECTED_PYTHON == (3, 12, 10)
    assert recipe.EXPECTED_PIP == "25.0.1"
    assert recipe.PLATFORMS[0] == "manylinux_2_36_x86_64"
    assert "manylinux_2_28_x86_64" in recipe.PLATFORMS
    assert "manylinux_2_17_x86_64" in recipe.PLATFORMS
    assert "manylinux2014_x86_64" in recipe.PLATFORMS
    assert recipe.ABIS == ("cp312", "abi3", "none")

    command = recipe.download_command(tmp_path)
    assert command[:4] == [recipe.sys.executable, "-m", "pip", "download"]
    assert command.count("--platform") == len(recipe.PLATFORMS)
    assert command.count("--abi") == len(recipe.ABIS)
    assert "--no-deps" in command
    assert "--only-binary=:all:" in command


def test_production_lock_and_developer_nltk_pin_match_runtime_code():
    pins = runtime_contract.read_exact_pins(PRODUCTION_REQUIREMENTS)
    development_specs = runtime_contract.declared_specifiers(
        ROOT / "requirements.txt", "nltk"
    )

    assert pins["nltk"] == runtime_contract.AUDITED_NLTK_VERSION == "3.10.0"
    assert development_specs == ["==3.10.0"]
    assert runtime_contract.PRODUCTION_REQUIREMENTS == PRODUCTION_REQUIREMENTS
    assert runtime_contract.PRODUCTION_WHEEL_LOCK == PRODUCTION_WHEEL_LOCK

    from ldfreq import tubelex

    assert tubelex.TUBELEX_PRODUCTION_NLTK_VERSION == "3.10.0"
    assert tubelex.TUBELEX_AUDITED_NLTK_VERSIONS == {"3.10.0"}


@pytest.mark.parametrize(
    "bad_requirement",
    [
        "nltk>=3.10.0\n",
        "nltk==3.10.0; python_version >= '3.12'\n",
        "nltk @ https://example.invalid/nltk.whl\n",
        "-r another-lock.txt\n",
        "nltk==3.10.0\nNLTK==3.10.0\n",
    ],
)
def test_runtime_lock_parser_rejects_nonexact_or_ambiguous_inputs(
    tmp_path: Path,
    bad_requirement: str,
):
    path = tmp_path / "bad-requirements.txt"
    path.write_text(bad_requirement, encoding="utf-8")

    with pytest.raises(runtime_contract.RuntimeContractError):
        runtime_contract.read_exact_pins(path)


def test_runtime_python_contract_fails_closed_on_wrong_series_or_prerelease():
    assert runtime_contract.python_contract_violations(
        implementation="cpython",
        version=(3, 12, 10),
        releaselevel="final",
    ) == []

    violations = runtime_contract.python_contract_violations(
        implementation="pypy",
        version=(3, 13, 0),
        releaselevel="candidate",
    )
    assert len(violations) == 3
    assert any("implementation" in violation for violation in violations)
    assert any("expected exactly 3.12.10" in violation for violation in violations)
    assert any("final release" in violation for violation in violations)


@pytest.mark.parametrize(
    "bad_requirement",
    [
        "nltk==3.10.0\n",
        "nltk==3.10.0 --hash=sha256:abc\n",
        "nltk==3.10.0 --hash=sha256:" + "a" * 64 + " --hash=sha256:" + "b" * 64 + "\n",
        "nltk==3.10.0 --hash=sha256:" + "A" * 64 + "\n",
        "nltk==3.10.0 --hash=sha256:" + "a" * 64 + "\nNLTK==3.10.0 --hash=sha256:" + "b" * 64 + "\n",
    ],
)
def test_hashed_lock_parser_rejects_missing_multiple_or_ambiguous_hashes(
    tmp_path: Path,
    bad_requirement: str,
):
    path = tmp_path / "bad-wheel.lock"
    path.write_text(bad_requirement, encoding="utf-8")
    with pytest.raises(runtime_contract.RuntimeContractError):
        runtime_contract.read_hashed_lock(path)


def test_runtime_distribution_check_reports_missing_and_mismatched_pins():
    pins = {"nltk": "3.10.0", "numpy": "2.4.2", "pandas": "2.3.3"}

    def resolve(package: str) -> str:
        if package == "nltk":
            return "3.9.4"
        if package == "pandas":
            raise importlib.metadata.PackageNotFoundError(package)
        return pins[package]

    violations = runtime_contract.installed_pin_violations(
        pins,
        version_resolver=resolve,
    )
    assert len(violations) == 2
    assert any("nltk==3.9.4" in violation for violation in violations)
    assert any("pandas==2.3.3" in violation for violation in violations)


def test_nltk_contract_cross_checks_imported_and_tubelex_versions():
    modules = {
        "nltk": SimpleNamespace(__version__="3.10.0"),
        "ldfreq.tubelex": SimpleNamespace(
            TUBELEX_PRODUCTION_NLTK_VERSION="3.10.0",
            TUBELEX_AUDITED_NLTK_VERSIONS=frozenset({"3.10.0"}),
        ),
    }

    assert runtime_contract.nltk_contract_violations(
        {"nltk": "3.10.0"},
        importer=modules.__getitem__,
    ) == []

    modules["nltk"] = SimpleNamespace(__version__="3.9.4")
    violations = runtime_contract.nltk_contract_violations(
        {"nltk": "3.9.4"},
        importer=modules.__getitem__,
    )
    assert any("production NLTK pin" in violation for violation in violations)
    assert any("imported NLTK" in violation for violation in violations)


def test_tagged_release_workflow_is_read_only_and_rebuilds_all_evidence():
    text = RELEASE_WORKFLOW_PATH.read_text(encoding="utf-8")
    workflow = yaml.load(text, Loader=yaml.BaseLoader)
    verify = workflow["jobs"]["verify-release"]
    steps = verify["steps"]
    commands = "\n".join(str(step.get("run", "")) for step in steps)
    action_steps = [step for step in steps if "uses" in step]

    assert workflow["on"] == {"push": {"tags": ["v*"]}}
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    assert verify["runs-on"] == "ubuntu-24.04"
    assert [step["uses"] for step in action_steps] == [
        CHECKOUT_ACTION,
        SETUP_PYTHON_ACTION,
    ]
    assert action_steps[0]["with"] == {
        "fetch-depth": "0",
        "persist-credentials": "false",
    }
    assert action_steps[1]["with"]["python-version"] == "3.12.10"
    assert "python scripts/check_version_contract.py --release" in commands
    assert "python scripts/check_git_history.py" in commands
    assert "--requirement requirements-ci-linux-x86_64.lock" in commands
    assert "--require-hashes" in commands
    assert "python scripts/check_runtime_environment.py" in commands
    assert "python -m pytest -p no:cacheprovider" in commands
    assert "python scripts/build_v1_golden_fixtures.py --check" in commands
    assert "python scripts/check_public_release.py" in commands
    assert "docker buildx build --target production --platform linux/amd64" in commands
    assert "--file deploy/cloud-run/Dockerfile" in commands
    assert 'export SOURCE_DATE_EPOCH="${ldfreq_source_date_epoch}"' in commands
    assert commands.count("--provenance=false") == 1
    assert "SOURCE_DATE_EPOCH=${ldfreq_source_date_epoch}" in commands
    assert "rewrite-timestamp=true,compatibility-version=20" in commands
    assert 'docker image inspect "ldfreq:${GITHUB_SHA}"' in commands
    assert "docker run --rm --network none --read-only" in commands
    assert "python scripts/build_release_archive.py" in commands
    assert "--output /tmp/ldfreq-source.tar.gz" in commands
    assert "python scripts/build_release_evidence.py" in commands
    assert "--source-archive /tmp/ldfreq-source.tar.gz" in commands
    assert "--output /tmp/ldfreq-release-evidence.json" in commands
    assert "sha256sum /tmp/ldfreq-source.tar.gz" in commands
    assert "sha256sum /tmp/ldfreq-release-evidence.json" in commands
    assert "git diff --exit-code" in commands
    assert "git status --porcelain=v1 --untracked-files=all" in commands
    assert "upload-artifact" not in text.lower()
    assert "secrets." not in text.lower()


def test_candidate_image_workflow_is_manual_least_privilege_and_sha_pinned():
    text = IMAGE_WORKFLOW_PATH.read_text(encoding="utf-8")
    workflow = yaml.load(text, Loader=yaml.BaseLoader)
    job = workflow["jobs"]["build-scan-attest"]
    steps = job["steps"]
    action_steps = [step for step in steps if "uses" in step]

    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {
        "contents": "read",
        "packages": "write",
        "id-token": "write",
        "attestations": "write",
    }
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["timeout-minutes"] == "45"
    assert job["env"]["IMAGE_NAME"] == "ghcr.io/ryuya-dot-com/lexicaldiversity"
    assert job["env"]["SYFT_VERSION"] == "1.49.0"
    assert job["env"]["GRYPE_VERSION"] == "0.116.0"
    assert job["env"]["IMAGE_NAME"] == image_evidence.EXPECTED_IMAGE_NAME
    assert job["env"]["SYFT_VERSION"] == image_evidence.EXPECTED_SYFT_VERSION
    assert job["env"]["GRYPE_VERSION"] == image_evidence.EXPECTED_GRYPE_VERSION
    assert image_evidence.BUILDKIT_COMPATIBILITY_VERSION == 20
    assert [step["uses"] for step in action_steps] == [
        CHECKOUT_ACTION,
        SETUP_PYTHON_ACTION,
        SBOM_ACTION,
        SCAN_ACTION,
        LOGIN_ACTION,
        ATTEST_ACTION,
        UPLOAD_ACTION,
    ]
    assert all(re.search(r"@[0-9a-f]{40}\Z", step["uses"]) for step in action_steps)
    assert "secrets." not in text.lower()


def test_candidate_image_workflow_separates_verification_scan_push_and_release():
    text = IMAGE_WORKFLOW_PATH.read_text(encoding="utf-8")
    workflow = yaml.load(text, Loader=yaml.BaseLoader)
    steps = workflow["jobs"]["build-scan-attest"]["steps"]
    commands = "\n".join(str(step.get("run", "")) for step in steps)
    by_name = {step["name"]: step for step in steps}

    assert 'test "${GITHUB_REF}" = "refs/heads/main"' in commands
    assert "python scripts/check_git_history.py" in commands
    assert "python scripts/check_public_release.py" in commands
    assert "python scripts/check_base_image_identity.py --remote" in commands
    assert commands.count("docker buildx build --no-cache --target production") == 2
    assert "docker buildx build --target verification" in commands
    assert commands.count("--file deploy/cloud-run/Dockerfile") == 3
    assert 'export SOURCE_DATE_EPOCH="${source_date_epoch}"' in commands
    assert commands.count("--provenance=false") == 3
    assert "SOURCE_DATE_EPOCH=${source_date_epoch}" in commands
    assert commands.count("rewrite-timestamp=true,compatibility-version=20") == 3
    assert "test \"${rebuild_image_id}\" = \"${image_id}\"" in commands
    assert "/tmp/ldfreq-image-evidence/production-image-inspect.json" in commands
    assert "/tmp/ldfreq-image-evidence/rebuild-image-inspect.json" in commands
    assert "production image ID: %s" in commands
    assert "/verification/scripts/build_v1_golden_fixtures.py --check" in commands
    assert commands.count("--network none --read-only --cap-drop ALL") == 2
    assert by_name["Scan image with a Critical-severity gate"]["with"] == {
        "image": "${{ steps.build.outputs.production-ref }}",
        "fail-build": "true",
        "severity-cutoff": "critical",
        "only-fixed": "false",
        "output-format": "json",
        "output-file": "/tmp/ldfreq-image-evidence/grype.json",
        "grype-version": "v${{ env.GRYPE_VERSION }}",
        "cache-db": "false",
    }
    assert by_name["Log in to GitHub Container Registry after scan success"]["if"] == (
        "steps.scan.outcome == 'success'"
    )
    assert by_name["Push commit-addressed candidate and verify registry manifest"][
        "if"
    ] == "steps.scan.outcome == 'success'"
    assert "candidate-${GITHUB_SHA}" in commands
    assert "docker buildx imagetools inspect --raw" in commands
    assert "config_digest" in commands
    assert "build_candidate_image_evidence.py" in commands
    assert "registry_candidate_is_release" not in text
    assert by_name["Upload SBOM, scan, manifest, and canonical evidence"]["if"] == (
        "always()"
    )
    assert by_name["Enforce scan and complete-evidence gates"]["if"] == "always()"
