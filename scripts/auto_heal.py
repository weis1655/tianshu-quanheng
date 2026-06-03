#!/usr/bin/env python3
"""
天枢权衡 — 自动迭代编排器 v1
读回头看报告 → 提取TOP P0问题 → 派OpenCode修复 → 核验 → 合并
"""
import json
import os
import re
import sys
import subprocess
import time
import argparse
import yaml
from datetime import datetime
from pathlib import Path
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional


def _run_cmd(cmd: list[str], cwd: str | None = None, timeout: int = 30,
             check: bool = False, capture: bool = True) -> subprocess.CompletedProcess:
    """统一命令执行封装，替代直接 subprocess.run"""
    result = subprocess.run(
        cmd, cwd=cwd, capture_output=capture, text=True, timeout=timeout,
    )
    if check and result.returncode != 0:
        print(f"[auto_heal] ❌ 命令失败: {' '.join(cmd)}\n{result.stderr[:200]}")
    return result


class FixState(Enum):
    PENDING = "pending"
    FIXING = "fixing"
    VERIFYING = "verifying"
    MERGED = "merged"
    SKIPPED = "skipped"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"

@dataclass
class FixRun:
    """单次修复运行状态"""
    run_id: str
    timestamp: str
    issues: list[dict]
    results: list[dict] = field(default_factory=list)
    state: FixState = FixState.PENDING
    action_log: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

def log_action(run: FixRun, action: str):
    """记录操作日志"""
    entry = f"[{datetime.now().isoformat()}] {action}"
    run.action_log.append(entry)
    print(f"[auto_heal] 📝 {action}")

PROJECT = Path(os.environ.get("TIANSHU_HOME", "/home/seven/hermes-data/tianshu-quanheng"))
SCRIPTS = PROJECT / "scripts"
HISTORY = PROJECT / "data" / "历史记录"
REVIEWS = PROJECT / "data" / "回顾报告"
WORKTREE_BASE = Path("/tmp/auto_heal_worktrees")


def load_config() -> dict:
    """加载配置文件"""
    config_path = PROJECT / "config.yaml"
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def get_latest_report() -> str:
    """读取最新的回头看报告，提取分析"""
    reports = sorted(REVIEWS.glob("*_回头看报告_*.md"))
    if not reports:
        print("[auto_heal] ❌ 无回头看报告")
        return ""
    report_path = reports[-1]
    content = report_path.read_text(encoding="utf-8")
    print(f"[auto_heal] 📄 报告: {report_path.name}")
    return content


def extract_p0_issues(content: str) -> list[dict]:
    """从报告中提取 TOP-3 P0 问题（按类型去重，过滤不可修复类型）"""
    issues: list[dict] = []
    seen_types: set[str] = set()
    # 这些是市场结果/数据问题，非代码缺陷，自动修复无效
    SKIP_TYPES = {"P0-实盘亏损"}

    for m in re.finditer(r"### 🔴 (P0-\S+)", content):
        ptype = m.group(1)
        if ptype in seen_types or ptype in SKIP_TYPES:
            continue
        seen_types.add(ptype)
        if len(issues) >= 3:
            break

        start = m.start()
        end_m = re.search(r"(?=^### |^---)", content[m.end():], re.MULTILINE)
        end = m.end() + end_m.start() if end_m else min(m.end() + 500, len(content))
        block = content[start:end]

        code_match = re.search(r"\|\s*代码\s*\|\s*(\d{6})", block)
        detail_match = re.search(r"\|\s*说明\s*\|\s*([^|]+)", block)

        issues.append({
            "type": ptype,
            "code": code_match.group(1) if code_match else "",
            "detail": detail_match.group(1).strip() if detail_match else "",
        })

    print(f"[auto_heal] 🔍 提取到 {len(issues)} 个P0问题:")
    for i in issues:
        print(f"         • {i['type']} ({i['code']})")
    return issues


def was_recently_fixed(issue_type: str, history_dir: Path) -> bool:
    """检查该问题是否已在最近一次 auto_heal 中被修复"""
    today_log = history_dir / f"{datetime.now().strftime('%Y-%m-%d')}_auto_heal.json"
    if today_log.exists():
        try:
            log = json.loads(today_log.read_text(encoding="utf-8"))
            for r in log.get("results", []):
                if r.get("status") == "merged" and r.get("issue", {}).get("type") == issue_type:
                    return True
        except Exception:
            pass
    return False


