import gzip
import io
import json
import tarfile
from pathlib import Path

from scripts import build_clean_public_candidate as candidate


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


def test_current_clean_candidate_inventory_is_public_and_omits_generated_outputs():
    inventory = candidate.candidate_inventory()

    assert candidate.REQUIRED_PATHS <= set(inventory.paths)
    assert "docs/dispersion-sensitivity-simulation.qmd" in inventory.paths
    assert "docs/dispersion-sensitivity-simulation.html" not in inventory.paths
    assert not any(
        path.startswith("docs/dispersion-sensitivity-simulation_files/")
        for path in inventory.paths
    )
    assert not any(path.startswith(".git/") for path in inventory.paths)


def test_candidate_evidence_is_canonical_and_archive_hash_matches(monkeypatch, tmp_path):
    (tmp_path / "README.md").write_bytes(b"candidate")
    inventory = candidate.CandidateInventory(("README.md",), (), ())
    archive_payload = gzip.compress(b"archive", mtime=0)
    monkeypatch.setattr(candidate, "PROJECT_ROOT", tmp_path)
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
    assert document["archive"]["sha256"] == __import__("hashlib").sha256(
        archive_payload
    ).hexdigest()
    assert document["inventory"]["files"]["README.md"]["sha256"] == (
        "dda18a0e21ae47c53b4309434cbc02ae8bf764fa83a6defbb719431242722aa7"
    )
