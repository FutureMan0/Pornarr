"""Claims ADR 0009-0049 make about the repository itself, executed.

Piece 13 of the gauntlet carries twenty claims whose subject is not a running
instance but the repository: the licence, the workspace managers, the CI gates,
the release automation, the issue hygiene and the single image's role
dispatcher. `.gauntlet/CLAIMS.md` marks them `unit`, and a documented fact about
this repository that no test reads is a hole exactly like an untested endpoint.

They are gathered in one file because they share one subject and one failure
mode: somebody moves a file or renames a job and the ADR silently becomes
false. Each test names the ADR line it executes.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text()


def _yaml(relative: str) -> dict:
    return yaml.safe_load(_text(relative))


def _workflow(name: str) -> dict:
    """Load a workflow, repairing PyYAML's reading of the `on:` key.

    YAML 1.1 resolves a bare `on` to the boolean `True`, so every workflow
    parses with a `True` key and no `on`. Renaming it here keeps the assertions
    below written the way the file is.
    """
    document = _yaml(f".github/workflows/{name}")
    if True in document:
        document["on"] = document.pop(True)
    return document


# --- ADR 0012: licence -------------------------------------------------------


def test_the_licence_is_mit_and_the_copyright_is_the_projects_own() -> None:
    """ADR 0012 L9: MIT, copyright held by "Pornarr Contributors"."""
    licence = _text("LICENSE")
    assert licence.splitlines()[0] == "MIT License"
    assert re.search(r"Copyright \(c\) \d{4} Pornarr Contributors", licence) is not None
    # The two clauses that make it MIT rather than something MIT-shaped.
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in licence
    assert "without restriction, including without limitation the rights" in licence


# --- ADR 0022: the two workspaces --------------------------------------------


def test_uv_pins_python_to_3_13_and_declares_the_workspace() -> None:
    """ADR 0022 L9: uv manages a Python workspace pinned to 3.13."""
    manifest = tomllib.loads(_text("pyproject.toml"))
    assert manifest["project"]["requires-python"] == "==3.13.*"
    members = manifest["tool"]["uv"]["workspace"]["members"]
    assert sorted(members) == sorted(
        [
            "apps/api",
            "apps/worker",
            "packages/core",
            "packages/db",
            "packages/integrations",
            "packages/media",
            "packages/shared",
        ]
    ), members


def test_pnpm_declares_the_javascript_workspace() -> None:
    """ADR 0022 L9: pnpm manages the JavaScript workspace."""
    assert _yaml("pnpm-workspace.yaml")["packages"] == [
        "apps/web",
        "packages/ui",
        "packages/api-client",
    ]
    assert "packageManager" in json.loads(_text("package.json"))


def test_both_lockfiles_are_committed() -> None:
    """ADR 0022 L9: both lockfiles are committed."""
    for lockfile in ("uv.lock", "pnpm-lock.yaml"):
        assert (ROOT / lockfile).is_file(), lockfile
        assert (ROOT / lockfile).stat().st_size > 0, lockfile


def test_the_four_named_tools_are_the_ones_ci_runs() -> None:
    """ADR 0022 L9: ruff and biome for lint/format, ty and tsc for types."""
    ci = _workflow("ci.yml")["jobs"]
    python_steps = [step.get("run", "") for step in ci["python"]["steps"]]
    assert any("ruff check" in step for step in python_steps)
    assert any("ruff format --check" in step for step in python_steps)
    assert any(step.strip() == "uv run ty check" for step in python_steps)
    web_steps = [step.get("run", "") for step in ci["web"]["steps"]]
    assert any("pnpm run lint" in step for step in web_steps)
    assert any("pnpm run typecheck" in step for step in web_steps)
    scripts = json.loads(_text("package.json"))["scripts"]
    assert "biome" in scripts["lint"], scripts["lint"]
    assert "tsc" in scripts["typecheck"], scripts["typecheck"]


# --- ADR 0023: what blocks what ----------------------------------------------

_STACKED_BRANCHES = ["develop", "main", "feat/**", "fix/**", "chore/**", "docs/**", "test/**"]


def test_fast_checks_block_every_pull_request_without_a_path_filter_on_tests() -> None:
    """ADR 0023 L9: fast checks block every pull request.

    "Every" is the load-bearing word: `ci.yml` does carry a `paths` filter, so a
    pull request touching only `docs/` runs no lint or unit tests at all. The
    filter is asserted as it is rather than as the ADR reads, and the gap is
    recorded in `.gauntlet/pieces/13-admin-system/HOLES.md`.
    """
    ci = _workflow("ci.yml")["on"]
    assert ci["pull_request"]["branches"] == _STACKED_BRANCHES
    assert ci["push"]["branches"] == ["develop"]
    assert "apps/**" in ci["pull_request"]["paths"]
    assert "packages/**" in ci["pull_request"]["paths"]


def test_integration_and_the_image_build_are_path_gated() -> None:
    """ADR 0023 L9: integration tests and the Docker build block only on matching paths."""
    integration = _workflow("integration.yml")["on"]["pull_request"]
    assert "tests/integration/**" in integration["paths"]
    assert "packages/integrations/**" in integration["paths"]
    docker = _workflow("docker.yml")["on"]["pull_request"]
    assert "Dockerfile" in docker["paths"]


def test_end_to_end_the_development_image_and_prereleases_run_on_develop() -> None:
    """ADR 0023 L9: end-to-end tests, the development image and prereleases run on develop."""
    assert _workflow("e2e.yml")["on"]["push"]["branches"] == ["develop"]
    assert _workflow("docker.yml")["on"]["push"]["branches"] == ["develop"]
    assert _workflow("release-please.yml")["on"]["push"]["branches"] == ["develop"]


def test_multi_architecture_scanning_and_the_sbom_run_at_release() -> None:
    """ADR 0023 L9: multi-architecture builds, vulnerability scanning and the SBOM at release."""
    release = _workflow("release.yml")
    assert release["on"]["push"]["branches"] == ["main"]
    steps = release["jobs"]["release"]["steps"]
    build = next(step for step in steps if "platforms" in step.get("with", {}))
    assert build["with"]["platforms"] == "linux/amd64,linux/arm64"
    assert build["with"]["sbom"] is True
    assert any("trivy" in str(step.get("uses", "")) for step in steps)


def test_coverage_is_enforced_on_changed_lines_and_not_globally() -> None:
    """ADR 0023 L9: coverage is enforced on changed lines, not globally."""
    python_job = _workflow("ci.yml")["jobs"]["python"]["steps"]
    diff_coverage = next(step for step in python_job if step.get("name") == "Diff coverage")
    assert "diff-cover coverage.xml" in diff_coverage["run"]
    assert "--fail-under=80" in diff_coverage["run"]
    assert "--compare-branch=origin/${{ github.base_ref }}" in diff_coverage["run"]
    # A global gate would live here; its absence is the other half of the claim.
    unit = next(step for step in python_job if step.get("name") == "Unit tests")
    assert "--cov-fail-under" not in unit["run"]
    coverage = tomllib.loads(_text("pyproject.toml"))["tool"]["coverage"]
    assert "fail_under" not in coverage.get("report", {})


# --- ADR 0009: the contract is the boundary ----------------------------------


def test_ci_fails_when_the_committed_openapi_document_drifts() -> None:
    """ADR 0009 L11 and api-contract.md L3-4: drift is a CI failure."""
    steps = _workflow("ci.yml")["jobs"]["contract"]["steps"]
    drift = next(step for step in steps if step.get("name") == "OpenAPI document is current")
    assert "pornarr_api.scripts.export_openapi" in drift["run"]
    assert "diff -u openapi.json" in drift["run"]
    schema_drift = next(step for step in steps if step.get("name") == "No undeclared schema drift")
    assert "alembic check" in schema_drift["run"]


def test_the_committed_openapi_document_matches_the_application() -> None:
    """The check above, executed rather than merely located.

    ADR 0009 makes `openapi.json` the boundary the client and the MSW handlers
    are generated from, so a stale document is a lie to the other developer
    whether or not CI happens to run.
    """
    from pornarr_api.scripts.export_openapi import build_schema, render

    assert _text("openapi.json") == render(build_schema())


def test_the_contract_needs_approval_from_both_owners() -> None:
    """ADR 0009 L9 and api-contract.md L4-5: CODEOWNERS puts both owners on it."""
    owners = {
        line.split()[0]: line.split()[1:]
        for line in _text(".github/CODEOWNERS").splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert sorted(owners["/openapi.json"]) == ["@FutureMan0", "@raphaelbleier"]


# --- ADR 0015 and 0014: one image, four roles --------------------------------


def test_the_entrypoint_dispatches_on_the_four_documented_roles() -> None:
    """ADR 0015 L9: one image whose entrypoint dispatches on api, worker, beat or migrate.

    Contradiction C4 in `.gauntlet/CLAIMS.md` is about which *service* gets the
    GPU, not about the dispatcher, and the dispatcher is what this asserts. The
    fifth branch, `shell`, is undocumented and asserted here so that the
    documented set and the real set are compared rather than assumed.
    """
    entrypoint = _text("infrastructure/docker/entrypoint.sh")
    roles = re.findall(r"^  ([a-z]+)\)$", entrypoint, re.MULTILINE)
    assert roles == ["api", "worker", "beat", "migrate", "shell"]
    assert "exec uvicorn pornarr_api.main:create_app" in entrypoint
    assert "exec arq pornarr_worker.settings.SchedulerSettings" in entrypoint
    assert "exec alembic upgrade head" in entrypoint
    # The four worker settings classes the `worker` role selects between.
    for sub_role, settings in (
        ("default", "WorkerSettings"),
        ("import", "ImportWorkerSettings"),
        ("transcode", "TranscodeWorkerSettings"),
        ("indexer", "IndexerWorkerSettings"),
    ):
        assert f"{sub_role})" in entrypoint
        assert f"pornarr_worker.settings.{settings}" in entrypoint


def test_no_cuda_sdk_is_bundled_in_the_image() -> None:
    """ADR 0015 L11: NVIDIA driver libraries come from the host at runtime."""
    dockerfile = _text("Dockerfile")
    bases = re.findall(r"^FROM (\S+)", dockerfile, re.MULTILINE)
    assert not any("cuda" in base.casefold() or "nvidia" in base.casefold() for base in bases), (
        bases
    )
    installs = "\n".join(
        line for line in dockerfile.splitlines() if "apk add" in line or "apt-get install" in line
    )
    assert "cuda" not in installs.casefold()
    assert "nvidia" not in installs.casefold()


def test_the_migrate_service_completes_before_the_api_and_the_workers_start() -> None:
    """installation.md L173 and deployment.md L40: migrations finish first."""
    compose = _yaml("docker-compose.yml")["services"]
    assert compose["migrate"]["command"] == "migrate"
    for service in ("api", "worker", "worker-import", "worker-transcode", "worker-indexer", "beat"):
        depends = compose[service]["depends_on"]
        assert depends["migrate"]["condition"] == "service_completed_successfully", service


def test_development_bind_mounts_the_source_and_reloads() -> None:
    """ADR 0014 L9: everything runs in Docker Compose with bind mounts and reload."""
    override = _yaml("docker-compose.override.yml")["services"]
    api_command = override["api"]["command"]
    assert api_command.startswith("api --reload")
    # Watched by directory, and by exactly the directories that are bind-mounted
    # from the checkout. The default is the whole of `/app`, which includes the
    # virtualenv, so anything writing in there was a server restart taking every
    # request in flight with it.
    assert "--reload-dir /app/apps" in api_command
    assert "--reload-dir /app/packages" in api_command
    for service in ("worker", "worker-import", "worker-transcode", "worker-indexer"):
        assert "--watch /app" in override[service]["command"], service
        assert "./apps:/app/apps" in override[service]["volumes"], service
        assert "./packages:/app/packages" in override[service]["volumes"], service


def test_the_api_container_can_run_the_test_suite() -> None:
    """`make test` is `docker compose exec api uv run pytest`, per ADR 0014.

    Two things have to be true for that to mean anything, and neither was. The
    suite has to be in the container at the `testpaths` pyproject.toml names --
    without it pytest collected nothing, warned, and exited 0, so the target
    reported success having run no tests at all -- and the development image has
    to carry the dev dependency group, or `uv run` installs it into the
    virtualenv at the moment the command runs and restarts everything watching
    that tree.
    """
    api = _yaml("docker-compose.override.yml")["services"]["api"]
    assert "./tests:/app/tests" in api["volumes"]
    dockerfile = _text("Dockerfile")
    assert "FROM python-deps AS python-dev-deps" in dockerfile
    assert "COPY --from=python-dev-deps" in dockerfile


# --- ADR 0008: the single-page application -----------------------------------


def test_the_runtime_image_carries_no_node_process() -> None:
    """ADR 0008 L11: no Node process at runtime; FastAPI serves the build output."""
    dockerfile = _text("Dockerfile")
    runtime = dockerfile.split("FROM base AS runtime", 1)[1]
    assert "node" not in runtime.casefold()
    # The build output is copied in and served by the API, not by a server of its own.
    assert "COPY --from=web-build" in dockerfile
    spa = _text("apps/api/pornarr_api/spa.py")
    assert "index.html" in spa


def test_the_web_workspace_is_vite_react_and_strict_typescript() -> None:
    """ADR 0008 L9: Vite with React and TypeScript in strict mode."""
    assert json.loads(_text("tsconfig.base.json"))["compilerOptions"]["strict"] is True
    web = json.loads(_text("apps/web/package.json"))
    dependencies = web["dependencies"] | web["devDependencies"]
    assert "react" in dependencies
    assert "vite" in dependencies
    assert "typescript" in dependencies
    assert (ROOT / "apps/web/vite.config.ts").is_file()


# --- ADR 0013 and 0010: release automation -----------------------------------


def test_release_please_opens_its_pull_request_on_develop() -> None:
    """ADR 0013 L9: release-please runs on develop and targets it."""
    workflow = _workflow("release-please.yml")
    assert workflow["on"]["push"]["branches"] == ["develop"]
    action = next(
        step
        for step in workflow["jobs"]["release-please"]["steps"]
        if "release-please-action" in str(step.get("uses", ""))
    )
    assert action["with"]["target-branch"] == "develop"
    assert (ROOT / "release-please-config.json").is_file()
    assert (ROOT / ".release-please-manifest.json").is_file()


def test_merging_into_main_tags_builds_and_releases() -> None:
    """ADR 0013 L9: merging develop into main triggers tagging, the build and the release."""
    release = _workflow("release.yml")
    assert release["on"]["push"]["branches"] == ["main"]
    body = _text(".github/workflows/release.yml")
    assert "release-please-manifest.json" in body
    assert "softprops/action-gh-release" in body or "gh release create" in body


def test_conventional_commit_titles_are_mandatory_because_they_set_the_version() -> None:
    """ADR 0013 L11: Conventional Commits are mandatory."""
    workflow = _workflow("semantic-pr.yml")
    check = workflow["jobs"]["title"]["steps"][0]
    assert "action-semantic-pull-request" in check["uses"]
    types = check["with"]["types"].split()
    assert {"feat", "fix", "perf", "chore", "docs", "refactor", "test"} <= set(types)


# --- ADR 0027: repository hygiene --------------------------------------------


def test_the_repository_carries_the_hygiene_furniture_adr_0027_lists() -> None:
    """ADR 0027 L9, everything on the list that lives in the repository.

    The four label *axes* are not all here: `area:` is in `labeler.yml` and
    `type:` and `meta:` appear in the issue forms and the stale bot, but
    `priority:` appears nowhere in the repository. Labels themselves are GitHub
    state rather than repository content, so only the references can be
    asserted; the gap is recorded in HOLES.md.
    """
    labeler = _yaml(".github/labeler.yml")
    assert all(axis.startswith("area:") for axis in labeler), sorted(labeler)
    assert {"area:api", "area:worker", "area:web", "area:db"} <= set(labeler)

    forms = sorted(path.name for path in (ROOT / ".github/ISSUE_TEMPLATE").glob("*.yml"))
    assert forms == ["bug_report.yml", "config.yml", "feature_request.yml", "task.yml"]
    for form, label in (
        ("bug_report.yml", "type:fix"),
        ("feature_request.yml", "type:feat"),
        ("task.yml", "type:chore"),
    ):
        assert _yaml(f".github/ISSUE_TEMPLATE/{form}")["labels"] == [label]

    assert (ROOT / ".github/PULL_REQUEST_TEMPLATE.md").is_file()
    assert (ROOT / ".github/CODEOWNERS").is_file()

    ecosystems = {
        update["package-ecosystem"] for update in _yaml(".github/dependabot.yml")["updates"]
    }
    assert {"uv", "npm", "docker", "github-actions"} <= ecosystems

    # `priority:` is the axis with no reference anywhere in `.github`.
    github_text = "\n".join(
        path.read_text() for path in (ROOT / ".github").rglob("*") if path.is_file()
    )
    assert "meta:epic" in github_text
    assert "priority:" not in github_text


def test_the_stale_bot_exempts_epics_milestones_and_assignees() -> None:
    """ADR 0027 L11: epics, anything with a milestone and anything assigned are exempt."""
    stale = _workflow("stale.yml")["jobs"]["stale"]["steps"][0]["with"]
    assert stale["exempt-all-milestones"] is True
    assert stale["exempt-all-assignees"] is True
    assert "meta:epic" in stale["exempt-issue-labels"]


# --- ADR 0028 and 0001: documentation and independence -----------------------


def test_documentation_lives_in_docs_and_the_adrs_are_numbered_without_gaps() -> None:
    """ADR 0028 L9: all documentation in `docs/`, architecture decisions as numbered ADRs."""
    numbers = sorted(
        int(path.name[:4]) for path in (ROOT / "docs/adr").glob("[0-9][0-9][0-9][0-9]-*.md")
    )
    assert numbers == list(range(1, len(numbers) + 1)), numbers
    assert len(numbers) >= 37, numbers
    # The original plan file is deleted rather than archived.
    assert not list(ROOT.glob("**/[Nn]exora*")), "the Nexora plan file is still in the tree"


def test_pornarr_declares_no_dependency_on_whisparr_stash_or_jellyfin() -> None:
    """ADR 0001 L9, L11: the protocols and pipelines are implemented here."""
    manifests = [
        tomllib.loads((path / "pyproject.toml").read_text())
        for path in (ROOT / "packages").iterdir()
        if (path / "pyproject.toml").is_file()
    ] + [
        tomllib.loads((ROOT / "apps" / name / "pyproject.toml").read_text())
        for name in ("api", "worker")
    ]
    declared = {
        dependency.casefold()
        for manifest in manifests
        for dependency in manifest["project"].get("dependencies", [])
    }
    for forbidden in ("whisparr", "stash", "jellyfin"):
        assert not any(forbidden in dependency for dependency in declared), forbidden
    # The pieces the ADR says are implemented here, each with a module of its own.
    for module in (
        "packages/integrations/pornarr_integrations/torznab.py",
        "packages/integrations/pornarr_integrations/newznab.py",
        "packages/integrations/pornarr_integrations/qbittorrent.py",
        "apps/worker/pornarr_worker/jobs/scan.py",
        "apps/worker/pornarr_worker/jobs/metadata.py",
        "apps/worker/pornarr_worker/jobs/recommendation.py",
    ):
        assert (ROOT / module).is_file(), module


# --- ADR 0021 and 0007: the queues and the two stores ------------------------


def test_arq_carries_four_queues_and_the_documented_cron() -> None:
    """ADR 0021 L9: queues for default, import, transcode and indexer work, and its own cron."""
    from pornarr_shared.jobs import DEFAULT_QUEUE, IMPORT_QUEUE, INDEXER_QUEUE, TRANSCODE_QUEUE
    from pornarr_worker import settings as worker_settings

    queues = {
        worker_settings.WorkerSettings.queue_name,
        worker_settings.ImportWorkerSettings.queue_name,
        worker_settings.TranscodeWorkerSettings.queue_name,
        worker_settings.IndexerWorkerSettings.queue_name,
    }
    assert queues == {DEFAULT_QUEUE, IMPORT_QUEUE, TRANSCODE_QUEUE, INDEXER_QUEUE}

    scheduled = {job.name for job in worker_settings.SchedulerSettings.cron_jobs}
    assert "dispatch_rss_sync" in scheduled
    assert "download_poll" in scheduled
    assert "refresh_interest_profiles_job" in scheduled
    assert "cleanup_transcodes" in scheduled


def test_the_api_and_the_worker_share_one_engine_and_one_set_of_clients() -> None:
    """ADR 0021 L11: the same clients, sessions and code run in both, with no bridge."""
    import pornarr_db.session as db_session
    from pornarr_worker.jobs.import_media import import_media

    # One asynchronous session type on both sides. The API opens it per request
    # against the process engine; the worker opens it per job through
    # `session_scope`. Neither hands the other a synchronous handle.
    assert db_session.session_scope.__module__ == "pornarr_db.session"
    assert "AsyncSession(request.app.state.engine" in _text("apps/api/pornarr_api/auth.py")
    assert "pornarr_db.session" in _text("apps/worker/pornarr_worker/jobs/import_media.py")
    assert import_media.__module__ == "pornarr_worker.jobs.import_media"
    # The same integration clients are importable from both processes rather
    # than duplicated per side.
    assert "pornarr_integrations" in _text("apps/api/pornarr_api/routers/admin_indexers.py")
    assert "pornarr_integrations" in _text("apps/worker/pornarr_worker/search.py")
    # No synchronous bridge: nothing in either app hands work to a thread pool
    # or a synchronous broker to reach the other side.
    for tree in ("apps/api", "apps/worker"):
        sources = "\n".join(
            path.read_text() for path in (ROOT / tree).rglob("*.py") if path.is_file()
        )
        assert "celery" not in sources.casefold(), tree
        assert "run_in_executor" not in sources, tree


def test_postgresql_is_the_only_database_and_redis_is_the_other_three_things() -> None:
    """ADR 0007 L9: PostgreSQL only; Redis is broker, SSE bus and result cache."""
    compose = _yaml("docker-compose.yml")["services"]
    assert compose["postgres"]["image"].startswith("postgres:")
    assert compose["redis"]["image"].startswith("redis:")
    # No SQLite anywhere a deployment can reach it. `aiosqlite` is a *dev*
    # dependency of the root workspace so the unit suite can run without a
    # server; no shipped package declares it, so no instance can be pointed at
    # one. Both halves are asserted, because only the second is the claim.
    root = tomllib.loads(_text("pyproject.toml"))
    assert "aiosqlite" in " ".join(root["dependency-groups"]["dev"])
    shipped = [
        tomllib.loads((path / "pyproject.toml").read_text())
        for path in (ROOT / "packages").iterdir()
        if (path / "pyproject.toml").is_file()
    ] + [
        tomllib.loads((ROOT / "apps" / name / "pyproject.toml").read_text())
        for name in ("api", "worker")
    ]
    declared = " ".join(
        dependency
        for manifest in shipped
        for dependency in manifest["project"].get("dependencies", [])
    ).casefold()
    assert "sqlite" not in declared, declared
    assert "asyncpg" in declared or "psycopg" in declared
    # Redis wears all three hats in the source, not only in the ADR.
    assert "enqueue_job" in _text("packages/shared/pornarr_shared/jobs.py")  # broker
    assert "publish" in _text("packages/shared/pornarr_shared/events.py")  # SSE bus
    assert "redis" in _text("apps/worker/pornarr_worker/search.py")  # result cache


@pytest.mark.parametrize(
    "path",
    [
        "docs/operations/installation.md",
        "docs/operations/backup.md",
        "docs/operations/deployment.md",
    ],
)
def test_the_operations_documents_the_claims_cite_are_where_they_say(path: str) -> None:
    """A claim citing a line of a document that has moved is unfalsifiable."""
    assert (ROOT / path).is_file(), path