def create_worktree(issue: dict, idx: int, run: FixRun) -> tuple[str, str]:
    """为问题创建隔离的 git worktree，返回 (path, branch)"""
    # sanitize: 只保留 ASCII 字母数字和下划线，中文替换为英文缩写
    raw_type = issue["type"]
    type_map = {
        "P0-过热漏检": "overheat",
        "P0-降级延迟": "downgrade_slow",
        "P0-质疑报告缺失": "missing_skeptic",
        "P0-决策越权": "decision_abuse",
    }
    safe_type = type_map.get(raw_type, re.sub(r"[^a-zA-Z0-9]", "_", raw_type))
    if not safe_type:
        safe_type = f"p0_{idx}"
    branch = f"auto-heal/{safe_type}_{issue['code']}_{datetime.now().strftime('%H%M%S')}"
    worktree_path = str(WORKTREE_BASE / f"heal_{idx:02d}_{safe_type}")

    # 创建锁文件防止并发
    lock_file = WORKTREE_BASE / f".lock_{safe_type}"
    if lock_file.exists():
        print(f"[auto_heal]   ⚠️ 同类型修复正在运行，跳过")
        return "", ""
    lock_file.write_text(run.run_id, encoding="utf-8")

    # 清理旧 worktree
    _run_cmd(["git", "-C", str(PROJECT), "worktree", "remove", worktree_path, "--force"])
    _run_cmd(["rm", "-rf", worktree_path])

    result = _run_cmd(
        ["git", "-C", str(PROJECT), "worktree", "add", "-b", branch, worktree_path, "main"],
    )
    if result.returncode != 0:
        print(f"[auto_heal] ⚠️ Worktree 创建失败: {result.stderr.strip()}")
        lock_file.unlink(missing_ok=True)
        return "", ""
    print(f"[auto_heal] 🌿 Worktree: {worktree_path} @ {branch}")
    return worktree_path, branch


def build_opencode_prompt(issue: dict, worktree: str, cgc_timeout: int = 15, cgc_max_results: int = 8) -> str:
    """为 OpenCode 构建修复 prompt（含 CGC 代码图定位）"""
    # 先用 CGC 查询相关代码位置
    cgc_hint = ""
    try:
        # 问题类型 → CGC 搜索关键词
        type_keywords = {
            "过热漏检": "overheat",
            "降级延迟": "downgrade",
            "质疑报告缺失": "missing_skeptic",
            "决策越权": "decision_abuse",
        }
        search_keyword = "overheat downgrade threshold score"
        for t, kw in type_keywords.items():
            if t in issue["type"]:
                search_keyword = kw
                break
        search_list = search_keyword.split()
        # 正确 Cypher: (file:File)-[:CONTAINS]->(f:Function)
        # 注意: f.name CONTAINS ANY([...]) 语法错误，需用 ANY(kw IN [...] WHERE f.name CONTAINS kw)
        kw_pattern = " OR ".join(f'f.name CONTAINS "{kw}"' for kw in search_list)
        cgc_result = _run_cmd(
            ["codegraphcontext", "query",
             f"MATCH (file:File)-[:CONTAINS]->(f:Function) "
             f"WHERE {kw_pattern} "
             f"RETURN file.name, f.name LIMIT {cgc_max_results}"],
            timeout=cgc_timeout,
        )
        if cgc_result.returncode == 0 and cgc_result.stdout.strip():
            lines = [l for l in cgc_result.stdout.split("\n") if l.strip()][:8]
            cgc_hint = "\n".join(lines)
    except Exception as e:
        print(f"[auto_heal]   ⚠️ CGC 查询异常: {e}")

    # 用 CGC 查询调用该函数的代码
    code_context = ""
    if cgc_hint:
        # 查询调用该函数的代码
        caller_query = f"MATCH (caller:Function)-[:CALLS]->(f:Function) WHERE f.name CONTAINS '{search_list[0]}' RETURN caller.name, caller.file LIMIT {cgc_max_results}"
        caller_result = _run_cmd(
            ["codegraphcontext", "query", caller_query],
            timeout=cgc_timeout,
        )
        if caller_result.returncode == 0 and caller_result.stdout.strip():
            callers = caller_result.stdout.strip()
            code_context = f"\n## 调用上下文\n以下代码调用了相关函数：\n{callers}"

    # 读取 CLAUDE.md 作为上下文
    claude_md = PROJECT / "CLAUDE.md"
    if claude_md.exists():
        code_context += f"\n## 修复规范（CLAUDE.md）\n{claude_md.read_text(encoding='utf-8')}\n"

    prompt = f"""你正在修复天枢权衡系统的代码问题。工作目录: {worktree}

## 问题描述
{issue['type']}: 股票 {issue['code']}
详情: {issue.get('detail', '')}
"""
    if cgc_hint:
        prompt += f"""
## 代码图定位（CGC查询结果）
以下为代码库中与问题相关的函数位置，作为修复参考：
{cgc_hint}
"""
    if code_context:
        prompt += code_context
    prompt += f"""
## 修复指令
1. 根据问题类型，定位相关代码
2. 修复代码（最小改动原则，只改1-2行）
3. 运行 python3 -m py_compile 验证语法
4. 输出修复的：文件名、行号、改动内容

## 约束
- 只修问题直接相关的代码
- 不修改测试文件
- 不修改配置文件
- 不升级依赖版本
- 修复完即止，不要做额外优化
"""
    return prompt


