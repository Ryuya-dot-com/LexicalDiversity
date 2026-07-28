import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from scripts import build_clean_public_candidate as candidate


def _identity(payload: bytes) -> dict[str, object]:
    return {
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _minimal_project(tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    project = tmp_path / "candidate-source"
    (project / "data").mkdir(parents=True)
    files = {
        "README.md": b"reviewed candidate\n",
        "data/resource_registry.json": json.dumps(
            {"schema_version": "test", "resources": []},
            sort_keys=True,
        ).encode("utf-8"),
    }
    for relative, payload in files.items():
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return project, files


def _selection_document(
    project: Path,
    paths: list[str],
    *,
    excluded_family_ids: list[str] | None = None,
) -> dict[str, object]:
    registry_payload = (project / "data/resource_registry.json").read_bytes()
    excluded = (
        list(candidate.QUARANTINED_DERIVED_FAMILIES)
        if excluded_family_ids is None
        else excluded_family_ids
    )
    return {
        "schema_version": candidate.SELECTION_SCHEMA_VERSION,
        "candidate_id": "test-reviewed-candidate",
        "resource_registry": {
            "path": "data/resource_registry.json",
            **_identity(registry_payload),
        },
        "review": {
            "status": "approved",
            "reviewer_role": "release-reviewer",
            "reviewed_on": "2026-07-27",
            "approval_reference": "test-review:deadbeef",
        },
        "entries": [
            {
                "path": relative,
                **_identity((project / relative).read_bytes()),
                "role": "governance"
                if relative == "data/resource_registry.json"
                else "source",
            }
            for relative in paths
        ],
        "excluded_derived_output_families": [
            {
                "id": family_id,
                "decision": "blocked",
                "detector": f"test-detector:{family_id}",
            }
            for family_id in excluded
        ],
        "required_scans": [
            {
                "id": scan_id,
                "status": "pass",
                "finding_count": 0,
                "performed_on": "2026-07-27",
                "reviewer_role": "release-reviewer",
                "evidence_reference": f"test-scan:{scan_id}",
            }
            for scan_id in sorted(candidate.REQUIRED_SCAN_IDS)
        ],
    }


def _write_external_selection(
    tmp_path: Path,
    project: Path,
    paths: list[str],
    *,
    excluded_family_ids: list[str] | None = None,
) -> Path:
    selection_path = tmp_path / "reviewed-selection.json"
    selection_path.write_text(
        json.dumps(
            _selection_document(
                project,
                paths,
                excluded_family_ids=excluded_family_ids,
            ),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return selection_path


def _use_minimal_project(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    discovered_paths: list[str],
) -> None:
    payload = b"".join(
        relative.encode("utf-8") + b"\0" for relative in discovered_paths
    )

    def fake_git_bytes(*arguments: str, input_bytes: bytes | None = None) -> bytes:
        if arguments == (
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ):
            return payload
        if arguments == ("rev-parse", "HEAD"):
            return b"deadbeef\n"
        if arguments == ("status", "--porcelain=v1", "--untracked-files=all"):
            return b" M README.md\n"
        raise AssertionError(arguments)

    monkeypatch.setattr(candidate, "PROJECT_ROOT", project)
    monkeypatch.setattr(
        candidate,
        "REQUIRED_PATHS",
        {"README.md", "data/resource_registry.json"},
    )
    monkeypatch.setattr(candidate, "_git_bytes", fake_git_bytes)
    monkeypatch.setattr(candidate, "ignored_paths", lambda paths: set())


def _tar_member(payload: bytes, relative: str) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        extracted = archive.extractfile(candidate.PREFIX + relative)
        assert extracted is not None
        return extracted.read()


def test_candidate_tar_is_deterministic_and_normalizes_metadata(tmp_path: Path):
    (tmp_path / "nested").mkdir()
    (tmp_path / "README.md").write_text("public\n", encoding="utf-8")
    (tmp_path / "nested" / "tool.py").write_text("print('ok')\n", encoding="utf-8")
    paths = ["nested/tool.py", "README.md"]

    first = candidate.tar_payload(paths, root=tmp_path)
    second = candidate.tar_payload(reversed(paths), root=tmp_path)

    assert first == second
    with tarfile.open(fileobj=io.BytesIO(first), mode="r:") as archive:
        members = archive.getmembers()
        assert [member.name for member in members] == [
            candidate.PREFIX + "README.md",
            candidate.PREFIX + "nested/tool.py",
        ]
        assert all(member.mtime == 0 for member in members)
        assert all(member.uid == member.gid == 0 for member in members)
        assert all(member.mode == 0o644 for member in members)


def test_cli_requires_an_external_reviewed_selection_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    project, _ = _minimal_project(tmp_path)
    monkeypatch.setattr(candidate, "PROJECT_ROOT", project)

    with pytest.raises(SystemExit) as missing_argument:
        candidate.main(
            [
                "--output",
                str(tmp_path / "candidate.tar.gz"),
                "--evidence-output",
                str(tmp_path / "evidence.json"),
            ]
        )
    assert missing_argument.value.code == 2

    internal_selection = project / "reviewed-selection.json"
    internal_selection.write_text("{}\n", encoding="utf-8")
    with pytest.raises(candidate.CleanCandidateError, match="must be external"):
        candidate._load_selection(internal_selection)

    external_selection = tmp_path / "external-selection.json"
    external_selection.write_text("{}\n", encoding="utf-8")
    assert candidate.main(
        [
            "--output",
            str(project / "candidate.tar.gz"),
            "--evidence-output",
            str(tmp_path / "evidence.json"),
            "--selection-manifest",
            str(external_selection),
        ]
    ) == 1
    assert not (project / "candidate.tar.gz").exists()


@pytest.mark.parametrize(
    ("discovered", "declared", "expected"),
    [
        (
            ["README.md", "data/resource_registry.json", "scratch.txt"],
            ["README.md", "data/resource_registry.json"],
            "unreviewed=scratch.txt",
        ),
        (
            ["README.md", "data/resource_registry.json"],
            ["README.md", "data/resource_registry.json", "planned.py"],
            "missing=planned.py",
        ),
    ],
)
def test_selection_is_an_exact_set_and_rejects_unreviewed_or_missing_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    discovered: list[str],
    declared: list[str],
    expected: str,
):
    project, _ = _minimal_project(tmp_path)
    for relative in {"scratch.txt", "planned.py"}:
        (project / relative).write_text(relative, encoding="utf-8")
    selection = _write_external_selection(tmp_path, project, declared)
    _use_minimal_project(monkeypatch, project, discovered)

    with pytest.raises(candidate.CleanCandidateError, match=expected):
        candidate.candidate_inventory(selection)


def test_main_archives_and_evidences_the_reviewed_snapshot_after_worktree_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    project, original = _minimal_project(tmp_path)
    paths = sorted(original)
    selection = _write_external_selection(tmp_path, project, paths)
    _use_minimal_project(monkeypatch, project, paths)
    real_inventory = candidate.candidate_inventory

    def inventory_then_mutate(selection_path: Path) -> candidate.CandidateInventory:
        inventory = real_inventory(selection_path)
        (project / "README.md").write_bytes(b"unreviewed mutation\n")
        return inventory

    monkeypatch.setattr(candidate, "candidate_inventory", inventory_then_mutate)
    archive_path = tmp_path / "candidate.tar.gz"
    evidence_path = tmp_path / "candidate-evidence.json"

    assert candidate.main(
        [
            "--output",
            str(archive_path),
            "--evidence-output",
            str(evidence_path),
            "--selection-manifest",
            str(selection),
        ]
    ) == 0

    tar_bytes = gzip.decompress(archive_path.read_bytes())
    assert _tar_member(tar_bytes, "README.md") == original["README.md"]
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["inventory"]["files"]["README.md"]["sha256"] == hashlib.sha256(
        original["README.md"]
    ).hexdigest()
    assert evidence["archive"]["sha256"] == hashlib.sha256(
        archive_path.read_bytes()
    ).hexdigest()


def test_evidence_schema_v2_binds_review_registry_scans_and_quarantine_without_leaks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    project, files = _minimal_project(tmp_path)
    paths = sorted(files)
    selection = _write_external_selection(tmp_path, project, paths)
    _use_minimal_project(monkeypatch, project, paths)
    inventory = candidate.candidate_inventory(selection)
    archive_payload = gzip.compress(b"archive", mtime=0)

    document = candidate.evidence_document(inventory, archive_payload)

    assert document["candidate_schema_version"] == 2
    policy = document["release_policy"]
    selection_evidence = policy["selection_manifest"]
    assert selection_evidence["candidate_id"] == "test-reviewed-candidate"
    assert selection_evidence["review"] == {
        "status": "approved",
        "reviewer_role": "release-reviewer",
        "reviewed_on": "2026-07-27",
        "approval_reference": "test-review:deadbeef",
    }
    assert selection_evidence["sha256"] == hashlib.sha256(selection.read_bytes()).hexdigest()
    assert policy["resource_registry"] == {
        "path": "data/resource_registry.json",
        **_identity(files["data/resource_registry.json"]),
    }
    assert policy["scans"] == [
        {
            "id": scan_id,
            "status": "pass",
            "finding_count": 0,
            "performed_on": "2026-07-27",
            "reviewer_role": "release-reviewer",
            "evidence_reference": f"test-scan:{scan_id}",
        }
        for scan_id in sorted(candidate.REQUIRED_SCAN_IDS)
    ]
    exclusions = policy["excluded_derived_output_families"]
    assert {item["id"] for item in exclusions} == set(
        candidate.QUARANTINED_DERIVED_FAMILIES
    )
    assert all(item["decision"] == "blocked" for item in exclusions)
    assert all(item["selected_match_count"] == 0 for item in exclusions)
    assert all("path" not in item and "sha256" not in item for item in exclusions)
    assert "LexicalSophistication" not in json.dumps(document)


def test_selection_must_enumerate_every_quarantined_family(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    project, files = _minimal_project(tmp_path)
    paths = sorted(files)
    selection = _write_external_selection(
        tmp_path,
        project,
        paths,
        excluded_family_ids=[candidate.QUARANTINED_DERIVED_FAMILIES[0]],
    )
    _use_minimal_project(monkeypatch, project, paths)

    with pytest.raises(candidate.CleanCandidateError, match="omits quarantined families"):
        candidate.candidate_inventory(selection)


def test_selection_closed_schema_rejects_private_metadata_smuggling(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    project, files = _minimal_project(tmp_path)
    paths = sorted(files)
    document = _selection_document(project, paths)
    document["review"]["private_notes"] = "must not enter public evidence"
    selection = tmp_path / "reviewed-selection.json"
    selection.write_text(json.dumps(document), encoding="utf-8")
    _use_minimal_project(monkeypatch, project, paths)

    with pytest.raises(
        candidate.CleanCandidateError,
        match="review fields differ",
    ):
        candidate.candidate_inventory(selection)


def test_declaring_a_quarantined_file_does_not_make_it_publishable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    project, files = _minimal_project(tmp_path)
    quarantined = "scripts/run_taales_legacy_coca.py"
    (project / "scripts").mkdir()
    (project / quarantined).write_text("# private calibration only\n", encoding="utf-8")
    paths = sorted([*files, quarantined])
    selection = _write_external_selection(tmp_path, project, paths)
    _use_minimal_project(monkeypatch, project, paths)

    with pytest.raises(candidate.CleanCandidateError, match="quarantined derived output"):
        candidate.candidate_inventory(selection)


def test_candidate_evidence_is_canonical_and_archive_hash_matches(monkeypatch, tmp_path):
    payload = b"candidate"
    inventory = candidate.CandidateInventory(
        ("README.md",),
        (),
        (),
        {"README.md": payload},
        {"README.md": _identity(payload)},
        {"README.md": "source"},
        {
            "candidate_id": "fixture",
            "schema_version": candidate.SELECTION_SCHEMA_VERSION,
            "bytes": 1,
            "sha256": "0" * 64,
            "review": {},
            "required_scans": [
                {
                    "id": scan_id,
                    "status": "pass",
                    "finding_count": 0,
                    "performed_on": "2026-07-27",
                    "reviewer_role": "release-reviewer",
                    "evidence_reference": f"fixture:{scan_id}",
                }
                for scan_id in sorted(candidate.REQUIRED_SCAN_IDS)
            ],
        },
        {"path": "data/resource_registry.json", "bytes": 0, "sha256": "0" * 64},
        (),
        tuple(
            {
                "id": family_id,
                "decision": "blocked",
                "detector": f"fixture:{family_id}",
                "selected_match_count": 0,
            }
            for family_id in candidate.QUARANTINED_DERIVED_FAMILIES
        ),
    )
    archive_payload = gzip.compress(b"archive", mtime=0)
    monkeypatch.setattr(candidate, "_git_text", lambda *arguments: "deadbeef")

    first = candidate.canonical_json(
        candidate.evidence_document(inventory, archive_payload)
    )
    second = candidate.canonical_json(
        candidate.evidence_document(inventory, archive_payload)
    )
    document = json.loads(first)

    assert first == second
    assert first.endswith(b"\n")
    assert document["archive"]["sha256"] == hashlib.sha256(
        archive_payload
    ).hexdigest()
    assert document["inventory"]["files"]["README.md"]["sha256"] == (
        "dda18a0e21ae47c53b4309434cbc02ae8bf764fa83a6defbb719431242722aa7"
    )
