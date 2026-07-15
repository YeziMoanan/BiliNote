import pytest

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