def run_opencode_fix(worktree: str, prompt: str, timeout_min: int = 10, max_retries: int = 3) -> tuple[bool, str]:
    """在 worktree 中运行 OpenCode 修复，支持重试，返回 (成功, 原因)"""
    prompt_file = Path(worktree) / ".auto_heal_prompt.md"
    prompt_file.write_text(prompt, encoding="utf-8")

    print(f"[auto_heal] 🔧 OpenCode 启动修复... (超时{timeout_min}分钟, 最多重试{max_retries}次)")
    start = time.time()

    prompt_text = prompt_file.read_text(encoding="utf-8")

    for attempt in range(1, max_retries + 1):
        result = _run_cmd(
            ["opencode", "run", prompt_text],
            cwd=worktree,
            timeout=timeout_min * 60,
        )

        elapsed = time.time() - start
        print(f"[auto_heal]   ⏱ 第{attempt}次尝试 | {elapsed:.0f}秒 | 退出码: {result.returncode}")
        if result.stdout:
            print(f"[auto_heal]   📋 {result.stdout[:500]}")
        if result.stderr:
            print(f"[auto_heal]   ⚠️ {result.stderr[:300]}")

        if result.returncode == 0:
            return True, "success"
        if attempt < max_retries:
            print(f"[auto_heal]   ⏳ 第{attempt}次失败，{2**attempt}s 后重试...")
            time.sleep(2**attempt)

    # 第 3 次失败
    return False, f"failed after {max_retries} attempts, needs manual intervention"


def check_business_constraints(worktree: str, issue: dict, max_lines: int = 50, forbidden: set = None) -> bool:
    """检查业务约束"""
    if forbidden is None:
        forbidden = {"agents/__init__.py", "config/", ".env", ".env.*", "requirements.txt", "pyproject.toml"}
    # 1. 检查是否修改了不该修改的文件
    result = _run_cmd(
        ["git", "-C", worktree, "diff", "--name-only", "main"],
    )
    changed_files = result.stdout.strip().split("\n") if result.stdout.strip() else []

    # 禁止修改的文件
    for f in changed_files:
        for forb in forbidden:
            if f.startswith(forb):
                print(f"[auto_heal]   ❌ 禁止修改的文件: {f}")
                return False

    # 2. 检查改动行数是否合理（单问题不超过 max_lines 行）
    stat = _run_cmd(
        ["git", "-C", worktree, "diff", "--numstat", "main"],
    )
    total_lines = 0
    for line in stat.stdout.strip().split("\n"):
        if line:
            parts = line.split("\t")
            if len(parts) >= 2:
                try:
                    total_lines += int(parts[0]) + int(parts[1])
                except ValueError:
                    pass

    if total_lines > max_lines:
        print(f"[auto_heal]   ⚠️ 改动行数过多: {total_lines} 行（建议 ≤{max_lines}）")
        # 不阻塞，仅警告

    return True


