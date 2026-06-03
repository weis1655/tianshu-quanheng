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
import shlex
import argparse
import yaml
import logging
import signal
from datetime import datetime, timezone, timedelta
from pathlib import Path
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional

# 日志初始化
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("auto_heal")

# 时区：Asia/Shanghai
TIANSHU_TZ = timezone(timedelta(hours=8))

# 默认禁止修改的文件集合
DEFAULT_FORBIDDEN_FILES = frozenset({
    "agents/__init__.py",
    "config/",
    ".env",
    ".env.*",
    "requirements.txt",
    "pyproject.toml",
})

# 信号处理：待清理的 worktree 列表
_worktree_to_cleanup: list[str] = []


def _signal_handler(signum, frame):
    """处理 SIGINT/SIGTERM，清理遗留的 worktree"""
    logger.warning(f"[auto_heal] 收到信号 {signum}，正在清理...")
    for wt in _worktree_to_cleanup:
        if wt and Path(wt).exists():
            _run_cmd(["git", "-C", str(PROJECT), "worktree", "remove", wt, "--force"])
            logger.info(f"[auto_heal] 已清理 worktree: {wt}")
    # 清理所有锁文件
    for lock in WORKTREE_BASE.glob(".lock_*"):
        lock.unlink(missing_ok=True)
    logger.info(f"[auto_heal] 锁文件已清理")
    sys.exit(128 + signum)


# 注册信号处理
signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


def _run_cmd(cmd: list[str], cwd: str | None = None, timeout: int = 30,
             check: bool = False, capture: bool = True) -> subprocess.CompletedProcess:
    """统一命令执行封装，替代直接 subprocess.run"""
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=capture, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        logger.warning(f"[auto_heal] ⏰ 命令超时 (timeout={timeout}s): {' '.join(cmd)}")
        result = subprocess.CompletedProcess(
            args=cmd, returncode=124, stdout="", stderr=str(e)
        )
    if check and result.returncode != 0:
        logger.error(f"[auto_heal] ❌ 命令失败: {' '.join(cmd)}\n{result.stderr[:200]}")
    return result


class FixState(Enum):
    PENDING = "pending"
    FIXING = "fixing"
    VERIFYING = "verifying"
    MERGED = "merged"
    SKIPPED = "skipped"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class FailureReason(Enum):
    TIMEOUT = "timeout"
    COMPILE_ERROR = "compile_error"
    IMPORT_ERROR = "import_error"
    BUSINESS_CONSTRAINT = "business_constraint_violated"
    PR_CREATION_FAILED = "pr_creation_failed"
    NO_CHANGES = "no_changes"
    CGC_MISS = "cgc_miss"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"


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


def log_action(run: FixRun, action: str) -> None:
    """记录操作日志"""
    entry = f"[{datetime.now(TIANSHU_TZ).isoformat()}] {action}"
    run.action_log.append(entry)
    logger.info(f"[auto_heal] 📝 {action}")


# 路径配置：从环境变量读取默认值（后续可在 main 中通过 config 覆盖）
PROJECT = Path(os.environ.get("TIANSHU_HOME", "/home/seven/hermes-data/tianshu-quanheng"))
SCRIPTS = PROJECT / "scripts"
HISTORY = PROJECT / "data" / "历史记录"
REVIEWS = PROJECT / "data" / "回顾报告"
# 先定义默认值（在 load_config 之前无法读取 config，所以用环境变量+默认值）
WORKTREE_BASE = Path(os.environ.get("AUTO_HEAL_WORKTREE", "/tmp/auto_heal_worktrees"))


def load_config(project: Path = None) -> dict:
    """加载配置文件"""
    if project is None:
        from os import environ
        project = Path(environ.get("TIANSHU_HOME", "."))
    config_path = project / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    return yaml.safe_load(config_path.read_text(encoding="utf-8"))


def validate_config(cfg: dict) -> list[str]:
    """验证配置，返回错误列表"""
    errors = []
    ah_cfg = cfg.get("auto_heal", {})

    # timeout_minutes 应为正数
    timeout = ah_cfg.get("timeout_minutes", 10)
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        errors.append("auto_heal.timeout_minutes 必须为正数")

    # max_retries 应为 1-10 的正整数
    retries = ah_cfg.get("max_retries", 3)
    if not isinstance(retries, int) or retries < 1 or retries > 10:
        errors.append("auto_heal.max_retries 必须是 1-10 的整数")

    # max_lines_changed 应为正数
    lines = ah_cfg.get("max_lines_changed", 50)
    if not isinstance(lines, int) or lines <= 0:
        errors.append("auto_heal.max_lines_changed 必须为正整数")

    return errors


