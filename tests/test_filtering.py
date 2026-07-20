from ai_agent_radar.config import ExclusionConfig
from ai_agent_radar.filtering import dedupe_repositories, quality_gate


def test_dedupe_merges_categories_and_preserves_first_record(repo_factory) -> None:
    codex = repo_factory(repository_id=7, matched_categories=("codex",), description="first")
    general = repo_factory(repository_id=7, matched_categories=("general",), description="second")

    merged = dedupe_repositories([codex, general])

    assert len(merged) == 1
    assert merged[0].description == "first"
    assert merged[0].matched_categories == ("codex", "general")


def test_quality_gate_keeps_low_star_complete_project(repo_factory, radar_config) -> None:
    project = repo_factory(repository_id=1, stars=0, readme="Install with pip", license_spdx="MIT")

    accepted, rejected = quality_gate([project], radar_config)

    assert accepted == [project]
    assert rejected == []


def test_quality_gate_rejects_archived_fork_empty_and_excluded_candidates(
    repo_factory, radar_config
) -> None:
    config = radar_config.model_copy(
        update={"exclusions": ExclusionConfig(repositories=["acme/excluded"], keywords=["malware"])}
    )
    archived = repo_factory(repository_id=1, archived=True)
    fork = repo_factory(repository_id=2, fork=True)
    empty = repo_factory(
        repository_id=3, readme="", license_spdx=None, has_skill_md=False, has_mcp=False
    )
    excluded_name = repo_factory(repository_id=4, full_name="ACME/EXCLUDED")
    excluded_keyword = repo_factory(repository_id=5, description="Useful MALWARE detector")
    accepted = repo_factory(repository_id=6, readme="Install with pip", license_spdx="MIT")

    kept, rejected = quality_gate(
        [archived, fork, empty, excluded_name, excluded_keyword, accepted], config
    )

    assert kept == [accepted]
    assert [(item.repository.repository_id, item.reason) for item in rejected] == [
        (1, "archived"),
        (2, "fork"),
        (3, "empty shell"),
        (4, "explicit exclusion"),
        (5, "excluded keyword"),
    ]
