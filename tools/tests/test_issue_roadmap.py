from __future__ import annotations

import json
import re
import sys
from http import client
from pathlib import Path
from urllib import error, request
from urllib.parse import parse_qs, urlparse

import pytest

from tools import issue_roadmap as roadmap
from tools.issue_roadmap import (
    Issue,
    escape_markdown,
    issue_kind,
    ordered_issues,
    render_readme,
    render_stage,
)


def make_issue(
    number: int,
    created_at: str,
    *,
    title: str = "Example",
    labels: tuple[str, ...] = (),
) -> Issue:
    return Issue(
        number=number,
        title=title,
        created_at=created_at,
        updated_at=created_at,
        labels=labels,
        url=f"https://github.com/JefferyHcool/BiliNote/issues/{number}",
        author="tester",
    )


def make_api_item() -> dict[str, object]:
    return {
        "number": 42,
        "title": "  Example issue  ",
        "created_at": "2026-07-01T00:00:00Z",
        "updated_at": "2026-07-02T00:00:00Z",
        "labels": [{"name": " bug "}, {"name": ""}, {"name": None}],
        "html_url": "https://github.com/JefferyHcool/BiliNote/issues/42",
        "user": {"login": "tester"},
    }


def directory_snapshot(root: Path) -> dict[str, bytes | None]:
    return {
        path.relative_to(root).as_posix(): (
            None if path.is_dir() else path.read_bytes()
        )
        for path in root.rglob("*")
    }


def api_issue(number: int, pull_request: bool = False) -> dict[str, object]:
    item: dict[str, object] = {
        "number": number,
        "title": f"Issue {number}",
        "created_at": f"2026-07-{number:02d}T00:00:00Z",
        "updated_at": f"2026-07-{number:02d}T12:00:00Z",
        "labels": [],
        "html_url": f"https://github.com/JefferyHcool/BiliNote/issues/{number}",
        "user": {"login": "tester"},
    }
    if pull_request:
        item["pull_request"] = {"url": "https://api.github.com/pulls/99"}
    return item


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def test_github_get_json_sets_required_headers_token_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(
        api_request: request.Request, *, timeout: int
    ) -> FakeResponse:
        captured["method"] = api_request.get_method()
        captured["headers"] = {
            key.casefold(): value for key, value in api_request.header_items()
        }
        captured["timeout"] = timeout
        return FakeResponse(b"[]")

    monkeypatch.setattr(request, "urlopen", fake_urlopen)

    result = roadmap.github_get_json(
        "https://api.github.com/repos/owner/repo/issues", "test-token"
    )

    assert result == []
    assert captured["method"] == "GET"
    assert captured["timeout"] == 30
    assert captured["headers"] == {
        "accept": "application/vnd.github+json",
        "user-agent": "BiliNote-issue-roadmap",
        "x-github-api-version": "2022-11-28",
        "authorization": "Bearer test-token",
    }