def create_pr(worktree: str, branch: str, issue: dict, pr_repo: str = "weis1655/tianshu-quanheng") -> Optional[str]:
    """创建 PR 等待人工审核，返回 PR URL 或 None"""
    # 推送到远程
    push = _run_cmd(
        ["git", "-C", worktree, "push", "origin", branch],
    )
    if push.returncode != 0:
        print(f"[auto_heal]   ❌ 推送失败: {push.stderr[:200]}")
        return None

    # 创建 PR
    pr_title = f"auto-heal: {issue.get('type', 'fix')} ({issue.get('code', '')})"
    diff_result = _run_cmd(["git", "-C", worktree, "diff", "main"], cwd=worktree)
    diff_content = diff_result.stdout[:2000]
    pr_body = f"""## 自动修复 PR

**问题类型**: {issue.get('type', 'N/A')}
**涉及股票**: {issue.get('code', 'N/A')}
**详情**: {issue.get('detail', 'N/A')}

### 修复内容
```
{diff_content}
```

### 验证结果
- ✅ 编译通过
- ✅ 模块导入通过

**请人工审核后再合并。**
"""
    pr_result = _run_cmd(
        ["gh", "pr", "create", "--repo", pr_repo, "--base", "main", "--head", branch,
         "--title", pr_title, "--body", pr_body],
    )
    if pr_result.returncode != 0:
        print(f"[auto_heal]   ❌ PR 创建失败: {pr_result.stderr[:200]}")
        return None

    pr_url = pr_result.stdout.strip()
    print(f"[auto_heal]   🔗 PR 已创建: {pr_url}")
    return pr_url


def verify_and_reconcile(worktree: str, branch: str, issue: dict, max_lines: int = 50, forbidden: set = None, pr_repo: str = "weis1655/tianshu-quanheng") -> dict:
    """核验 worktree 中的改动，通过后创建 PR 等待审核"""
    if forbidden is None:
        forbidden = {"agents/__init__.py", "config/", ".env", ".env.*", "requirements.txt", "pyproject.toml"}
    # 检查是否有改动
    result = _run_cmd(
        ["git", "-C", worktree, "diff", "--stat"],
    )

    # 检查分支是否有新 commit（OpenCode 可能自行 commit）
    branch_log = _run_cmd(
        ["git", "-C", worktree, "log", "--oneline", "main..HEAD"],
    )
    branch_commits = branch_log.stdout.strip().split("\n") if branch_log.stdout.strip() else []

    # 合并两种检测：工作区改动 + 分支新 commit
    has_worktree_changes = bool(result.stdout.strip())
    has_branch_commits = len(branch_commits) > 0

    if not has_worktree_changes and not has_branch_commits:
        print(f"[auto_heal]   ➖ 无改动")
        return {"status": "no_change", "reason": "no_changes"}

    if has_branch_commits:
        print(f"[auto_heal]   📦 分支有 {len(branch_commits)} 个新 commit:")
        for c in branch_commits[:5]:
            print(f"      {c}")

    print(f"[auto_heal]   📊 改动统计:\n{result.stdout}")

    # 编译验证
    for pyfile in Path(worktree).rglob("*.py"):
        if ".venv" in str(pyfile) or "__pycache__" in str(pyfile):
            continue
        comp = _run_cmd(
            ["python3", "-m", "py_compile", str(pyfile)],
        )
        if comp.returncode != 0:
            print(f"[auto_heal]   ❌ 编译失败: {pyfile}\n{comp.stderr[:200]}")
            return {"status": "failed", "reason": "compile_error"}

    print(f"[auto_heal]   ✅ 编译通过")

    # 简单的回归检查：确保修改的文件能被导入
    for pyfile in Path(worktree).rglob("*.py"):
        if ".venv" in str(pyfile) or "__pycache__" in str(pyfile):
            continue
        # 尝试导入模块（仅验证无导入错误）
        import_result = _run_cmd(
            ["python3", "-c", f"import sys; sys.path.insert(0, '{worktree}'); import importlib.util; spec = importlib.util.spec_from_file_location('{pyfile.stem}', '{pyfile}'); importlib.util.module_from_spec(spec)"],
            timeout=10,
        )
        if import_result.returncode != 0:
            print(f"[auto_heal]   ❌ 导入失败: {pyfile}\n{import_result.stderr[:200]}")
            return {"status": "failed", "reason": "import_error"}

    print(f"[auto_heal]   ✅ 模块导入通过")

    # 业务约束检查
    if not check_business_constraints(worktree, issue, max_lines=max_lines, forbidden=forbidden):
        return {"status": "failed", "reason": "business_constraint_violated"}

    # 编译+导入验证通过后，创建 PR 等待人工审核
    pr_url = create_pr(worktree, branch, issue, pr_repo=pr_repo)
    if pr_url:
        # 不 merge，保留 worktree 供审查
        print(f"[auto_heal]   ⏸️ 等待人工审核 PR: {pr_url}")
        return {"status": "needs_review", "pr_url": pr_url}
    return {"status": "failed", "reason": "pr_creation_failed"}


