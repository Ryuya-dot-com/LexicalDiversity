from scripts import check_staging_coherence as staging


def _ordinary(status: str, path: str) -> str:
    return (
        f"1 {status} N... 100644 100644 100644 "
        f"{'a' * 40} {'b' * 40} {path}"
    )


def test_porcelain_parser_preserves_paths_and_ignores_headers():
    entries = staging.parse_porcelain_v2(
        [
            "# branch.head main",
            _ordinary("M.", "README.md"),
            _ordinary("AM", "path with spaces.py"),
            "? untracked file.txt",
        ]
    )

    assert entries == [
        staging.StatusEntry("ordinary", "M", ".", "README.md"),
        staging.StatusEntry("ordinary", "A", "M", "path with spaces.py"),
        staging.StatusEntry("untracked", "?", "?", "untracked file.txt"),
    ]


def test_partial_stage_and_staged_then_deleted_are_blocking():
    entries = staging.parse_porcelain_v2(
        [
            _ordinary("MM", "mixed.py"),
            _ordinary("AD", "generated-output.html"),
            _ordinary("M.", "coherent.py"),
            "? new.py",
        ]
    )

    violations = staging.coherence_violations(entries)

    assert len(violations) == 2
    assert any("partially staged MM: mixed.py" in item for item in violations)
    assert any("staged addition is absent" in item for item in violations)


def test_coherent_index_or_worktree_only_changes_pass():
    entries = staging.parse_porcelain_v2(
        [
            _ordinary("M.", "staged.py"),
            _ordinary(".M", "unstaged.py"),
            "? untracked.py",
        ]
    )

    assert staging.coherence_violations(entries) == []
