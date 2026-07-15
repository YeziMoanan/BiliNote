from __future__ import annotations

import argparse
import json
import math
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping
from urllib import error, parse, request


__all__ = [
    "Issue",
    "JsonRequest",
    "escape_markdown",
    "fetch_open_issues",
    "github_get_json",
    "issue_kind",
    "main",
    "ordered_issues",
    "parse_args",
    "render_readme",
    "render_stage",
    "write_roadmap",
]


JsonRequest = Callable[[str, str | None], list[Mapping[str, object]]]


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
    def from_api(cls, item: Mapping[str, object]) -> Issue:
        required_text: dict[str, str] = {}
        for field in ("created_at", "updated_at", "html_url"):
            value = str(item.get(field) or "").strip()
            if not value:
                raise ValueError(f"{field} must be a non-empty value")
            required_text[field] = value

        raw_labels = item.get("labels")
        api_labels = raw_labels if isinstance(raw_labels, (list, tuple)) else ()
        labels = tuple(
            name
            for label in api_labels
            if isinstance(label, Mapping)
            if (name := str(label.get("name") or "").strip())
        )
        raw_user = item.get("user")
        user = raw_user if isinstance(raw_user, Mapping) else {}
        author = str(user.get("login") or "unknown").strip() or "unknown"

        return cls(
            number=int(item["number"]),
            title=str(item.get("title") or "").strip() or "(untitled)",
            created_at=required_text["created_at"],
            updated_at=required_text["updated_at"],
            labels=labels,
            url=required_text["html_url"],
            author=author,
        )

    def to_json_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["labels"] = list(self.labels)
        return data


def ordered_issues(issues: Iterable[Issue]) -> list[Issue]:
    return sorted(issues, key=lambda issue: (issue.created_at, issue.number), reverse=True)


def github_get_json(
    url: str, token: str | None
) -> list[Mapping[str, object]]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "BiliNote-issue-roadmap",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    for attempt in range(3):
        try:
            api_request = request.Request(
                url, headers=headers, method="GET"
            )
            with request.urlopen(api_request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (error.HTTPError, error.URLError, TimeoutError) as exc:
            if attempt == 2:
                raise RuntimeError(
                    "GitHub issue request failed after 3 attempts: "
                    f"{exc}"
                ) from exc
            time.sleep(attempt + 1)
            continue

        if not isinstance(payload, list):
            raise RuntimeError("GitHub issue response must be a JSON list")
        return payload

    raise AssertionError("unreachable")


def fetch_open_issues(
    repo: str,
    token: str | None = None,
    per_page: int = 100,
    request_json: JsonRequest = github_get_json,
) -> list[Issue]:
    repo_parts = [part.strip() for part in repo.strip().split("/")]
    if len(repo_parts) != 2 or not all(repo_parts):
        raise ValueError("repo must be a non-empty owner/name")
    if per_page <= 0:
        raise ValueError("per_page must be greater than zero")

    owner, name = repo_parts
    endpoint = (
        "https://api.github.com/repos/"
        f"{parse.quote(owner, safe='')}/{parse.quote(name, safe='')}/issues"
    )
    api_issues: list[Issue] = []
    page = 1
    while True:
        query = parse.urlencode(
            {
                "state": "open",
                "sort": "created",
                "direction": "desc",
                "per_page": per_page,
                "page": page,
            }
        )
        payload = request_json(f"{endpoint}?{query}", token)
        api_issues.extend(
            Issue.from_api(item)
            for item in payload
            if "pull_request" not in item
        )
        if len(payload) != per_page:
            break
        page += 1

    return ordered_issues(api_issues)


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
    collapsed = " ".join(value.split())
    return collapsed.replace("\\", "\\\\").replace("|", r"\|")


def _validate_stage_size(stage_size: int) -> None:
    if stage_size <= 0:
        raise ValueError("stage_size must be greater than zero")


def render_readme(
    issues: list[Issue], snapshot_date: str, *, stage_size: int = 10
) -> str:
    _validate_stage_size(stage_size)
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
        issue_link = (
            f"[#{issue.number}]({escape_markdown(issue.url)}) "
            f"{escape_markdown(issue.title)}"
        )
        lines.append(
            f"| {position} | {stage} | {issue_link} | "
            f"{escape_markdown(issue.created_at[:10])} | "
            f"{escape_markdown(issue_kind(issue))} | "
            f"`{escape_markdown(issue.status)}` | "
            f"{escape_markdown(issue.disposition)} |"
        )

    return "\n".join(lines) + "\n"


def render_stage(
    issues: list[Issue], *, stage: int, stage_size: int = 10
) -> str:
    _validate_stage_size(stage_size)
    if stage < 1:
        raise ValueError("stage must be at least 1")

    stage_count = math.ceil(len(issues) / stage_size)
    if stage > stage_count:
        raise ValueError(f"stage {stage} exceeds available stages ({stage_count})")

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
        issue_link = (
            f"[#{issue.number}]({escape_markdown(issue.url)}) "
            f"{escape_markdown(issue.title)}"
        )
        lines.append(
            f"| {offset} | {issue_link} | {escape_markdown(issue.created_at[:10])} | "
            f"{escape_markdown(issue_kind(issue))} | "
            f"`{escape_markdown(issue.status)}` | "
            f"{escape_markdown(issue.disposition)} | - | 尚未开始 |"
        )

    return "\n".join(lines) + "\n"


def write_roadmap(
    issues: list[Issue],
    output_dir: Path,
    snapshot_date: str,
    expected_count: int,
    stage_size: int = 10,
) -> None:
    _validate_stage_size(stage_size)
    if len(issues) != expected_count:
        raise RuntimeError(
            f"expected {expected_count} open issues, got {len(issues)}; "
            "review upstream issue state before exporting"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot = json.dumps(
        [issue.to_json_dict() for issue in issues],
        ensure_ascii=False,
        indent=2,
    )
    (output_dir / "issues-snapshot.json").write_text(
        snapshot + "\n", encoding="utf-8", newline="\n"
    )
    (output_dir / "README.md").write_text(
        render_readme(issues, snapshot_date, stage_size=stage_size),
        encoding="utf-8",
        newline="\n",
    )

    stage_count = math.ceil(len(issues) / stage_size)
    for stage in range(1, stage_count + 1):
        (output_dir / f"stage-{stage:02d}.md").write_text(
            render_stage(issues, stage=stage, stage_size=stage_size),
            encoding="utf-8",
            newline="\n",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="JefferyHcool/BiliNote")
    parser.add_argument("--snapshot-date", required=True)
    parser.add_argument("--expected-count", required=True, type=int)
    parser.add_argument("--stage-size", default=10, type=int)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    issues = fetch_open_issues(
        args.repo, token=os.environ.get("GITHUB_TOKEN")
    )
    write_roadmap(
        issues,
        args.output_dir,
        snapshot_date=args.snapshot_date,
        expected_count=args.expected_count,
        stage_size=args.stage_size,
    )
    stage_count = math.ceil(len(issues) / args.stage_size)
    print(
        f"exported {len(issues)} open issues across {stage_count} stages"
    )


if __name__ == "__main__":
    main()
