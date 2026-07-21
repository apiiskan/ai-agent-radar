from datetime import datetime, timedelta, timezone

from ai_agent_radar.config import ExclusionConfig, QualityPolicyConfig
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
        (1, "archived without recent verified release"),
        (2, "fork without independent development evidence"),
        (3, "empty shell"),
        (4, "explicit exclusion"),
        (5, "excluded keyword"),
    ]


def test_archived_repository_requires_verified_recent_release(repo_factory, radar_config) -> None:
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    recent = repo_factory(
        repository_id=1,
        archived=True,
        latest_release="v2",
        latest_release_published_at=now - timedelta(days=30),
        release_detail_valid=True,
    )
    stale = repo_factory(
        repository_id=2,
        archived=True,
        latest_release="v1",
        latest_release_published_at=now - timedelta(days=120),
        release_detail_valid=True,
    )
    unknown = repo_factory(
        repository_id=3,
        archived=True,
        latest_release="v3",
        latest_release_published_at=None,
        release_detail_valid=False,
    )

    accepted, rejected = quality_gate([recent, stale, unknown], radar_config, now=now)

    assert accepted == [recent]
    assert [item.reason for item in rejected] == [
        "archived without recent verified release",
        "archived without recent verified release",
    ]


def test_fork_requires_independent_development_evidence(repo_factory, radar_config) -> None:
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    independent = repo_factory(
        repository_id=1,
        fork=True,
        fork_ahead_by=5,
        parent_pushed_at=now - timedelta(days=10),
        pushed_at=now - timedelta(days=1),
    )
    mirror = repo_factory(
        repository_id=2,
        fork=True,
        fork_ahead_by=1,
        parent_pushed_at=now - timedelta(days=1),
        pushed_at=now - timedelta(days=1),
    )

    accepted, rejected = quality_gate([independent, mirror], radar_config, now=now)

    assert accepted == [independent]
    assert rejected[0].reason == "fork without independent development evidence"


def test_empty_shell_checks_code_and_runnable_entrypoints(repo_factory, radar_config) -> None:
    executable = repo_factory(
        repository_id=1,
        readme="",
        license_spdx=None,
        has_executable_code=True,
    )
    runnable = repo_factory(
        repository_id=2,
        readme="",
        license_spdx=None,
        has_runnable_entrypoint=True,
    )
    shell = repo_factory(
        repository_id=3,
        readme="",
        license_spdx="MIT",
        has_skill_md=False,
        has_mcp=False,
        has_executable_code=False,
        has_runnable_entrypoint=False,
    )

    accepted, rejected = quality_gate([executable, runnable, shell], radar_config)

    assert accepted == [executable, runnable]
    assert rejected[0].reason == "empty shell"


def test_keyword_stuffing_and_unrelated_content_are_rejected(repo_factory, radar_config) -> None:
    stuffing = repo_factory(
        repository_id=1,
        description="agent agent codex claude mcp grok kimi agent skill mcp",
        readme="",
        has_executable_code=True,
    )
    unrelated = repo_factory(
        repository_id=2,
        description="Recipe organizer for family dinners",
        topics=("cooking",),
        readme="Install this recipe organizer",
    )

    accepted, rejected = quality_gate([stuffing, unrelated], radar_config)

    assert accepted == []
    assert [item.reason for item in rejected] == ["keyword stuffing", "unrelated content"]


def test_relevance_requires_whole_tokens_not_agent_substrings(repo_factory, radar_config) -> None:
    unrelated = repo_factory(
        repository_id=1,
        description="Management dashboard for property agencies",
        topics=("management",),
        readme="Install the management dashboard",
    )

    accepted, rejected = quality_gate([unrelated], radar_config)

    assert accepted == []
    assert rejected[0].reason == "unrelated content"


def test_official_policy_can_bypass_relevance_but_not_base_quality(repo_factory, radar_config) -> None:
    config = radar_config.model_copy(
        update={
            "quality": QualityPolicyConfig(
                official_organizations=["acme"],
                trusted_topics=["trusted-agent"],
                allow_official_relevance_exception=True,
            )
        }
    )
    official = repo_factory(
        repository_id=1,
        description="Developer utilities",
        topics=(),
        readme="Install developer utilities",
    )
    official_shell = repo_factory(
        repository_id=2,
        description="Developer utilities",
        topics=(),
        readme="",
        license_spdx=None,
        has_skill_md=False,
        has_mcp=False,
    )

    accepted, rejected = quality_gate([official, official_shell], config)

    assert accepted == [official]
    assert rejected[0].reason == "empty shell"
