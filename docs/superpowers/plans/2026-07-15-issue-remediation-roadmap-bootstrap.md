# Issue Remediation Roadmap Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible GitHub issue exporter that snapshots all 160 open upstream issues, sorts them newest-first, and generates the master ledger plus 16 ten-issue stage files.

**Architecture:** A small standard-library Python module will separate GitHub REST fetching, issue normalization, chronological ordering, Markdown rendering, and filesystem output. Unit tests will inject API pages without network access; one explicit CLI run will create the real 2026-07-15 snapshot and verify that stage 1 starts with the approved ten issue numbers.

**Tech Stack:** Python 3.11+, standard library (`argparse`, `dataclasses`, `json`, `pathlib`, `urllib`), pytest, GitHub REST API

---

## File Structure

- Create `tools/__init__.py`: mark the repository tooling directory as an importable package.
- Create `tools/issue_roadmap.py`: fetch, normalize, sort, render, and write the issue roadmap.
- Create `tools/tests/test_issue_roadmap.py`: unit tests for ordering, pagination, PR filtering, rendering, output, and count guards.
- Create `docs/issue-remediation/issues-snapshot.json`: structured metadata snapshot for all 160 issues.
- Create `docs/issue-remediation/README.md`: master chronological ledger.
- Create `docs/issue-remediation/stage-01.md` through `stage-16.md`: ten-issue stage ledgers.

### Task 1: Add the issue model and deterministic Markdown renderer

**Files:**
- Create: `tools/__init__.py`
- Create: `tools/issue_roadmap.py`
- Create: `tools/tests/test_issue_roadmap.py`

- [ ] **Step 1: Write failing renderer tests**

Create `tools/__init__.py` as an empty file. Create `tools/tests/test_issue_roadmap.py` with:

```python
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


def test_ordered_issues_uses_created_at_then_number_descending():
    issues = [
        make_issue(10, "2026-07-01T00:00:00Z"),
        make_issue(12, "2026-07-02T00:00:00Z"),
        make_issue(11, "2026-07-02T00:00:00Z"),
    ]

    result = ordered_issues(issues)

    assert [issue.number for issue in result] == [12, 11, 10]


def test_render_readme_assigns_ten_issues_per_stage_and_escapes_titles():
    issues = [
        make_issue(
            number,
            f"2026-07-{number:02d}T00:00:00Z",
            title="A | B" if number == 11 else f"Issue {number}",
            labels=("bug",) if number % 2 else ("enhancement",),
        )
        for number in range(1, 12)
    ]

    markdown = render_readme(ordered_issues(issues), "2026-07-15", stage_size=10)

    assert "共 11 条 open issue，分为 2 个阶段" in markdown
    assert "[#11](https://github.com/JefferyHcool/BiliNote/issues/11) A \\| B" in markdown
    assert "| 10 | 1 |" in markdown
    assert "| 11 | 2 |" in markdown


def test_render_stage_contains_only_requested_stage():
    issues = [
        make_issue(number, f"2026-07-{number:02d}T00:00:00Z")
        for number in range(1, 12)
    ]
    ordered = ordered_issues(issues)

    stage_two = render_stage(ordered, stage=2, stage_size=10)

    assert "# Issue Remediation Stage 02" in stage_two
    assert "[#1]" in stage_two
    assert "[#2]" not in stage_two
    assert "`queued`" in stage_two
```

- [ ] **Step 2: Run the tests and confirm the module is missing**

Run:

```powershell
python -m pytest tools/tests/test_issue_roadmap.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'tools.issue_roadmap'`.

- [ ] **Step 3: Implement the model, ordering, and renderers**

Create `tools/issue_roadmap.py` with:

```python
from __future__ import annotations

import argparse
import json
import math
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


JsonRequest = Callable[[str, str | None], list[dict]]


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
    def from_api(cls, item: dict) -> "Issue":
        return cls(
            number=int(item["number"]),
            title=str(item.get("title") or "(untitled)").strip(),
            created_at=str(item["created_at"]),
            updated_at=str(item["updated_at"]),
            labels=tuple(
                str(label["name"])
                for label in item.get("labels", [])
                if label.get("name")
            ),
            url=str(item["html_url"]),
            author=str((item.get("user") or {}).get("login") or "unknown"),
        )

    def to_json_dict(self) -> dict:
        data = asdict(self)
        data["labels"] = list(self.labels)
        return data


def ordered_issues(issues: Iterable[Issue]) -> list[Issue]:
    return sorted(issues, key=lambda issue: (issue.created_at, issue.number), reverse=True)


def issue_kind(issue: Issue) -> str:
    label_set = {label.lower() for label in issue.labels}
    if "bug" in label_set:
        return "bug"
    if "enhancement" in label_set:
        return "enhancement"
    if not label_set:
        return "unlabeled"
    return ", ".join(sorted(label_set))


def escape_markdown(value: str) -> str:
    return " ".join(value.split()).replace("|", "\\|")


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


def render_stage(issues: list[Issue], *, stage: int, stage_size: int = 10) -> str:
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
```

- [ ] **Step 4: Run the renderer tests**

Run:

```powershell
python -m pytest tools/tests/test_issue_roadmap.py -v
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit the renderer**

```powershell
git add tools/__init__.py tools/issue_roadmap.py tools/tests/test_issue_roadmap.py
git commit -m "feat(tools): add issue roadmap renderer"
```

### Task 2: Add paginated GitHub fetching and guarded output

**Files:**
- Modify: `tools/issue_roadmap.py`
- Modify: `tools/tests/test_issue_roadmap.py`

- [ ] **Step 1: Add failing API and output tests**

Append to `tools/tests/test_issue_roadmap.py`:

```python
import json
from urllib.parse import parse_qs, urlparse

import pytest

from tools.issue_roadmap import fetch_open_issues, write_roadmap


def api_issue(number: int, *, pull_request: bool = False) -> dict:
    item = {
        "number": number,
        "title": f"Issue {number}",
        "created_at": f"2026-07-{number:02d}T00:00:00Z",
        "updated_at": f"2026-07-{number:02d}T00:00:00Z",
        "labels": [{"name": "bug"}],
        "html_url": f"https://github.com/JefferyHcool/BiliNote/issues/{number}",
        "user": {"login": "tester"},
    }
    if pull_request:
        item["pull_request"] = {"url": "https://api.github.com/pulls/1"}
    return item


def test_fetch_open_issues_paginates_and_filters_pull_requests():
    pages = {
        1: [api_issue(3), api_issue(99, pull_request=True)],
        2: [api_issue(2)],
    }

    def fake_request(url: str, token: str | None) -> list[dict]:
        assert token == "test-token"
        page = int(parse_qs(urlparse(url).query)["page"][0])
        return pages[page]

    result = fetch_open_issues(
        "JefferyHcool/BiliNote",
        token="test-token",
        per_page=2,
        request_json=fake_request,
    )

    assert [issue.number for issue in result] == [3, 2]


def test_write_roadmap_creates_snapshot_master_and_all_stages(tmp_path):
    issues = ordered_issues(
        [make_issue(number, f"2026-07-{number:02d}T00:00:00Z") for number in range(1, 12)]
    )

    write_roadmap(
        issues,
        output_dir=tmp_path,
        snapshot_date="2026-07-15",
        expected_count=11,
        stage_size=10,
    )

    snapshot = json.loads((tmp_path / "issues-snapshot.json").read_text(encoding="utf-8"))
    assert [item["number"] for item in snapshot] == [11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
    assert (tmp_path / "README.md").is_file()
    assert (tmp_path / "stage-01.md").is_file()
    assert (tmp_path / "stage-02.md").is_file()


def test_write_roadmap_rejects_unexpected_issue_count(tmp_path):
    with pytest.raises(RuntimeError, match="expected 160 open issues, got 1"):
        write_roadmap(
            [make_issue(1, "2026-07-01T00:00:00Z")],
            output_dir=tmp_path,
            snapshot_date="2026-07-15",
            expected_count=160,
        )
```

- [ ] **Step 2: Run the new tests and confirm the imports fail**

Run:

```powershell
python -m pytest tools/tests/test_issue_roadmap.py -v
```

Expected: collection fails because `fetch_open_issues` and `write_roadmap` do not exist.

- [ ] **Step 3: Implement GitHub fetching, retries, output, and the CLI**

Append to `tools/issue_roadmap.py` after `render_stage`:

```python
def github_get_json(url: str, token: str | None) -> list[dict]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "BiliNote-issue-roadmap",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            request = Request(url, headers=headers)
            with urlopen(request, timeout=30) as response:
                payload = json.load(response)
            if not isinstance(payload, list):
                raise RuntimeError("GitHub issue endpoint returned a non-list payload")
            return payload
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2**attempt)
    raise RuntimeError(f"GitHub issue request failed after 3 attempts: {last_error}")