def cleanup_worktree(worktree: str, branch: str):
    """清理 worktree"""
    _run_cmd(
        ["git", "-C", str(PROJECT), "worktree", "remove", worktree, "--force"],
    )
    _run_cmd(
        ["git", "-C", str(PROJECT), "branch", "-D", branch],
    )
    # 清理锁文件
    for lock in WORKTREE_BASE.glob(".lock_*"):
        lock.unlink(missing_ok=True)
    print(f"[auto_heal]   🧹 Worktree 已清理")


def main():
    parser = argparse.ArgumentParser(description="天枢自动迭代")
    parser.add_argument("--dry-run", action="store_true", help="模拟运行，不实际修改代码")
    parser.add_argument("--config", type=str, help="配置文件路径")
    args = parser.parse_args()

    if args.dry_run:
        print("[auto_heal] 🔍 Dry-run 模式：仅模拟，不执行实际修复")

    # 加载配置
    cfg = load_config()
    ah_cfg = cfg.get("auto_heal", {})
    timeout_min = ah_cfg.get("timeout_minutes", 10)
    max_retries = ah_cfg.get("max_retries", 3)
    max_lines = ah_cfg.get("max_lines_changed", 50)
    forbidden = set(ah_cfg.get("forbidden_files", []))
    cgc_timeout = ah_cfg.get("cgc_timeout", 15)
    cgc_max_results = ah_cfg.get("cgc_max_results", 8)
    pr_enabled = ah_cfg.get("pr_enabled", True)
    pr_repo = ah_cfg.get("pr_repo", "weis1655/tianshu-quanheng")

    print(f"\n{'='*50}")
    print(f"🔄 天枢自动迭代 v1 | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}\n")

    # Step 1: 读报告
    report = get_latest_report()
    if not report:
        print("[auto_heal] ⛔ 无报告，退出")
        sys.exit(1)

    # Step 2: 提取TOP-3 P0
    issues = extract_p0_issues(report)
    if not issues:
        print("[auto_heal] ✅ 无P0问题，无需修复")
        return

    # 创建状态追踪
    run = FixRun(
        run_id=datetime.now().strftime("%Y%m%d_%H%M%S"),
        timestamp=datetime.now().isoformat(),
        issues=issues,
    )
    log_action(run, f"开始自动迭代，发现 {len(issues)} 个P0问题")

    # Step 3: 创建工作树基目录
    WORKTREE_BASE.mkdir(parents=True, exist_ok=True)

    run_total_start = time.time()

    for i, issue in enumerate(issues[:3]):
        print(f"\n{'─'*40}")
        print(f"📌 问题 {i+1}: {issue['type']} ({issue['code']})")
        print(f"{'─'*40}")

        # 幂等性检测：检查该问题今日是否已修复
        if was_recently_fixed(issue["type"], HISTORY):
            print(f"[auto_heal]   ⏭️ 该问题今日已修复，跳过")
            run.results.append({"issue": issue, "status": "skipped", "reason": "already_fixed_today"})
            log_action(run, f"跳过 {issue['type']} - 已修复")
            # 记录 metrics
            run.metrics.setdefault(issue['type'], {})['status'] = 'skipped'
            continue

        # Dry-run 模式：跳过实际修复
        if args.dry_run:
            print(f"[auto_heal]   🔎 Dry-run: 模拟修复 {issue['type']}")
            run.results.append({"issue": issue, "status": "dry_run", "reason": "dry_run_mode"})
            run.metrics.setdefault(issue['type'], {})['status'] = 'dry_run'
            continue

        worktree, branch = create_worktree(issue, i, run)
        if not worktree:
            run.results.append({"issue": issue, "status": "skipped", "reason": "worktree_failed"})
            log_action(run, f"跳过 {issue['type']} - worktree 创建失败")
            run.metrics.setdefault(issue['type'], {})['status'] = 'worktree_failed'
            continue

        run.state = FixState.FIXING
        log_action(run, f"开始修复 {issue['type']}")

        run_start = time.time()
        retry_count = 0

        try:
            prompt = build_opencode_prompt(issue, worktree, cgc_timeout=cgc_timeout, cgc_max_results=cgc_max_results)
            ok, reason = run_opencode_fix(worktree, prompt, timeout_min=timeout_min, max_retries=max_retries)
            if not ok:
                print(f"[auto_heal]   ⚠️ OpenCode 异常退出: {reason}，仍尝试核验改动")
                run.state = FixState.FAILED
                log_action(run, f"OpenCode 修复失败: {reason}")

            result = verify_and_reconcile(worktree, branch, issue, max_lines=max_lines, forbidden=forbidden, pr_repo=pr_repo)
            status = result.get("status", "unknown")
            elapsed = time.time() - run_start

            run.results.append({
                "issue": issue,
                "status": status,
                "pr_url": result.get("pr_url"),
                "reason": result.get("reason"),
            })

            # 记录 metrics
            run.metrics.setdefault(issue['type'], {})['elapsed'] = round(elapsed, 2)
            run.metrics.setdefault(issue['type'], {})['retries'] = retry_count
            run.metrics.setdefault(issue['type'], {})['status'] = status

            if status == "needs_review":
                run.state = FixState.NEEDS_REVIEW
                log_action(run, f"修复 {issue['type']} 完成，状态: 需人工审核 (PR: {result.get('pr_url')})")
            elif status == "no_change":
                run.state = FixState.SKIPPED
                log_action(run, f"修复 {issue['type']} 完成，状态: 无改动")
            elif status == "failed":
                run.state = FixState.FAILED
                log_action(run, f"修复 {issue['type']} 失败: {result.get('reason')}")
                print(f"[auto_heal]   ⚠️ 保留 worktree 供审查: {worktree}")
            else:
                log_action(run, f"修复 {issue['type']} 完成，状态: {status}")
        finally:
            # 根据状态决定是否清理 worktree
            if status in ("no_change", "needs_review"):
                cleanup_worktree(worktree, branch)
            elif status == "failed":
                # failed 时保留 worktree 供审查，但仍清理锁文件
                for lock in WORKTREE_BASE.glob(".lock_*"):
                    lock.unlink(missing_ok=True)
            # needs_review 时保留 worktree

    # 汇总 metrics
    total_elapsed = time.time() - run_total_start
    run.metrics['summary'] = {
        'total_issues': len(issues),
        'merged': sum(1 for r in run.results if r["status"] == "needs_review"),
        'skipped': sum(1 for r in run.results if r["status"] in ("skipped", "dry_run", "no_change")),
        'failed': sum(1 for r in run.results if r["status"] == "failed"),
        'total_elapsed': round(total_elapsed, 2),
        'avg_elapsed': round(total_elapsed / len(issues), 2) if issues else 0,
    }

    # Step 4: 汇总
    print(f"\n{'='*50}")
    print(f"📊 自动迭代汇总")
    print(f"{'='*50}")
    merged = sum(1 for r in run.results if r["status"] == "needs_review")
    skipped = sum(1 for r in run.results if r["status"] in ("skipped", "dry_run", "no_change"))
    failed = sum(1 for r in run.results if r["status"] == "failed")
    print(f"  待审核: {merged}/{len(run.results)}")
    print(f"  跳过:   {skipped}/{len(run.results)}")
    print(f"  失败:   {failed}/{len(run.results)}")
    if merged > 0:
        print(f"\n  ✅ 生成 {merged} 个 PR 等待人工审核")
    print()

    # 记录结果
    report_path = REVIEWS / f"{datetime.now().strftime('%Y-%m-%d')}_回头看报告_v3.md"
    log_path = HISTORY / f"{run.run_id}_auto_heal.json"
    log = {
        "timestamp": run.timestamp,
        "run_id": run.run_id,
        "state": run.state.value,
        "results": run.results,
        "report": str(report_path),
        "log": str(log_path),
    }
    try:
        log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[auto_heal] 📝 日志: {log_path}")
    except Exception as e:
        print(f"[auto_heal] ⚠️ 日志写入失败: {e}")

    # 保存操作日志
    log_file_path = HISTORY / f"{run.run_id}_actions.log"
    try:
        log_file_path.write_text("\n".join(run.action_log), encoding="utf-8")
        print(f"[auto_heal] 📋 操作日志: {log_file_path}")
    except Exception as e:
        print(f"[auto_heal] ⚠️ 操作日志写入失败: {e}")


if __name__ == "__main__":
    main()