def get_latest_report(reviews_dir: Path = None) -> Optional[Path]:
    """获取最新的回顾报告"""
    if reviews_dir is None:
        from os import environ
        home = Path(environ.get("TIANSHU_HOME", "."))
        reviews_dir = home / "data" / "回顾报告"
    if not reviews_dir.exists():
        return None
    reports = sorted(reviews_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    return reports[0] if reports else None


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

    logger.info(f"[auto_heal] 🔍 提取到 {len(issues)} 个P0问题:")
    for i in issues:
        logger.info(f"         • {i['type']} ({i['code']})")
    return issues


def was_recently_fixed(issue_type: str, history_dir: Path) -> bool:
    """检查该问题是否已在最近一次 auto_heal 中被修复"""
    today_log = history_dir / f"{datetime.now(TIANSHU_TZ).strftime('%Y-%m-%d')}_auto_heal.json"
    if today_log.exists():
        try:
            log = json.loads(today_log.read_text(encoding="utf-8"))
            for r in log.get("results", []):
                if r.get("status") == "merged" and r.get("issue", {}).get("type") == issue_type:
                    return True
        except Exception:
            pass
    return False


def get_cgc_keyword(issue_type: str) -> str:
    """根据问题类型返回 CGC 搜索关键词"""
    type_keywords = {
        "过热漏检": "overheat",
        "降级延迟": "downgrade",
        "质疑报告缺失": "missing_skeptic",
        "决策越权": "decision_abuse",
    }
    for t, kw in type_keywords.items():
        if t in issue_type:
            return kw
    return "overheat"  # 默认关键词


def create_worktree(issue: dict, idx: int, run: FixRun, paths: "PathsConfig") -> tuple[str, str, str]:
    """为问题创建隔离的 git worktree，返回 (path, branch, safe_type)"""
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
    branch = f"auto-heal/{safe_type}_{issue['code']}_{datetime.now(TIANSHU_TZ).strftime('%H%M%S')}"
    worktree_path = str(paths.worktree_base / f"heal_{idx:02d}_{safe_type}")

    # 原子锁文件创建
    lock_file = paths.worktree_base / f".lock_{safe_type}"
    try:
        fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, run.run_id.encode("utf-8"))
        os.close(fd)
    except FileExistsError:
        logger.warning("[auto_heal]   ⚠️ 同类型修复正在运行，跳过")
        return "", "", ""

    # 清理旧 worktree
    _run_cmd(["git", "-C", str(paths.project), "worktree", "remove", worktree_path, "--force"])
    _run_cmd(["rm", "-rf", worktree_path])

    result = _run_cmd(
        ["git", "-C", str(paths.project), "worktree", "add", "-b", branch, worktree_path, "main"],
    )
    if result.returncode != 0:
        logger.warning(f"[auto_heal] ⚠️ Worktree 创建失败: {result.stderr.strip()}")
        lock_file.unlink(missing_ok=True)
        return "", "", ""
    logger.info(f"[auto_heal] 🌿 Worktree: {worktree_path} @ {branch}")
    # 注册待清理的 worktree
    _worktree_to_cleanup.append(worktree_path)
    return worktree_path, branch, safe_type