def fetch_open_issues(
    repo: str,
    *,
    token: str | None = None,
    per_page: int = 100,
    request_json: JsonRequest = github_get_json,
) -> list[Issue]:
    parts = repo.split("/", maxsplit=1)
    if len(parts) != 2 or not all(parts):
        raise ValueError("repo must use owner/name format")

    page = 1
    issues: list[Issue] = []
    while True:
        query = urlencode(
            {
                "state": "open",
                "sort": "created",
                "direction": "desc",
                "per_page": per_page,
                "page": page,
            }
        )
        payload = request_json(
            f"https://api.github.com/repos/{repo}/issues?{query}", token
        )
        issues.extend(
            Issue.from_api(item) for item in payload if "pull_request" not in item
        )
        if len(payload) < per_page:
            break
        page += 1
    return ordered_issues(issues)


def write_roadmap(
    issues: list[Issue],
    *,
    output_dir: Path,
    snapshot_date: str,
    expected_count: int,
    stage_size: int = 10,
) -> None:
    if len(issues) != expected_count:
        raise RuntimeError(
            f"expected {expected_count} open issues, got {len(issues)}; "
            "review upstream state before regenerating the approved snapshot"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot = [issue.to_json_dict() for issue in issues]
    (output_dir / "issues-snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "README.md").write_text(
        render_readme(issues, snapshot_date, stage_size=stage_size),
        encoding="utf-8",
    )
    stage_count = math.ceil(len(issues) / stage_size)
    for stage in range(1, stage_count + 1):
        (output_dir / f"stage-{stage:02d}.md").write_text(
            render_stage(issues, stage=stage, stage_size=stage_size),
            encoding="utf-8",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export the BiliNote issue roadmap")
    parser.add_argument("--repo", default="JefferyHcool/BiliNote")
    parser.add_argument("--snapshot-date", required=True)
    parser.add_argument("--expected-count", required=True, type=int)
    parser.add_argument("--stage-size", default=10, type=int)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    issues = fetch_open_issues(args.repo, token=os.getenv("GITHUB_TOKEN"))
    write_roadmap(
        issues,
        output_dir=args.output_dir,
        snapshot_date=args.snapshot_date,
        expected_count=args.expected_count,
        stage_size=args.stage_size,
    )
    stage_count = math.ceil(len(issues) / args.stage_size)
    print(f"exported {len(issues)} open issues across {stage_count} stages")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run all exporter tests**

Run:

```powershell
python -m pytest tools/tests/test_issue_roadmap.py -v
```

Expected: 6 tests pass.

- [ ] **Step 5: Commit fetching and guarded output**

```powershell
git add tools/issue_roadmap.py tools/tests/test_issue_roadmap.py
git commit -m "feat(tools): fetch and export issue roadmap"
```

### Task 3: Generate the approved 160-issue snapshot

**Files:**
- Create: `docs/issue-remediation/issues-snapshot.json`
- Create: `docs/issue-remediation/README.md`
- Create: `docs/issue-remediation/stage-01.md`
- Create: `docs/issue-remediation/stage-02.md`
- Create: `docs/issue-remediation/stage-03.md`
- Create: `docs/issue-remediation/stage-04.md`
- Create: `docs/issue-remediation/stage-05.md`
- Create: `docs/issue-remediation/stage-06.md`
- Create: `docs/issue-remediation/stage-07.md`
- Create: `docs/issue-remediation/stage-08.md`
- Create: `docs/issue-remediation/stage-09.md`
- Create: `docs/issue-remediation/stage-10.md`
- Create: `docs/issue-remediation/stage-11.md`
- Create: `docs/issue-remediation/stage-12.md`
- Create: `docs/issue-remediation/stage-13.md`
- Create: `docs/issue-remediation/stage-14.md`
- Create: `docs/issue-remediation/stage-15.md`
- Create: `docs/issue-remediation/stage-16.md`

- [ ] **Step 1: Export the live upstream snapshot with the approved count guard**

Run:

```powershell
python -m tools.issue_roadmap `
  --repo JefferyHcool/BiliNote `
  --snapshot-date 2026-07-15 `
  --expected-count 160 `
  --stage-size 10 `
  --output-dir docs/issue-remediation
```

Expected: `exported 160 open issues across 16 stages`.

- [ ] **Step 2: Verify the snapshot count, order, and first stage**

Run:

```powershell
python -c "import json, pathlib; p=pathlib.Path('docs/issue-remediation/issues-snapshot.json'); data=json.loads(p.read_text(encoding='utf-8')); assert set(data)=={'schema_version','repository','snapshot_date','ordering','stage_size','issues'}; assert data['schema_version']==1; assert data['repository']=='JefferyHcool/BiliNote'; assert data['snapshot_date']=='2026-07-15'; assert data['ordering']=='created_at desc, number desc'; assert data['stage_size']==10; items=data['issues']; assert len(items)==160; assert items==sorted(items, key=lambda x:(x['created_at'],x['number']), reverse=True); assert [x['number'] for x in items[:10]]==[420,419,417,416,415,404,401,400,395,392]; print('snapshot-ok')"
```

Expected: `snapshot-ok`.

- [ ] **Step 3: Verify all stage files contain ten issues**

Run:

```powershell
python -c "import pathlib; files=sorted(pathlib.Path('docs/issue-remediation').glob('stage-*.md')); required=('工作区：','正文与评论摘要：','当前版本核查：','根因：','修改范围：','复现或核查证据：','分支和提交：','验证命令与结果：','残余风险或解除阻塞条件：'); texts=[path.read_text(encoding='utf-8') for path in files]; assert len(files)==16; assert all(text.count('id='+chr(34)+'issue-')==10 and all(text.count('- '+section)==10 for section in required) for text in texts); print('stages-ok')"
```

Expected: `stages-ok`.

- [ ] **Step 4: Confirm generation is deterministic**

Run the export command from Step 1 a second time, then run:

```powershell
git add -N -- docs/issue-remediation
git diff --exit-code -- docs/issue-remediation
```

Expected: no diff and exit code 0.

- [ ] **Step 5: Commit the generated roadmap**

```powershell
git add docs/issue-remediation
git commit -m "docs: add chronological issue remediation ledger"
```

### Task 4: Run final verification and hand off to issue 420 design

**Files:**
- Verify: `tools/issue_roadmap.py`
- Verify: `tools/tests/test_issue_roadmap.py`
- Verify: `docs/issue-remediation/README.md`
- Verify: `docs/issue-remediation/stage-01.md`
- Verify: `docs/issue-remediation/issues-snapshot.json`

- [ ] **Step 1: Run the complete exporter test file**

```powershell
python -m pytest tools/tests/test_issue_roadmap.py -v
```

Expected: 6 tests pass.

- [ ] **Step 2: Check formatting and repository state**

```powershell
git diff --check
git status --short --branch
```

Expected: no whitespace errors; only this plan file may remain uncommitted if it was not included in an earlier documentation commit.

- [ ] **Step 3: Commit this implementation plan if necessary**

```powershell
git add docs/superpowers/plans/2026-07-15-issue-remediation-roadmap-bootstrap.md
git commit -m "docs: plan issue roadmap bootstrap"
```

- [ ] **Step 4: Confirm the next chronological work item**

Run:

```powershell
python -c "import json, pathlib; data=json.loads(pathlib.Path('docs/issue-remediation/issues-snapshot.json').read_text(encoding='utf-8')); issue=data['issues'][0]; assert issue['number']==420; print('next=#{} {}'.format(issue['number'], issue['title']))"
```

Expected: `next=#420 [Feature] 希望支持读取 B 站评论区内容并参与视频总结`.

After this plan is complete, return to the brainstorming workflow for the independent `#420` feature design before modifying application behavior.
