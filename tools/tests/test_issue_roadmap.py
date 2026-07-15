from tools.issue_roadmap import Issue, ordered_issues, render_readme, render_stage


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
