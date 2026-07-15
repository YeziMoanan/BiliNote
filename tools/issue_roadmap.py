from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import stat
import tempfile
import time
from dataclasses import dataclass
from http import client
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

_MANAGED_ROADMAP_FILENAMES = {"README.md", "issues-snapshot.json"}
_STAGE_FILENAME = re.compile(r"stage-\d{2}\.md", flags=re.ASCII)


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    created_at: str
    updated_at: str
    labels: tuple[str, ...]
    url: str
    author: str

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

    def to_source_dict(self) -> dict[str, object]:
        return {
            "number": self.number,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "labels": list(self.labels),
            "url": self.url,
            "author": self.author,
        }


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
        destination = parse.urlsplit(url)
        if (
            destination.scheme != "https"
            or destination.hostname != "api.github.com"
        ):
            raise ValueError(
                "authenticated requests require an HTTPS api.github.com URL"
            )

    for attempt in range(3):
        try:
            api_request = request.Request(
                url, headers=headers, method="GET"
            )
            if token:
                api_request.add_unredirected_header(
                    "Authorization", f"Bearer {token}"
                )
            with request.urlopen(api_request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (
            client.IncompleteRead,
            error.HTTPError,
            error.URLError,
            TimeoutError,
        ) as exc:
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
    if not 1 <= per_page <= 100:
        raise ValueError("per_page must be between 1 and 100")

    owner, name = repo_parts
    endpoint = (
        "https://api.github.com/repos/"
        f"{parse.quote(owner, safe='')}/{parse.quote(name, safe='')}/issues"
    )
    api_issues: list[Issue] = []
    full_page_signatures: set[tuple[tuple[str, bool], ...]] = set()
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
        if len(payload) == per_page:
            signature = tuple(
                (str(item.get("number")), "pull_request" in item)
                for item in payload
            )
            if signature in full_page_signatures:
                raise RuntimeError(
                    "GitHub issue pagination returned a repeated full page"
                )
            full_page_signatures.add(signature)
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
    issues: list[Issue],
    snapshot_date: str,
    *,
    repository: str = "JefferyHcool/BiliNote",
    stage_size: int = 10,
) -> str:
    _validate_stage_size(stage_size)
    stage_count = math.ceil(len(issues) / stage_size)
    bugs = sum(issue_kind(issue) == "bug" for issue in issues)
    enhancements = sum(issue_kind(issue) == "enhancement" for issue in issues)
    unclassified = len(issues) - bugs - enhancements
    lines = [
        "# Issue Remediation Ledger",
        "",
        f"> Snapshot: {snapshot_date}. Source: `{repository}`.",
        "",
        f"共 {len(issues)} 条 open issue，分为 {stage_count} 个阶段。",
        f"分类：bug {bugs} 条，enhancement {enhancements} 条，其他或未分类 {unclassified} 条。",
        "",
        "排序规则：`created_at` 降序；创建时间相同时按 issue 编号降序。",
        "",
        "| 顺序 | 阶段 | Issue | 标题 | 创建时间 | 工作区 | 类型 | 当前状态 | 处置 | 详情 |",
        "| ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for position, issue in enumerate(issues, start=1):
        stage = (position - 1) // stage_size + 1
        stage_filename = f"stage-{stage:02d}.md"
        stage_link = f"[{stage:02d}]({stage_filename})"
        issue_link = f"[#{issue.number}]({escape_markdown(issue.url)})"
        detail_link = (
            f"[查看详情]({stage_filename}#issue-{issue.number})"
        )
        lines.append(
            f"| {position} | {stage_link} | {issue_link} | "
            f"{escape_markdown(issue.title)} | "
            f"{escape_markdown(issue.created_at[:10])} | 未分诊 | "
            f"{escape_markdown(issue_kind(issue))} | `queued` | "
            f"未判定 | {detail_link} |"
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
        "[返回总表](README.md)",
        "",
        f"范围：全量时间序中的第 {start + 1}-{start + len(selected)} 条。",
        "",
        "| 顺序 | Issue | 标题 | 创建时间 | 类型 |",
        "| ---: | --- | --- | --- | --- |",
    ]

    for offset, issue in enumerate(selected, start=start + 1):
        issue_link = f"[#{issue.number}]({escape_markdown(issue.url)})"
        lines.append(
            f"| {offset} | {issue_link} | {escape_markdown(issue.title)} | "
            f"{escape_markdown(issue.created_at[:10])} | "
            f"{escape_markdown(issue_kind(issue))} |"
        )

    for issue in selected:
        lines.extend(
            [
                "",
                f'<a id="issue-{issue.number}"></a>',
                f"## Issue #{issue.number}",
                "",
                "- 工作区：未分诊",
                "- 正文与评论摘要：尚未开始",
                "- 当前版本核查：尚未开始",
                "- 根因：尚未判定",
                "- 修改范围：尚未评估",
                "- 复现或核查证据：尚未开始",
                "- 分支和提交：尚未开始",
                "- 验证命令与结果：尚未开始",
                "- 残余风险或解除阻塞条件：尚未评估",
            ]
        )

    lines.extend(
        [
            "",
            "## 阶段回顾",
            "",
            "- 阶段状态：尚未开始",
            f"- 完成情况：0/{len(selected)}",
            "- 阻塞项：尚未评估",
            "- 回归结果：尚未开始",
        ]
    )

    return "\n".join(lines) + "\n"


def write_roadmap(
    issues: list[Issue],
    output_dir: Path,
    snapshot_date: str,
    expected_count: int,
    repository: str = "JefferyHcool/BiliNote",
    stage_size: int = 10,
) -> None:
    _validate_stage_size(stage_size)
    if len(issues) != expected_count:
        raise RuntimeError(
            f"expected {expected_count} open issues, got {len(issues)}; "
            "review upstream issue state before exporting"
        )
    seen_numbers: set[int] = set()
    duplicate_numbers: set[int] = set()
    for issue in issues:
        if issue.number in seen_numbers:
            duplicate_numbers.add(issue.number)
        seen_numbers.add(issue.number)
    if duplicate_numbers:
        raise RuntimeError(
            "duplicate issue numbers in snapshot: "
            f"{sorted(duplicate_numbers)}"
        )

    snapshot = json.dumps(
        {
            "schema_version": 1,
            "repository": repository,
            "snapshot_date": snapshot_date,
            "ordering": "created_at desc, number desc",
            "stage_size": stage_size,
            "issues": [issue.to_source_dict() for issue in issues],
        },
        ensure_ascii=False,
        indent=2,
    )
    artifacts = {
        "issues-snapshot.json": snapshot + "\n",
        "README.md": render_readme(
            issues,
            snapshot_date,
            repository=repository,
            stage_size=stage_size,
        ),
    }
    stage_count = math.ceil(len(issues) / stage_size)
    for stage in range(1, stage_count + 1):
        artifacts[f"stage-{stage:02d}.md"] = render_stage(
            issues, stage=stage, stage_size=stage_size
        )

    if output_dir.exists() and not output_dir.is_dir():
        raise NotADirectoryError(
            f"roadmap output path is not a directory: {output_dir}"
        )
    if output_dir.exists():
        unmanaged_entries: list[str] = []
        for entry in output_dir.iterdir():
            entry_stat = entry.stat(follow_symlinks=False)
            file_attributes = getattr(entry_stat, "st_file_attributes", 0)
            is_reparse_point = bool(
                file_attributes
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            )
            has_managed_name = (
                entry.name in _MANAGED_ROADMAP_FILENAMES
                or _STAGE_FILENAME.fullmatch(entry.name) is not None
            )
            if (
                not has_managed_name
                or not stat.S_ISREG(entry_stat.st_mode)
                or is_reparse_point
            ):
                unmanaged_entries.append(entry.name)
        if unmanaged_entries:
            formatted_entries = ", ".join(
                repr(name) for name in sorted(unmanaged_entries)
            )
            raise RuntimeError(
                "roadmap publication refused; unmanaged output entries: "
                f"{formatted_entries}"
            )

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir: Path | None = Path(
        tempfile.mkdtemp(
            dir=output_dir.parent,
            prefix=f".{output_dir.name}.staging-",
        )
    )
    backup_dir: Path | None = None
    remove_backup = False
    try:
        for filename, contents in artifacts.items():
            (staging_dir / filename).write_text(
                contents, encoding="utf-8", newline="\n"
            )

        if not output_dir.exists():
            os.replace(staging_dir, output_dir)
            staging_dir = None
            return

        backup_dir = Path(
            tempfile.mkdtemp(
                dir=output_dir.parent,
                prefix=f".{output_dir.name}.backup-",
            )
        )
        backup_dir.rmdir()
        os.replace(output_dir, backup_dir)
        try:
            os.replace(staging_dir, output_dir)
        except BaseException as publication_error:
            try:
                os.replace(backup_dir, output_dir)
            except BaseException as restore_error:
                publication_error.add_note(
                    "failed to restore the previous roadmap generation from "
                    f"{backup_dir}: {restore_error}"
                )
            else:
                backup_dir = None
            raise

        staging_dir = None
        remove_backup = True
        shutil.rmtree(backup_dir, ignore_errors=True)
        if not backup_dir.exists():
            backup_dir = None
    finally:
        if staging_dir is not None:
            shutil.rmtree(staging_dir, ignore_errors=True)
        if backup_dir is not None and remove_backup:
            shutil.rmtree(backup_dir, ignore_errors=True)


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
        repository=args.repo,
        stage_size=args.stage_size,
    )
    stage_count = math.ceil(len(issues) / args.stage_size)
    print(
        f"exported {len(issues)} open issues across {stage_count} stages"
    )


if __name__ == "__main__":
    main()
