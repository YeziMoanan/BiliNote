from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Iterable


__all__ = [
    "Issue",
    "escape_markdown",
    "issue_kind",
    "ordered_issues",
    "render_readme",
    "render_stage",
]


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    created_at: str
    updated_at: str
    labels: tuple[str, ...]
    url: str
    author: str
    status: str = "queued"
    disposition: str = "-"

    @classmethod
    def from_api(cls, item: dict) -> Issue:
        labels = tuple(
            name
            for label in item.get("labels", ())
            if (name := str(label.get("name") or "").strip())
        )
        user = item.get("user") or {}
        author = str(user.get("login") or "unknown").strip() or "unknown"

        return cls(
            number=int(item["number"]),
            title=str(item.get("title") or "").strip() or "(untitled)",
            created_at=str(item.get("created_at") or ""),
            updated_at=str(item.get("updated_at") or ""),
            labels=labels,
            url=str(item.get("html_url") or ""),
            author=author,
        )

    def to_json_dict(self) -> dict:
        data = asdict(self)
        data["labels"] = list(self.labels)
        return data


def ordered_issues(issues: Iterable[Issue]) -> list[Issue]:
    return sorted(issues, key=lambda issue: (issue.created_at, issue.number), reverse=True)


def issue_kind(issue: Issue) -> str:
    label_set = {label.casefold() for label in issue.labels}
    if "bug" in label_set:
        return "bug"
    if "enhancement" in label_set:
        return "enhancement"
    if not label_set:
        return "unlabeled"
    return ", ".join(sorted(label_set))


def escape_markdown(value: str) -> str:
    return " ".join(value.split()).replace("|", r"\|")


def render_readme(
    issues: list[Issue], snapshot_date: str, *, stage_size: int = 10
) -> str:
    stage_count = math.ceil(len(issues) / stage_size)
    bugs = sum(issue_kind(issue) == "bug" for issue in issues)
    enhancements = sum(issue_kind(issue) == "enhancement" for issue in issues)
    unclassified = len(issues) - bugs - enhancements
    lines = [
        "# Issue Remediation Ledger",
        "",
        f"> Snapshot: {snapshot_date}. Source: `JefferyHcool/BiliNote`.",
        "",
        f"共 {len(issues)} 条 open issue，分为 {stage_count} 个阶段。",
        f"分类：bug {bugs} 条，enhancement {enhancements} 条，其他或未分类 {unclassified} 条。",
        "",
        "排序规则：`created_at` 降序；创建时间相同时按 issue 编号降序。",
        "",
        "| 顺序 | 阶段 | Issue | 创建时间 | 类型 | 状态 | 处置 |",
        "| ---: | ---: | --- | --- | --- | --- | --- |",
    ]

    for position, issue in enumerate(issues, start=1):
        stage = (position - 1) // stage_size + 1
        issue_link = f"[#{issue.number}]({issue.url}) {escape_markdown(issue.title)}"
        lines.append(
            f"| {position} | {stage} | {issue_link} | {issue.created_at[:10]} | "
            f"{issue_kind(issue)} | `{issue.status}` | {issue.disposition} |"
        )

    return "\n".join(lines) + "\n"


def render_stage(
    issues: list[Issue], *, stage: int, stage_size: int = 10
) -> str:
    start = (stage - 1) * stage_size
    selected = issues[start : start + stage_size]
    lines = [
        f"# Issue Remediation Stage {stage:02d}",
        "",
        f"范围：全量时间序中的第 {start + 1}-{start + len(selected)} 条。",
        "",
        "| 顺序 | Issue | 创建时间 | 类型 | 状态 | 处置 | 分支/提交 | 验证 |",
        "| ---: | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for offset, issue in enumerate(selected, start=start + 1):
        issue_link = f"[#{issue.number}]({issue.url}) {escape_markdown(issue.title)}"
        lines.append(
            f"| {offset} | {issue_link} | {issue.created_at[:10]} | {issue_kind(issue)} | "
            f"`{issue.status}` | {issue.disposition} | - | 尚未开始 |"
        )

    return "\n".join(lines) + "\n"
