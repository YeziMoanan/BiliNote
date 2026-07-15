from __future__ import annotations

import json
import sys
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
    status: str = "queued",
    disposition: str = "-",
) -> Issue:
    return Issue(
        number=number,
        title=title,
        created_at=created_at,
        updated_at=created_at,
        labels=labels,
        url=f"https://github.com/JefferyHcool/BiliNote/issues/{number}",
        author="tester",
        status=status,
        disposition=disposition,
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


@pytest.mark.parametrize("repo", ["", "owner", "/repo", "owner/"])
def test_fetch_open_issues_rejects_invalid_repo(repo: str) -> None:
    with pytest.raises(ValueError, match="owner/name"):
        roadmap.fetch_open_issues(repo)


def test_write_roadmap_creates_snapshot_readme_and_all_stages(
    tmp_path: Path,
) -> None:
    issues = ordered_issues(
        [
            make_issue(number, f"2026-07-{number:02d}T00:00:00Z")
            for number in range(1, 12)
        ]
    )

    roadmap.write_roadmap(
        issues,
        tmp_path,
        snapshot_date="2026-07-15",
        expected_count=11,
        stage_size=10,
    )

    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "README.md",
        "issues-snapshot.json",
        "stage-01.md",
        "stage-02.md",
    ]
    snapshot_bytes = (tmp_path / "issues-snapshot.json").read_bytes()
    assert snapshot_bytes.endswith(b"\n")
    assert not snapshot_bytes.endswith(b"\n\n")
    snapshot = json.loads(snapshot_bytes.decode("utf-8"))
    assert [item["number"] for item in snapshot] == list(range(11, 0, -1))
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == render_readme(
        issues, "2026-07-15", stage_size=10
    )
    assert (tmp_path / "stage-01.md").read_text(
        encoding="utf-8"
    ) == render_stage(issues, stage=1, stage_size=10)
    assert (tmp_path / "stage-02.md").read_text(
        encoding="utf-8"
    ) == render_stage(issues, stage=2, stage_size=10)


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
        stage_size: int = 10,
    ) -> None:
        calls["write"] = (
            fetched_issues,
            output_dir,
            snapshot_date,
            expected_count,
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
            "--snapshot-date",
            "2026-07-15",
            "--expected-count",
            "11",
            "--output-dir",
            str(tmp_path),
        ],
    )

    roadmap.main()

    assert calls["fetch"] == ("JefferyHcool/BiliNote", "test-token")
    assert calls["write"] == (
        issues,
        tmp_path,
        "2026-07-15",
        11,
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
        "[#11](https://github.com/JefferyHcool/BiliNote/issues/11) A \\| B"
        in rendered
    )
    assert "| 10 | 1 |" in rendered
    assert "| 11 | 2 |" in rendered


def test_render_stage_contains_only_requested_stage() -> None:
    issues = [
        make_issue(number, f"2026-07-{number:02d}T00:00:00Z")
        for number in range(1, 12)
    ]
    ordered = ordered_issues(issues)

    rendered = render_stage(ordered, stage=2, stage_size=10)

    assert "# Issue Remediation Stage 02" in rendered
    assert "[#1]" in rendered
    assert "[#2]" not in rendered
    assert "`queued`" in rendered


def test_issue_from_api_normalizes_metadata_and_json_labels() -> None:
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
    assert issue.to_json_dict() == {
        "number": 42,
        "title": "(untitled)",
        "created_at": "2026-07-01T00:00:00Z",
        "updated_at": "2026-07-02T00:00:00Z",
        "labels": ["bug"],
        "url": "https://github.com/JefferyHcool/BiliNote/issues/42",
        "author": "unknown",
        "status": "queued",
        "disposition": "-",
    }


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
        status="queued | blocked",
        disposition=r"owner\|review",
    )

    readme = render_readme([issue], "2026-07-15")
    stage = render_stage([issue], stage=1)

    assert (
        r"| 1 | 1 | [#1](https://github.com/JefferyHcool/BiliNote/issues/1) "
        r"Title\\\|pipe | 2026-07-01 | custom\\\|label | `queued \| blocked` | "
        r"owner\\\|review |"
    ) in readme
    assert (
        r"| 1 | [#1](https://github.com/JefferyHcool/BiliNote/issues/1) "
        r"Title\\\|pipe | 2026-07-01 | custom\\\|label | `queued \| blocked` | "
        r"owner\\\|review | - | 尚未开始 |"
    ) in stage


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