def test_github_get_json_keeps_token_out_of_redirected_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_request: request.Request | None = None

    def fake_urlopen(
        api_request: request.Request, *, timeout: int
    ) -> FakeResponse:
        del timeout
        nonlocal captured_request
        captured_request = api_request
        return FakeResponse(b"[]")

    monkeypatch.setattr(request, "urlopen", fake_urlopen)

    roadmap.github_get_json(
        "https://api.github.com/repos/owner/repo/issues", "test-token"
    )

    assert captured_request is not None
    assert "Authorization" not in captured_request.headers
    assert captured_request.unredirected_hdrs["Authorization"] == (
        "Bearer test-token"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://api.github.com/repos/owner/repo/issues",
        "https://example.com/repos/owner/repo/issues",
        "https://api.github.com.example.com/repos/owner/repo/issues",
    ],
)
def test_github_get_json_rejects_unsafe_token_destination_before_network(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    network_called = False

    def fail_if_called(*args: object, **kwargs: object) -> FakeResponse:
        del args, kwargs
        nonlocal network_called
        network_called = True
        raise AssertionError("network must not be called")

    monkeypatch.setattr(request, "urlopen", fail_if_called)

    with pytest.raises(ValueError, match="HTTPS api.github.com"):
        roadmap.github_get_json(url, "secret-token")

    assert network_called is False


def test_github_get_json_omits_authorization_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_headers: dict[str, str] = {}

    def fake_urlopen(
        api_request: request.Request, *, timeout: int
    ) -> FakeResponse:
        del timeout
        captured_headers.update(
            {key.casefold(): value for key, value in api_request.header_items()}
        )
        return FakeResponse(b"[]")

    monkeypatch.setattr(request, "urlopen", fake_urlopen)

    roadmap.github_get_json("https://api.github.com/issues", None)

    assert "authorization" not in captured_headers


@pytest.mark.parametrize(
    "failure",
    [
        error.HTTPError("https://api.github.com", 500, "failed", {}, None),
        error.URLError("failed"),
        TimeoutError("failed"),
        client.IncompleteRead(b"partial", 10),
    ],
)
def test_github_get_json_retries_transient_failures_three_times(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    attempts = 0
    sleeps: list[int] = []

    def failing_urlopen(
        api_request: request.Request, *, timeout: int
    ) -> FakeResponse:
        del api_request, timeout
        nonlocal attempts
        attempts += 1
        raise failure

    monkeypatch.setattr(request, "urlopen", failing_urlopen)
    monkeypatch.setattr("time.sleep", sleeps.append)

    with pytest.raises(
        RuntimeError,
        match="GitHub issue request failed after 3 attempts:",
    ):
        roadmap.github_get_json("https://api.github.com/issues", None)

    assert attempts == 3
    assert sleeps == [1, 2]


def test_github_get_json_does_not_retry_non_transient_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def failing_urlopen(
        api_request: request.Request, *, timeout: int
    ) -> FakeResponse:
        del api_request, timeout
        nonlocal attempts
        attempts += 1
        raise ValueError("invalid request")

    monkeypatch.setattr(request, "urlopen", failing_urlopen)

    with pytest.raises(ValueError, match="invalid request"):
        roadmap.github_get_json("https://api.github.com/issues", None)

    assert attempts == 1


def test_github_get_json_rejects_non_list_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        request,
        "urlopen",
        lambda api_request, timeout: FakeResponse(b'{"message": "failed"}'),
    )

    with pytest.raises(RuntimeError, match="JSON list"):
        roadmap.github_get_json("https://api.github.com/issues", None)


def test_fetch_open_issues_paginates_and_filters_pull_requests() -> None:
    calls: list[tuple[dict[str, list[str]], str | None]] = []

    def fake_request(url: str, token: str | None) -> list[dict[str, object]]:
        query = parse_qs(urlparse(url).query)
        page = int(query["page"][0])
        calls.append((query, token))
        if page == 1:
            return [api_issue(3), api_issue(99, pull_request=True)]
        if page == 2:
            return [api_issue(2)]
        raise AssertionError(f"unexpected page {page}")

    issues = roadmap.fetch_open_issues(
        "JefferyHcool/BiliNote",
        token="test-token",
        per_page=2,
        request_json=fake_request,
    )

    expected_query = {
        "state": ["open"],
        "sort": ["created"],
        "direction": ["desc"],
        "per_page": ["2"],
    }
    assert calls == [
        ({**expected_query, "page": ["1"]}, "test-token"),
        ({**expected_query, "page": ["2"]}, "test-token"),
    ]
    assert [issue.number for issue in issues] == [3, 2]


def test_fetch_open_issues_stops_when_payload_exceeds_page_size() -> None:
    request_count = 0

    def fake_request(url: str, token: str | None) -> list[dict[str, object]]:
        del url, token
        nonlocal request_count
        request_count += 1
        if request_count > 1:
            raise AssertionError("unexpected additional page request")
        return [api_issue(1), api_issue(3), api_issue(2)]

    issues = roadmap.fetch_open_issues(
        "JefferyHcool/BiliNote",
        per_page=2,
        request_json=fake_request,
    )

    assert request_count == 1
    assert [issue.number for issue in issues] == [3, 2, 1]


def test_fetch_open_issues_rejects_a_repeated_full_page() -> None:
    request_count = 0

    def fake_request(url: str, token: str | None) -> list[dict[str, object]]:
        del url, token
        nonlocal request_count
        request_count += 1
        if request_count > 2:
            raise AssertionError("pagination loop guard did not stop")
        return [api_issue(2), api_issue(1)]

    with pytest.raises(RuntimeError, match="repeated full page"):
        roadmap.fetch_open_issues(
            "JefferyHcool/BiliNote",
            per_page=2,
            request_json=fake_request,
        )

    assert request_count == 2


@pytest.mark.parametrize("repo", ["", "owner", "/repo", "owner/"])
def test_fetch_open_issues_rejects_invalid_repo(repo: str) -> None:
    with pytest.raises(ValueError, match="owner/name"):
        roadmap.fetch_open_issues(repo)


@pytest.mark.parametrize("per_page", [0, 101])
def test_fetch_open_issues_rejects_per_page_outside_github_range(
    per_page: int,
) -> None:
    request_called = False

    def fail_if_called(url: str, token: str | None) -> list[dict[str, object]]:
        del url, token
        nonlocal request_called
        request_called = True
        raise AssertionError("request must not be called")

    with pytest.raises(ValueError, match="per_page must be between 1 and 100"):
        roadmap.fetch_open_issues(
            "owner/repo", per_page=per_page, request_json=fail_if_called
        )

    assert request_called is False


def test_write_roadmap_creates_snapshot_readme_and_all_stages(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "roadmap"
    issues = ordered_issues(
        [
            make_issue(number, f"2026-07-{number:02d}T00:00:00Z")
            for number in range(1, 12)
        ]
    )

    roadmap.write_roadmap(
        issues,
        output_dir,
        snapshot_date="2026-07-15",
        expected_count=11,
        repository="owner/repo",
        stage_size=10,
    )

    assert sorted(path.name for path in output_dir.iterdir()) == [
        "README.md",
        "issues-snapshot.json",
        "stage-01.md",
        "stage-02.md",
    ]
    snapshot_bytes = (output_dir / "issues-snapshot.json").read_bytes()
    assert snapshot_bytes.endswith(b"\n")
    assert not snapshot_bytes.endswith(b"\n\n")
    snapshot = json.loads(snapshot_bytes.decode("utf-8"))
    assert snapshot == {
        "schema_version": 1,
        "repository": "owner/repo",
        "snapshot_date": "2026-07-15",
        "ordering": "created_at desc, number desc",
        "stage_size": 10,
        "issues": [issue.to_source_dict() for issue in issues],
    }
    assert [item["number"] for item in snapshot["issues"]] == list(
        range(11, 0, -1)
    )
    assert all(
        "status" not in item and "disposition" not in item
        for item in snapshot["issues"]
    )
    assert (output_dir / "README.md").read_text(
        encoding="utf-8"
    ) == render_readme(
        issues, "2026-07-15", repository="owner/repo", stage_size=10
    )
    assert (output_dir / "stage-01.md").read_text(
        encoding="utf-8"
    ) == render_stage(issues, stage=1, stage_size=10)
    assert (output_dir / "stage-02.md").read_text(
        encoding="utf-8"
    ) == render_stage(issues, stage=2, stage_size=10)


def test_write_roadmap_removes_obsolete_stage_files_on_rerun(
    tmp_path: Path,
) -> None:
    initial_issues = ordered_issues(
        [
            make_issue(number, f"2026-07-{number:02d}T00:00:00Z")
            for number in range(1, 12)
        ]
    )
    roadmap.write_roadmap(
        initial_issues,
        tmp_path,
        snapshot_date="2026-07-15",
        expected_count=11,
        stage_size=10,
    )

    remaining_issues = initial_issues[:5]
    roadmap.write_roadmap(
        remaining_issues,
        tmp_path,
        snapshot_date="2026-07-16",
        expected_count=5,
        stage_size=10,
    )

    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "README.md",
        "issues-snapshot.json",
        "stage-01.md",
    ]


def test_write_roadmap_refuses_output_with_unmanaged_file(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "roadmap"
    output_dir.mkdir()
    existing_files = {
        "README.md": b"# Existing roadmap\n",
        "issues-snapshot.json": b'{"generation": "old"}\n',
        "stage-01.md": b"# Existing stage\n",
        "unrelated.txt": b"must survive\n",
    }
    for filename, contents in existing_files.items():
        (output_dir / filename).write_bytes(contents)
    before = directory_snapshot(output_dir)

    with pytest.raises(
        RuntimeError,
        match=r"publication refused.*unrelated\.txt",
    ):
        roadmap.write_roadmap(
            [make_issue(1, "2026-07-01T00:00:00Z")],
            output_dir,
            snapshot_date="2026-07-16",
            expected_count=1,
        )

    assert directory_snapshot(output_dir) == before
    assert not list(tmp_path.glob(".roadmap.*"))


def test_write_roadmap_refuses_managed_name_that_is_a_directory(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "roadmap"
    output_dir.mkdir()
    (output_dir / "README.md").write_bytes(b"# Existing roadmap\n")
    (output_dir / "issues-snapshot.json").write_bytes(
        b'{"generation": "old"}\n'
    )
    unrelated_dir = output_dir / "stage-42.md"
    unrelated_dir.mkdir()
    (unrelated_dir / "sentinel.txt").write_bytes(b"must survive\n")
    before = directory_snapshot(output_dir)

    with pytest.raises(
        RuntimeError,
        match=r"publication refused.*stage-42\.md",
    ):
        roadmap.write_roadmap(
            [make_issue(1, "2026-07-01T00:00:00Z")],
            output_dir,
            snapshot_date="2026-07-16",
            expected_count=1,
        )

    assert directory_snapshot(output_dir) == before
    assert not list(tmp_path.glob(".roadmap.*"))


def test_write_roadmap_replaces_valid_managed_set_with_stale_stage(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "roadmap"
    output_dir.mkdir()
    (output_dir / "README.md").write_bytes(b"# Existing roadmap\n")
    (output_dir / "issues-snapshot.json").write_bytes(
        b'{"generation": "old"}\n'
    )
    (output_dir / "stage-01.md").write_bytes(b"# Existing stage\n")
    (output_dir / "stage-99.md").write_bytes(b"# Stale managed stage\n")

    issue = make_issue(1, "2026-07-01T00:00:00Z")
    roadmap.write_roadmap(
        [issue],
        output_dir,
        snapshot_date="2026-07-16",
        expected_count=1,
    )

    assert sorted(path.name for path in output_dir.iterdir()) == [
        "README.md",
        "issues-snapshot.json",
        "stage-01.md",
    ]
    assert (output_dir / "stage-01.md").read_text(
        encoding="utf-8"
    ) == render_stage([issue], stage=1)
    assert not list(tmp_path.glob(".roadmap.*"))


def test_write_roadmap_temp_failure_preserves_existing_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    initial_issues = ordered_issues(
        [
            make_issue(number, f"2026-07-{number:02d}T00:00:00Z")
            for number in range(1, 12)
        ]
    )
    roadmap.write_roadmap(
        initial_issues,
        tmp_path,
        snapshot_date="2026-07-15",
        expected_count=11,
        stage_size=10,
    )
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    original_write_text = Path.write_text
    write_count = 0

    def fail_second_write(path: Path, *args: object, **kwargs: object) -> int:
        nonlocal write_count
        write_count += 1
        if write_count == 2:
            raise OSError("injected temp write failure")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_second_write)

    with pytest.raises(OSError, match="injected temp write failure"):
        roadmap.write_roadmap(
            initial_issues[:5],
            tmp_path,
            snapshot_date="2026-07-16",
            expected_count=5,
            stage_size=10,
        )

    after = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    assert after == before
    assert not [
        path
        for path in tmp_path.parent.iterdir()
        if path.name.startswith(f".{tmp_path.name}.")
    ]


def test_write_roadmap_restores_old_generation_when_install_rename_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "roadmap"
    output_dir.mkdir()
    old_generation = {
        "issues-snapshot.json": b'{"generation": "old"}\n',
        "README.md": b"# Old roadmap\n",
        "stage-01.md": b"# Old stage\n",
    }
    for filename, contents in old_generation.items():
        (output_dir / filename).write_bytes(contents)

    original_replace = roadmap.os.replace
    directory_replace_count = 0
    failed_replace_count: int | None = None

    def fail_second_directory_replace(
        source: str | Path, destination: str | Path
    ) -> None:
        nonlocal directory_replace_count, failed_replace_count
        if Path(source).is_dir():
            directory_replace_count += 1
            if directory_replace_count == 2:
                failed_replace_count = directory_replace_count
                raise OSError("injected staging install failure")
        original_replace(source, destination)

    monkeypatch.setattr(roadmap.os, "replace", fail_second_directory_replace)

    issues = ordered_issues(
        [
            make_issue(number, f"2026-07-{number:02d}T00:00:00Z")
            for number in range(1, 6)
        ]
    )
    with pytest.raises(OSError, match="injected staging install failure"):
        roadmap.write_roadmap(
            issues,
            output_dir,
            snapshot_date="2026-07-16",
            expected_count=5,
            stage_size=10,
        )

    assert failed_replace_count == 2
    assert directory_replace_count == 3
    assert {
        path.name: path.read_bytes() for path in output_dir.iterdir()
    } == old_generation
    assert not [
        path
        for path in tmp_path.iterdir()
        if path.name.startswith(".roadmap.")
    ]


def test_write_roadmap_retains_backup_when_rollback_rename_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "roadmap"
    output_dir.mkdir()
    old_generation = {
        "issues-snapshot.json": b'{"generation": "old"}\n',
        "README.md": b"# Old roadmap\n",
        "stage-01.md": b"# Old stage\n",
    }
    for filename, contents in old_generation.items():
        (output_dir / filename).write_bytes(contents)

    original_replace = roadmap.os.replace
    directory_replace_count = 0

    def fail_install_and_restore(
        source: str | Path, destination: str | Path
    ) -> None:
        nonlocal directory_replace_count
        if Path(source).is_dir():
            directory_replace_count += 1
            if directory_replace_count == 2:
                output_dir.mkdir()
                (output_dir / "interloper.txt").write_text(
                    "concurrent output", encoding="utf-8"
                )
                raise OSError("injected staging install failure")
            if directory_replace_count == 3:
                raise OSError("injected backup restore failure")
        original_replace(source, destination)

    monkeypatch.setattr(roadmap.os, "replace", fail_install_and_restore)

    issues = ordered_issues(
        [
            make_issue(number, f"2026-07-{number:02d}T00:00:00Z")
            for number in range(1, 6)
        ]
    )
    with pytest.raises(
        OSError, match="injected staging install failure"
    ) as caught:
        roadmap.write_roadmap(
            issues,
            output_dir,
            snapshot_date="2026-07-16",
            expected_count=5,
            stage_size=10,
        )

    assert directory_replace_count == 3
    assert any(
        "injected backup restore failure" in note
        for note in caught.value.__notes__
    )
    assert not list(tmp_path.glob(".roadmap.staging-*"))
    backups = list(tmp_path.glob(".roadmap.backup-*"))
    assert len(backups) == 1
    assert {
        path.name: path.read_bytes() for path in backups[0].iterdir()
    } == old_generation


def test_write_roadmap_rejects_existing_non_directory_output(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "roadmap"
    old_contents = b"not a roadmap directory\n"
    output_path.write_bytes(old_contents)
    issues = [make_issue(1, "2026-07-01T00:00:00Z")]

    with pytest.raises(NotADirectoryError, match="not a directory"):
        roadmap.write_roadmap(
            issues,
            output_path,
            snapshot_date="2026-07-16",
            expected_count=1,
        )

    assert output_path.read_bytes() == old_contents
    assert not [
        path
        for path in tmp_path.iterdir()
        if path.name.startswith(".roadmap.")
    ]


def test_write_roadmap_rejects_unexpected_issue_count(tmp_path: Path) -> None:
    issues = [make_issue(1, "2026-07-01T00:00:00Z")]

    with pytest.raises(
        RuntimeError, match="expected 160 open issues, got 1"
    ):
        roadmap.write_roadmap(
            issues,
            tmp_path,
            snapshot_date="2026-07-15",
            expected_count=160,
        )

    assert list(tmp_path.iterdir()) == []


def test_write_roadmap_rejects_cross_page_duplicates_when_count_matches(
    tmp_path: Path,
) -> None:
    def fake_request(url: str, token: str | None) -> list[dict[str, object]]:
        del token
        page = int(parse_qs(urlparse(url).query)["page"][0])
        pages = {
            1: [api_issue(3), api_issue(2)],
            2: [api_issue(2), api_issue(1)],
            3: [],
        }
        return pages[page]

    issues = roadmap.fetch_open_issues(
        "JefferyHcool/BiliNote",
        per_page=2,
        request_json=fake_request,
    )

    with pytest.raises(
        RuntimeError, match=r"duplicate issue numbers.*\[2\]"
    ):
        roadmap.write_roadmap(
            issues,
            tmp_path,
            snapshot_date="2026-07-15",
            expected_count=4,
            stage_size=10,
        )

    assert list(tmp_path.iterdir()) == []


def test_parse_args_uses_expected_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "issue_roadmap.py",
            "--snapshot-date",
            "2026-07-15",
            "--expected-count",
            "11",
            "--output-dir",
            str(tmp_path),
        ],
    )

    args = roadmap.parse_args()

    assert args.repo == "JefferyHcool/BiliNote"
    assert args.snapshot_date == "2026-07-15"
    assert args.expected_count == 11
    assert args.stage_size == 10
    assert args.output_dir == tmp_path


def test_main_forwards_environment_token_and_reports_export(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    issues = [
        make_issue(number, f"2026-07-{number:02d}T00:00:00Z")
        for number in range(1, 12)
    ]
    calls: dict[str, object] = {}

    def fake_fetch(repo: str, token: str | None = None) -> list[Issue]:
        calls["fetch"] = (repo, token)
        return issues

    def fake_write(
        fetched_issues: list[Issue],
        output_dir: Path,
        snapshot_date: str,
        expected_count: int,
        repository: str = "JefferyHcool/BiliNote",
        stage_size: int = 10,
    ) -> None:
        calls["write"] = (
            fetched_issues,
            output_dir,
            snapshot_date,
            expected_count,
            repository,
            stage_size,
        )

    monkeypatch.setattr(roadmap, "fetch_open_issues", fake_fetch, raising=False)
    monkeypatch.setattr(roadmap, "write_roadmap", fake_write, raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "issue_roadmap.py",
            "--repo",
            "owner/repo",
            "--snapshot-date",
            "2026-07-15",
            "--expected-count",
            "11",
            "--output-dir",
            str(tmp_path),
        ],
    )

    roadmap.main()

    assert calls["fetch"] == ("owner/repo", "test-token")
    assert calls["write"] == (
        issues,
        tmp_path,
        "2026-07-15",
        11,
        "owner/repo",
        10,
    )
    assert capsys.readouterr().out == "exported 11 open issues across 2 stages\n"


def test_ordered_issues_uses_created_at_then_number_descending() -> None:
    issues = [
        make_issue(10, "2026-07-01T00:00:00Z"),
        make_issue(12, "2026-07-02T00:00:00Z"),
        make_issue(11, "2026-07-02T00:00:00Z"),
    ]

    result = ordered_issues(issues)

    assert [issue.number for issue in result] == [12, 11, 10]


def test_render_readme_assigns_ten_issues_per_stage_and_escapes_titles() -> None:
    issues = [
        make_issue(
            number,
            f"2026-07-{number:02d}T00:00:00Z",
            title="A | B" if number == 11 else f"Issue {number}",
            labels=("bug",) if number % 2 else ("enhancement",),
        )
        for number in range(1, 12)
    ]

    rendered = render_readme(
        ordered_issues(issues), "2026-07-15", stage_size=10
    )

    assert "共 11 条 open issue，分为 2 个阶段" in rendered
    assert (
        "| 顺序 | 阶段 | Issue | 标题 | 创建时间 | 工作区 | 类型 | "
        "当前状态 | 处置 | 详情 |"
    ) in rendered
    assert (
        "[#11](https://github.com/JefferyHcool/BiliNote/issues/11)"
        in rendered
    )
    assert "| A \\| B |" in rendered
    assert "| 10 | [01](stage-01.md) |" in rendered
    assert "| 11 | [02](stage-02.md) |" in rendered
    assert "| 未分诊 |" in rendered
    assert "| `queued` | 未判定 |" in rendered
    assert "[查看详情](stage-01.md#issue-11)" in rendered
    assert "[查看详情](stage-02.md#issue-1)" in rendered


def test_render_stage_contains_only_requested_stage() -> None:
    issues = [
        make_issue(number, f"2026-07-{number:02d}T00:00:00Z")
        for number in range(1, 12)
    ]
    ordered = ordered_issues(issues)

    rendered = render_stage(ordered, stage=2, stage_size=10)

    assert "# Issue Remediation Stage 02" in rendered
    assert "[返回总表](README.md)" in rendered
    assert "| 顺序 | Issue | 标题 | 创建时间 | 类型 |" in rendered
    assert "| 状态 |" not in rendered
    assert "| 处置 |" not in rendered
    assert "[#1]" in rendered
    assert "[#2]" not in rendered
    assert '<a id="issue-1"></a>' in rendered
    assert '<a id="issue-2"></a>' not in rendered
    assert "`queued`" not in rendered
    assert "| 未判定 |" not in rendered
    assert "- 完成情况：0/1" in rendered


def test_render_stage_has_ten_audit_ready_anchored_details_and_review() -> None:
    issues = ordered_issues(
        [
            make_issue(number, f"2026-07-{number:02d}T00:00:00Z")
            for number in range(1, 11)
        ]
    )

    rendered = render_stage(issues, stage=1, stage_size=10)

    for issue in issues:
        assert rendered.count(f'<a id="issue-{issue.number}"></a>') == 1
    for label in (
        "工作区",
        "正文与评论摘要",
        "当前版本核查",
        "根因",
        "修改范围",
        "复现或核查证据",
        "分支和提交",
        "验证命令与结果",
        "残余风险或解除阻塞条件",
    ):
        assert rendered.count(f"- {label}：") == 10
    assert "## 阶段回顾" in rendered
    assert "- 阶段状态：尚未开始" in rendered
    assert "- 完成情况：0/10" in rendered
    assert "- 阻塞项：尚未评估" in rendered
    assert "- 回归结果：尚未开始" in rendered
    assert "TODO" not in rendered
    assert "TBD" not in rendered


def test_readme_canonical_rows_resolve_to_matching_stage_details() -> None:
    issues = ordered_issues(
        [
            make_issue(number, f"2026-07-{number:02d}T00:00:00Z")
            for number in range(1, 12)
        ]
    )
    readme = render_readme(issues, "2026-07-15", stage_size=10)
    stages = {
        f"stage-{stage:02d}.md": render_stage(
            issues, stage=stage, stage_size=10
        )
        for stage in (1, 2)
    }

    detail_links = re.findall(
        r"\[查看详情\]\((stage-\d{2}\.md)#(issue-\d+)\)", readme
    )
    stage_links = re.findall(r"\[(\d{2})\]\((stage-\d{2}\.md)\)", readme)

    assert len(detail_links) == len(issues)
    assert len(stage_links) == len(issues)
    assert readme.count("| 未分诊 |") == len(issues)
    assert readme.count("| `queued` | 未判定 |") == len(issues)
    for filename, anchor in detail_links:
        assert filename in stages
        assert f'<a id="{anchor}"></a>' in stages[filename]
    for stage_number, filename in stage_links:
        assert filename == f"stage-{stage_number}.md"
    assert all("`queued`" not in stage for stage in stages.values())
    assert all("| 处置 |" not in stage for stage in stages.values())


def test_issue_from_api_normalizes_and_serializes_source_metadata() -> None:
    item = make_api_item()
    item["title"] = "   "
    item["user"] = None

    issue = Issue.from_api(item)

    assert issue == Issue(
        number=42,
        title="(untitled)",
        created_at="2026-07-01T00:00:00Z",
        updated_at="2026-07-02T00:00:00Z",
        labels=("bug",),
        url="https://github.com/JefferyHcool/BiliNote/issues/42",
        author="unknown",
    )
    assert issue.to_source_dict() == {
        "number": 42,
        "title": "(untitled)",
        "created_at": "2026-07-01T00:00:00Z",
        "updated_at": "2026-07-02T00:00:00Z",
        "labels": ["bug"],
        "url": "https://github.com/JefferyHcool/BiliNote/issues/42",
        "author": "unknown",
    }
    assert not hasattr(issue, "status")
    assert not hasattr(issue, "disposition")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("created_at", None),
        ("created_at", "   "),
        ("updated_at", None),
        ("updated_at", ""),
        ("html_url", None),
        ("html_url", "   "),
    ],
)
def test_issue_from_api_rejects_missing_or_empty_required_metadata(
    field: str, value: object
) -> None:
    item = make_api_item()
    if value is None:
        item.pop(field)
    else:
        item[field] = value

    with pytest.raises(ValueError, match=field):
        Issue.from_api(item)


