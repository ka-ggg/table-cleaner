"""v2.5.0 W6 发布验收（规划 5.11 / G-15、G-16）。

只读仓库文件，不访问网络、不依赖 GitHub PR 状态；tag 尚未创建，因此不检查 tag。
四项分别覆盖：版本四处一致、发布关键数字、CI 覆盖率/mypy 门禁、无自动合并配置。
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import cleaning

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = PROJECT_ROOT / "CHANGELOG.md"
README = PROJECT_ROOT / "README.md"
TEST_REPORT = PROJECT_ROOT / "TEST_REPORT.md"
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "tests.yml"


def _read(path: Path) -> str:
    assert path.is_file(), f"缺少发布文档：{path.relative_to(PROJECT_ROOT)}"
    return path.read_text(encoding="utf-8")


def test_version_is_consistent_in_four_places() -> None:
    """pyproject / cleaning.__version__ / CHANGELOG / README 的当前版本一致为 2.5.0。"""
    pyproject = tomllib.loads(_read(PROJECT_ROOT / "pyproject.toml"))
    changelog = _read(CHANGELOG)
    readme = _read(README)

    version = cleaning.__version__
    assert pyproject["project"]["version"] == version
    assert version == "2.5.0"

    latest = re.search(r"^## \[(\d+\.\d+\.\d+)\] — ", changelog, re.MULTILINE)
    assert latest is not None, "CHANGELOG 缺少「## [x.y.z] — 日期」格式的版本条目"
    assert latest.group(1) == version

    assert f"当前版本：v{version}" in readme
    assert f"## v{version} 的改动（当前版本）" in readme


def test_changelog_and_test_report_contain_release_numbers() -> None:
    """CHANGELOG 与 TEST_REPORT 必须包含实测发布口径：670、96%、94%、mypy 0。"""
    for path in (CHANGELOG, TEST_REPORT):
        text = _read(path)
        for number in ("670", "674", "96%", "94%", "mypy 0"):
            assert number in text, f"{path.name} 缺少发布数字 {number}"


def test_ci_workflow_has_coverage_and_mypy_gates() -> None:
    """CI 配置必须真的跑 coverage 并公布两个口径，且 mypy 是独立 job。"""
    workflow = _read(WORKFLOW)
    assert "coverage run -m pytest" in workflow
    assert "coverage report --include='cleaning/*'" in workflow
    assert "coverage report --include='app.py'" in workflow
    assert re.search(r"^  mypy:\s*$", workflow, re.MULTILINE)
    assert re.search(r"^      - run: mypy$", workflow, re.MULTILINE)


def test_repository_has_no_auto_merge_automation() -> None:
    """静态检查：发布准备不允许自动合并，仓库的 GitHub 配置里不能出现 auto-merge 动作。"""
    for path in (PROJECT_ROOT / ".github").rglob("*"):
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8").lower()
        for marker in (
            "gh pr merge --auto",
            "--enable-auto-merge",
            "enable-auto-merge",
            "allow_auto_merge",
            "enable_pull_request_automerge",
        ):
            assert marker not in content, f"{path.relative_to(PROJECT_ROOT)} 出现自动合并配置：{marker}"