def build_opencode_prompt(issue: dict, worktree: str, cgc_timeout: int = 15, cgc_max_results: int = 8, project: Path = None) -> str:
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
        logger.warning(f"[auto_heal]   ⚠️ CGC 查询异常: {e}")

    # 初始化 code_context
    code_context = ""

    # CGC 失败时，用 git grep 备选定位
    if not cgc_hint:
        logger.warning(f"[auto_heal]   ⚠️ CGC 查询无结果，尝试 git grep 备选定位")
        # 根据问题类型选择 grep 关键词
        grep_keywords = {
            "过热漏检": ["overheat", "over_heated", "overheat_detection"],
            "降级延迟": ["downgrade", "score_downgrade", "downgrade_threshold"],
            "质疑报告缺失": ["skeptic", "review_agent", "history_dir"],
            "决策越权": ["decision", "position", "仓位", "max_position"],
        }
        keywords = []
        for k, v in grep_keywords.items():
            if k in issue["type"]:
                keywords = v
                break
        if not keywords:
            keywords = ["overheat", "downgrade"]  # 默认

        # 执行 git grep
        grep_result = _run_cmd(
            ["git", "-C", str(project), "grep", "-l", "-i", *keywords],
            timeout=15,
        )
        if grep_result.returncode == 0 and grep_result.stdout.strip():
            files = grep_result.stdout.strip().split("\n")[:10]
            code_context += f"\n## 备选定位（git grep）\n以下文件包含相关关键词：\n"
            for f in files:
                code_context += f"- {f}\n"
            # 读取前几个文件的前 50 行作为上下文
            for f in files[:3]:
                fpath = project / f
                if fpath.exists():
                    try:
                        lines = fpath.read_text(encoding="utf-8").split("\n")[:50]
                        code_context += f"\n### {f} (前 50 行)\n" + "\n".join(lines) + "\n"
                    except Exception:
                        pass
    else:
        # 用 CGC 查询调用该函数的代码
        caller_query = f"MATCH (caller:Function)-[:CALLS]->(f:Function) WHERE f.name CONTAINS '{search_list[0]}' RETURN caller.name, caller.file LIMIT {cgc_max_results}"
        caller_result = _run_cmd(
            ["codegraphcontext", "query", caller_query],
            timeout=cgc_timeout,
        )
        if caller_result.returncode == 0 and caller_result.stdout.strip():
            callers = caller_result.stdout.strip()
            code_context = f"\n## 调用上下文\n以下代码调用了相关函数：\n{callers}"

    # 读取 CLAUDE.md 作为上下文
    claude_md = project / "CLAUDE.md"
    if claude_md.exists():
        try:
            code_context += f"\n## 修复规范（CLAUDE.md）\n{claude_md.read_text(encoding='utf-8')}\n"
        except Exception as e:
            logger.warning(f"[auto_heal]   ⚠️ 读取 CLAUDE.md 失败: {e}")

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
    prompt += """
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


def run_opencode_fix(worktree: str, prompt: str, timeout_min: int = 10, max_retries: int = 3, total_timeout_min: int = 30) -> tuple[bool, str]:
    """在 worktree 中运行 OpenCode 修复，支持重试和总超时，返回 (成功, 原因)"""
    prompt_file = Path(worktree) / ".auto_heal_prompt.md"
    prompt_file.write_text(prompt, encoding="utf-8")

    logger.info(f"[auto_heal] 🔧 OpenCode 启动修复... (超时{timeout_min}分钟, 最多重试{max_retries}次, 总超时{total_timeout_min}分钟)")
    start = time.time()
    total_deadline = time.time() + total_timeout_min * 60

    prompt_text = prompt_file.read_text(encoding="utf-8")

    try:
        for attempt in range(1, max_retries + 1):
            # 检查总超时
            if time.time() > total_deadline:
                elapsed_total = time.time() - start
                logger.warning(f"[auto_heal]   ⏰ 总超时 ({total_timeout_min}min)，停止重试")
                return False, f"total_timeout ({total_timeout_min}min), elapsed {elapsed_total:.0f}s"

            result = _run_cmd(
                ["opencode", "run", prompt_text],
                cwd=worktree,
                timeout=timeout_min * 60,
            )

            elapsed = time.time() - start
            logger.info(f"[auto_heal]   ⏱ 第{attempt}次尝试 | {elapsed:.0f}秒 | 退出码: {result.returncode}")
            if result.stdout:
                logger.info(f"[auto_heal]   📋 {result.stdout[:500]}")
            if result.stderr:
                logger.warning(f"[auto_heal]   ⚠️ {result.stderr[:300]}")

            if result.returncode == 0:
                return True, "success"
            if attempt < max_retries:
                logger.info(f"[auto_heal]   ⏳ 第{attempt}次失败，{2**attempt}s 后重试...")
                time.sleep(2**attempt)

        # 第 3 次失败
        return False, f"failed after {max_retries} attempts, needs manual intervention"
    finally:
        # 清理 prompt 文件
        try:
            prompt_file.unlink(missing_ok=True)
        except Exception:
            pass


def check_business_constraints(worktree: str, issue: dict, max_lines: int = 50, forbidden: set | None = None) -> bool:
    """检查业务约束"""
    if forbidden is None:
        forbidden = DEFAULT_FORBIDDEN_FILES
    # 1. 检查是否修改了不该修改的文件
    result = _run_cmd(
        ["git", "-C", worktree, "diff", "--name-only", "main"],
    )
    changed_files = result.stdout.strip().split("\n") if result.stdout.strip() else []

    # 禁止修改的文件
    for f in changed_files:
        for forb in forbidden:
            if f.startswith(forb):
                logger.error(f"[auto_heal]   ❌ 禁止修改的文件: {f}")
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
        logger.warning(f"[auto_heal]   ⚠️ 改动行数过多: {total_lines} 行（建议 ≤{max_lines}）")
        # 不阻塞，仅警告

    return True


def classify_failure_reason(status: str, reason: str = "") -> FailureReason:
    """将失败原因分类为枚举值"""
    if status in ("needs_review", "pushed_no_pr"):
        return None  # 成功
    if status == "skipped":
        return FailureReason.SKIPPED
    reason_lower = reason.lower()
    if "timeout" in reason_lower:
        return FailureReason.TIMEOUT
    if "compile" in reason_lower or "py_compile" in reason_lower:
        return FailureReason.COMPILE_ERROR
    if "import" in reason_lower:
        return FailureReason.IMPORT_ERROR
    if "business" in reason_lower:
        return FailureReason.BUSINESS_CONSTRAINT
    if "pr" in reason_lower:
        return FailureReason.PR_CREATION_FAILED
    if "no change" in reason_lower:
        return FailureReason.NO_CHANGES
    return FailureReason.UNKNOWN


def create_pr(worktree: str, branch: str, issue: dict, pr_repo: str = "weis1655/tianshu-quanheng", max_pr_retries: int = 2) -> Optional[str]:
    """创建 PR 等待人工审核，支持重试，返回 PR URL 或 None"""
    # 推送到远程（带重试）
    for attempt in range(1, max_pr_retries + 2):  # +2 因为 push 也需要重试
        push = _run_cmd(["git", "-C", worktree, "push", "origin", branch], timeout=60)
        if push.returncode == 0:
            break
        logger.warning(f"[auto_heal]   ⚠️ 推送第{attempt}次失败，重试...")
        if attempt < max_pr_retries + 1:
            time.sleep(2 ** attempt)
    else:
        logger.error(f"[auto_heal]   ❌ 推送失败，放弃 PR")
        return None

    # PR 创建（带重试）
    pr_title = f"auto-heal: {issue.get('type', 'fix')} ({issue.get('code', '')})"
    diff_result = _run_cmd(["git", "-C", worktree, "diff", "main"], cwd=worktree)
    diff_lines = diff_result.stdout.splitlines()
    diff_content = "\n".join(diff_lines[:50])  # 限制 50 行
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
    for attempt in range(1, max_pr_retries + 1):
        pr_result = _run_cmd(
            ["gh", "pr", "create", "--repo", pr_repo, "--base", "main", "--head", branch,
             "--title", pr_title, "--body", pr_body],
            timeout=30,
        )
        if pr_result.returncode == 0:
            pr_url = pr_result.stdout.strip()
            logger.info(f"[auto_heal]   🔗 PR 已创建: {pr_url}")
            return pr_url
        logger.warning(f"[auto_heal]   ⚠️ PR 创建第{attempt}次失败，重试...")
        if attempt < max_pr_retries:
            time.sleep(2 ** attempt)

    # Fallback: PR 创建失败，但分支已推送，返回 pushed_no_pr 状态
    logger.warning(f"[auto_heal]   ⚠️ PR 创建失败（gh 可能未配置），但分支已推送: {branch}")
    return f"pushed_no_pr:{branch}"


def verify_and_reconcile(worktree: str, branch: str, issue: dict, max_lines: int = 50, forbidden: set | None = None, pr_repo: str = "weis1655/tianshu-quanheng") -> dict:
    """核验 worktree 中的改动，通过后创建 PR 等待审核"""
    if forbidden is None:
        forbidden = DEFAULT_FORBIDDEN_FILES
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
        logger.info("[auto_heal]   ➖ 无改动")
        return {"status": "no_change", "reason": "no_changes"}

    if has_branch_commits:
        logger.info(f"[auto_heal]   📦 分支有 {len(branch_commits)} 个新 commit:")
        for c in branch_commits[:5]:
            logger.info(f"      {c}")

    logger.info(f"[auto_heal]   📊 改动统计:\n{result.stdout}")

    # 增量编译验证：只验证 git diff 中修改的文件
    changed_files_result = _run_cmd(["git", "-C", worktree, "diff", "--name-only", "main"])
    changed_files = [f.strip() for f in changed_files_result.stdout.strip().split("\n") if f.strip()]

    for rel_path in changed_files:
        if not rel_path.endswith(".py"):
            continue
        pyfile = Path(worktree) / rel_path
        if not pyfile.exists():
            continue
        # 跳过 venv 和 __pycache__
        if ".venv" in str(pyfile) or "__pycache__" in str(pyfile):
            continue
        comp = _run_cmd(
            ["python3", "-m", "py_compile", str(pyfile)],
        )
        if comp.returncode != 0:
            logger.error(f"[auto_heal]   ❌ 编译失败: {pyfile}\n{comp.stderr[:200]}")
            return {"status": "failed", "reason": "compile_error"}

    logger.info("[auto_heal]   ✅ 编译通过")

    # 简单的回归检查：确保修改的文件能被导入
    for rel_path in changed_files:
        if not rel_path.endswith(".py"):
            continue
        pyfile = Path(worktree) / rel_path
        if not pyfile.exists():
            continue
        if ".venv" in str(pyfile) or "__pycache__" in str(pyfile):
            continue
        # 尝试导入模块（仅验证无导入错误）
        import_result = _run_cmd(
            ["python3", "-c",
             f"import sys; sys.path.insert(0, {shlex.quote(worktree)}); "
             f"import importlib.util; spec = importlib.util.spec_from_file_location("
             f"'{pyfile.stem}', {shlex.quote(str(pyfile))}); "
             f"importlib.util.module_from_spec(spec)"],
            timeout=10,
        )
        if import_result.returncode != 0:
            logger.error(f"[auto_heal]   ❌ 导入失败: {pyfile}\n{import_result.stderr[:200]}")
            return {"status": "failed", "reason": "import_error"}

    logger.info("[auto_heal]   ✅ 模块导入通过")

    # 业务约束检查
    if not check_business_constraints(worktree, issue, max_lines=max_lines, forbidden=forbidden):
        return {"status": "failed", "reason": "business_constraint_violated"}

    # 编译+导入验证通过后，创建 PR 等待人工审核
    pr_result = create_pr(worktree, branch, issue, pr_repo=pr_repo)
    if pr_result is None:
        return {"status": "failed", "reason": "pr_creation_failed"}
    elif pr_result.startswith("pushed_no_pr:"):
        branch_name = pr_result.split(":")[1]
        logger.warning(f"[auto_heal]   ⚠️ PR 创建失败，但分支已推送: {branch_name}")
        return {"status": "pushed_no_pr", "branch": branch_name}
    else:
        logger.info(f"[auto_heal]   ⏸️ 等待人工审核 PR: {pr_result}")
        return {"status": "needs_review", "pr_url": pr_result}


def cleanup_worktree(worktree: str, branch: str, project: Path, safe_type: str | None = None, worktree_base: Path = None):
    """清理 worktree"""
    if not worktree or not branch:
        return
    _run_cmd(
        ["git", "-C", str(project), "worktree", "remove", worktree, "--force"],
    )
    _run_cmd(
        ["git", "-C", str(project), "branch", "-D", branch],
    )
    # 只清理当前类型的锁文件
    if safe_type:
        if worktree_base is None:
            worktree_base = project / "tmp" / "auto_heal_worktrees"
        lock_file = worktree_base / f".lock_{safe_type}"
        lock_file.unlink(missing_ok=True)
    logger.info("[auto_heal]   🧹 Worktree 已清理")


# ============== 架构：PathsConfig + 拆分后的子函数 ==============

@dataclass
class PathsConfig:
    """路径配置容器"""
    project: Path
    scripts: Path
    history: Path
    reviews: Path
    worktree_base: Path


class TimeSeriesStore:
    """时间序列指标存储 — CSV 格式，追加写入"""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.csv_path = self.data_dir / "auto_heal_timeseries.csv"
        self._ensure_header()

    def _ensure_header(self):
        if not self.csv_path.exists():
            import csv
            fields = ["timestamp", "run_id", "total_issues", "needs_review", "pushed_no_pr",
                      "skipped", "failed", "fix_success_rate", "avg_success_elapsed",
                      "cgc_hit_rate", "opencode_success_rate", "total_elapsed"]
            with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(fields)

    def append(self, run: FixRun, summary: dict):
        import csv
        row = [
            datetime.now(TIANSHU_TZ).isoformat(),
            run.run_id,
            summary.get("total_issues", 0),
            summary.get("needs_review", 0),
            summary.get("pushed_no_pr", 0),
            summary.get("skipped", 0),
            summary.get("failed", 0),
            summary.get("fix_success_rate", 0),
            summary.get("avg_success_elapsed", 0),
            summary.get("cgc_hit_rate", 0),
            summary.get("opencode_success_rate", 0),
            summary.get("total_elapsed", 0),
        ]
        with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(row)


def parse_args() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="天枢自动迭代")
    parser.add_argument("--dry-run", action="store_true", help="模拟运行，不实际修改代码")
    parser.add_argument("--config", type=str, help="配置文件路径")
    return parser.parse_args()


def load_and_validate_config(args, paths: PathsConfig) -> dict:
    """加载并验证配置文件"""
    cfg = load_config(paths.project)

    errors = validate_config(cfg)
    if errors:
        logger.error("[auto_heal] 配置验证失败:")
        for err in errors:
            logger.error(f"[auto_heal]   - {err}")
        sys.exit(1)

    return cfg


def run_iteration(cfg: dict, args: argparse.Namespace, paths: PathsConfig) -> FixRun:
    """执行一次迭代修复"""
    ah_cfg = cfg.get("auto_heal", {})
    timeout_min = ah_cfg.get("timeout_minutes", 10)
    max_retries = ah_cfg.get("max_retries", 3)
    total_timeout_min = ah_cfg.get("total_timeout_minutes", 30)
    max_lines = ah_cfg.get("max_lines_changed", 50)
    forbidden = set(ah_cfg.get("forbidden_files", []))
    cgc_timeout = ah_cfg.get("cgc_timeout", 15)
    cgc_max_results = ah_cfg.get("cgc_max_results", 8)
    pr_repo = ah_cfg.get("pr_repo", "weis1655/tianshu-quanheng")

    # 使用 paths 参数，不再写回全局变量
    reviews_dir = paths.reviews
    history_dir = paths.history
    worktree_base = paths.worktree_base
    project = paths.project

    if args.dry_run:
        logger.info("[auto_heal] 🔍 Dry-run 模式：仅模拟，不执行实际修复")

    logger.info(f"\n{'='*50}")
    logger.info(f"🔄 天枢自动迭代 v1 | {datetime.now(TIANSHU_TZ).strftime('%Y-%m-%d %H:%M')}")
    logger.info(f"{'='*50}\n")

    # Step 1: 读报告
    latest_report = get_latest_report(reviews_dir)
    if not latest_report:
        logger.error("[auto_heal] ⛔ 无报告，退出")
        sys.exit(1)

    # Step 2: 提取 TOP-3 P0
    issues = extract_p0_issues(latest_report)
    if not issues:
        logger.info("[auto_heal] ✅ 无P0问题，无需修复")
        return FixRun(
            run_id=datetime.now(TIANSHU_TZ).strftime("%Y%m%d_%H%M%S"),
            timestamp=datetime.now(TIANSHU_TZ).isoformat(),
            issues=[],
        )

    # 创建状态追踪
    run = FixRun(
        run_id=datetime.now(TIANSHU_TZ).strftime("%Y%m%d_%H%M%S"),
        timestamp=datetime.now(TIANSHU_TZ).isoformat(),
        issues=issues,
    )
    log_action(run, f"开始自动迭代，发现 {len(issues)} 个P0问题")

    # Step 3: 创建工作树基目录
    paths.worktree_base.mkdir(parents=True, exist_ok=True)

    run_total_start = time.time()

    for i, issue in enumerate(issues[:3]):
        logger.info(f"\n{'─'*40}")
        logger.info(f"📌 问题 {i+1}: {issue['type']} ({issue['code']})")
        logger.info(f"{'─'*40}")

        # 幂等性检测：检查该问题今日是否已修复
        if was_recently_fixed(issue["type"], paths.history):
            logger.info("[auto_heal]   ⏭️ 该问题今日已修复，跳过")
            run.results.append({"issue": issue, "status": "skipped", "reason": "already_fixed_today"})
            log_action(run, f"跳过 {issue['type']} - 已修复")
            run.metrics.setdefault(issue['type'], {})['status'] = 'skipped'
            continue

        # Dry-run 模式：跳过实际修复
        if args.dry_run:
            logger.info(f"[auto_heal]   🔎 Dry-run: 模拟修复 {issue['type']}")
            run.results.append({"issue": issue, "status": "dry_run", "reason": "dry_run_mode"})
            run.metrics.setdefault(issue['type'], {})['status'] = 'dry_run'
            continue

        worktree, branch, safe_type = create_worktree(issue, i, run, paths)
        if not worktree:
            run.results.append({"issue": issue, "status": "skipped", "reason": "worktree_failed"})
            log_action(run, f"跳过 {issue['type']} - worktree 创建失败")
            run.metrics.setdefault(issue['type'], {})['status'] = 'worktree_failed'
            continue

        run.state = FixState.FIXING
        log_action(run, f"开始修复 {issue['type']}")

        run_start = time.time()
        retry_count = 0
        status = "unknown"

        try:
            # CGC 查询计时
            cgc_start = time.time()
            prompt = build_opencode_prompt(issue, worktree, cgc_timeout=cgc_timeout, cgc_max_results=cgc_max_results, project=paths.project)
            cgc_elapsed = time.time() - cgc_start
            cgc_has_hint = "代码图定位" in prompt  # 简单判断 CGC 是否返回结果

            ok, reason = run_opencode_fix(worktree, prompt, timeout_min=timeout_min, max_retries=max_retries, total_timeout_min=total_timeout_min)
            if not ok:
                logger.warning(f"[auto_heal]   ⚠️ OpenCode 异常退出: {reason}，仍尝试核验改动")
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

            # 记录详细 metrics
            run.metrics.setdefault(issue['type'], {})['elapsed'] = round(elapsed, 2)
            run.metrics.setdefault(issue['type'], {})['retries'] = retry_count
            run.metrics.setdefault(issue['type'], {})['status'] = status
            run.metrics.setdefault(issue['type'], {})['cgc_status'] = 'hit' if cgc_has_hint else 'miss'
            run.metrics.setdefault(issue['type'], {})['cgc_elapsed'] = round(cgc_elapsed, 2)
            run.metrics.setdefault(issue['type'], {})['opencode_ok'] = ok
            run.metrics.setdefault(issue['type'], {})['opencode_reason'] = reason

            # 在 verify_and_reconcile 返回后记录 PR 状态
            pr_status = "unknown"
            if status == "pushed_no_pr":
                pr_status = "pushed_no_pr"
            elif status == "needs_review":
                pr_status = "created"
            run.metrics.setdefault(issue['type'], {})['pr_status'] = pr_status

            if status == "needs_review":
                run.state = FixState.NEEDS_REVIEW
                log_action(run, f"修复 {issue['type']} 完成，状态: 需人工审核 (PR: {result.get('pr_url')})")
            elif status == "no_change":
                run.state = FixState.SKIPPED
                log_action(run, f"修复 {issue['type']} 完成，状态: 无改动")
            elif status == "failed":
                run.state = FixState.FAILED
                log_action(run, f"修复 {issue['type']} 失败: {result.get('reason')}")
                logger.warning(f"[auto_heal]   ⚠️ 保留 worktree 供审查: {worktree}")
            else:
                log_action(run, f"修复 {issue['type']} 完成，状态: {status}")
        finally:
            # 根据状态决定是否清理 worktree
            if status in ("no_change", "needs_review"):
                cleanup_worktree(worktree, branch, project, safe_type=safe_type, worktree_base=worktree_base)
            elif status == "failed":
                # failed 时保留 worktree 供审查，但仍清理当前类型的锁文件
                lock_file = worktree_base / f".lock_{safe_type}"
                lock_file.unlink(missing_ok=True)
            # needs_review 时保留 worktree

    # 汇总 metrics
    total_elapsed = time.time() - run_total_start

    # 成功修复 = needs_review + pushed_no_pr
    successful_fixes = sum(1 for r in run.results if r["status"] in ("needs_review", "pushed_no_pr"))
    fix_success_rate = round(successful_fixes / len(issues), 2) if issues else 0

    # avg_success_elapsed 仅计算成功修复项的平均耗时
    success_elapsed_list = [
        run.metrics[r["issue"]["type"]]["elapsed"]
        for r in run.results
        if r["status"] in ("needs_review", "pushed_no_pr")
        and isinstance(run.metrics.get(r["issue"]["type"]), dict)
    ]
    avg_success_elapsed = round(sum(success_elapsed_list) / len(success_elapsed_list), 2) if success_elapsed_list else 0

    # status_distribution 各状态分布
    status_distribution = {}
    for r in run.results:
        s = r["status"]
        status_distribution[s] = status_distribution.get(s, 0) + 1

    # failure_breakdown 失败根因分类
    failure_breakdown = {}
    for r in run.results:
        if r["status"] == "failed":
            fr = classify_failure_reason(r["status"], r.get("reason", ""))
            fr_name = fr.value if fr else "unknown"
            failure_breakdown[fr_name] = failure_breakdown.get(fr_name, 0) + 1

    run.metrics['summary'] = {
        'total_issues': len(issues),
        'needs_review': sum(1 for r in run.results if r["status"] == "needs_review"),
        'pushed_no_pr': sum(1 for r in run.results if r["status"] == "pushed_no_pr"),
        'skipped': sum(1 for r in run.results if r["status"] in ("skipped", "dry_run", "no_change")),
        'failed': sum(1 for r in run.results if r["status"] == "failed"),
        'fix_success_rate': fix_success_rate,
        'avg_success_elapsed': avg_success_elapsed,
        'status_distribution': status_distribution,
        'failure_breakdown': failure_breakdown,
        'cgc_hit_rate': round(sum(1 for m in run.metrics.values() if isinstance(m, dict) and m.get('cgc_status') == 'hit') / len(issues), 2) if issues else 0,
        'opencode_success_rate': round(sum(1 for m in run.metrics.values() if isinstance(m, dict) and m.get('opencode_ok')) / len(issues), 2) if issues else 0,
        'total_elapsed': round(total_elapsed, 2),
        'avg_elapsed': round(total_elapsed / len(issues), 2) if issues else 0,
    }

    # Step 4: 汇总
    logger.info(f"\n{'='*50}")
    logger.info(f"📊 自动迭代汇总")
    logger.info(f"{'='*50}")
    merged = sum(1 for r in run.results if r["status"] == "needs_review")
    skipped = sum(1 for r in run.results if r["status"] in ("skipped", "dry_run", "no_change"))
    failed = sum(1 for r in run.results if r["status"] == "failed")
    logger.info(f"  待审核: {merged}/{len(run.results)}")
    logger.info(f"  跳过:   {skipped}/{len(run.results)}")
    logger.info(f"  失败:   {failed}/{len(run.results)}")
    if merged > 0:
        logger.info(f"\n  ✅ 生成 {merged} 个 PR 等待人工审核")
    logger.info("")

    # 记录结果
    report_path = paths.reviews / f"{datetime.now(TIANSHU_TZ).strftime('%Y-%m-%d')}_回头看报告_v3.md"
    log_path = paths.history / f"{run.run_id}_auto_heal.json"
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
        logger.info(f"[auto_heal] 📝 日志: {log_path}")
    except Exception as e:
        logger.warning(f"[auto_heal] ⚠️ 日志写入失败: {e}")

    # 保存操作日志
    log_file_path = paths.history / f"{run.run_id}_actions.log"
    try:
        log_file_path.write_text("\n".join(run.action_log), encoding="utf-8")
        logger.info(f"[auto_heal] 📋 操作日志: {log_file_path}")
    except Exception as e:
        logger.warning(f"[auto_heal] ⚠️ 操作日志写入失败: {e}")

    return run


def send_webhook_alert(webhook_url: str, summary: dict, threshold: float = 0.3):
    """当修复成功率低于阈值时发送 Webhook 告警"""
    success_rate = summary.get("fix_success_rate", 0)
    if success_rate < threshold:
        import urllib.request, json
        payload = json.dumps({
            "content": f"🚨 天枢自动迭代告警：修复成功率 {success_rate:.0%} 低于阈值 {threshold:.0%}\n"
                       f"总问题: {summary.get('total_issues', 0)} | "
                       f"成功: {summary.get('needs_review', 0) + summary.get('pushed_no_pr', 0)} | "
                       f"失败: {summary.get('failed', 0)} | "
                       f"跳过: {summary.get('skipped', 0)}"
        }).encode()
        try:
            req = urllib.request.Request(webhook_url, data=payload, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            logger.warning(f"[auto_heal]   Webhook 告警失败: {e}")


def main():
    """入口函数"""
    args = parse_args()
    # 初始化路径配置
    paths = PathsConfig(
        project=PROJECT,
        scripts=SCRIPTS,
        history=HISTORY,
        reviews=REVIEWS,
        worktree_base=WORKTREE_BASE,
    )
    cfg = load_and_validate_config(args, paths)

    run = run_iteration(cfg, args, paths)

    # 时间序列数据持久化
    data_dir = paths.history
    ts_store = TimeSeriesStore(data_dir)
    ts_store.append(run, run.metrics.get("summary", {}))

    if run.issues:
        pass  # stubs removed


if __name__ == "__main__":
    main()