def test_issue_kind_prioritizes_known_labels_and_sorts_custom_labels() -> None:
    bug = make_issue(1, "2026-07-01", labels=("enhancement", "bug"))

    assert issue_kind(bug) == "bug"
    assert (
        issue_kind(make_issue(2, "2026-07-01", labels=("custom", "Enhancement")))
        == "enhancement"
    )
    assert (
        issue_kind(make_issue(3, "2026-07-01", labels=("Zeta", "alpha", "ALPHA")))
        == "alpha, zeta"
    )
    assert issue_kind(make_issue(4, "2026-07-01")) == "unlabeled"


def test_escape_markdown_escapes_backslashes_before_pipes() -> None:
    assert escape_markdown(r"A\|B") == r"A\\\|B"


def test_renderers_escape_all_dynamic_table_cells() -> None:
    issue = make_issue(
        1,
        "2026-07-01T00:00:00Z",
        title=r"Title\|pipe",
        labels=(r"Custom\|Label",),
    )

    readme = render_readme([issue], "2026-07-15")
    stage = render_stage([issue], stage=1)

    assert r"| Title\\\|pipe |" in readme
    assert r"| custom\\\|label |" in readme
    assert r"| Title\\\|pipe |" in stage
    assert r"| custom\\\|label |" in stage


@pytest.mark.parametrize("stage_size", [0, -1])
def test_render_readme_rejects_non_positive_stage_size(stage_size: int) -> None:
    with pytest.raises(ValueError, match="stage_size must be greater than zero"):
        render_readme([], "2026-07-15", stage_size=stage_size)


@pytest.mark.parametrize("stage_size", [0, -1])
def test_render_stage_rejects_non_positive_stage_size(stage_size: int) -> None:
    with pytest.raises(ValueError, match="stage_size must be greater than zero"):
        render_stage([], stage=1, stage_size=stage_size)


def test_render_stage_rejects_invalid_or_unavailable_stage() -> None:
    issue = make_issue(1, "2026-07-01T00:00:00Z")

    with pytest.raises(ValueError, match="stage must be at least 1"):
        render_stage([issue], stage=0)
    with pytest.raises(ValueError, match=r"stage 2 exceeds available stages \(1\)"):
        render_stage([issue], stage=2)
    with pytest.raises(ValueError, match=r"stage 1 exceeds available stages \(0\)"):
        render_stage([], stage=1)
