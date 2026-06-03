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
from datetime import datetime
from pathlib import Path

PROJECT = Path("/home/seven/hermes-data/tianshu-quanheng")
SCRIPTS = PROJECT / "scripts"
HISTORY = PROJECT / "data" / "历史记录"
REVIEWS = PROJECT / "data" / "回顾报告"
WORKTREE_BASE = Path("/tmp/auto_heal_worktrees")


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


def create_worktree(issue: dict, idx: int) -> tuple[str, str]:
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

    # 清理旧 worktree
    subprocess.run(
        ["git", "-C", str(PROJECT), "worktree", "remove", worktree_path, "--force"],
        capture_output=True,
    )
    subprocess.run(["rm", "-rf", worktree_path], capture_output=True)

    result = subprocess.run(
        ["git", "-C", str(PROJECT), "worktree", "add", "-b", branch, worktree_path, "main"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"[auto_heal] ⚠️ Worktree 创建失败: {result.stderr.strip()}")
        return "", ""
    print(f"[auto_heal] 🌿 Worktree: {worktree_path} @ {branch}")
    return worktree_path, branch


def build_opencode_prompt(issue: dict, worktree: str) -> str:
    """为 OpenCode 构建修复 prompt（含 CGC 代码图定位）"""
    # 先用 CGC 查询相关代码位置
    cgc_hint = ""
    try:
        cgc_keywords = issue["type"].replace("P0-", "").replace("漏检", " overheat downgrade").replace("延迟", "downgrade").replace("缺失", "missing")
        cgc_result = subprocess.run(
            ["codegraphcontext", "query",
             f"MATCH (f:Function) WHERE f.name CONTAINS "
             f"ANY(['overheat','downgrade','threshold','score','降级','过热','止损']) "
             f"RETURN f.name, f.file, f.start_line LIMIT 8"],
            capture_output=True, text=True, timeout=15,
        )
        if cgc_result.returncode == 0 and cgc_result.stdout.strip():
            lines = [l for l in cgc_result.stdout.split("\n") if l.strip()][:8]
            cgc_hint = "\n".join(lines)
    except Exception:
        pass

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


def run_opencode_fix(worktree: str, prompt: str, timeout_min: int = 10) -> bool:
    """在 worktree 中运行 OpenCode 修复"""
    prompt_file = Path(worktree) / ".auto_heal_prompt.md"
    prompt_file.write_text(prompt, encoding="utf-8")

    print(f"[auto_heal] 🔧 OpenCode 启动修复... (超时{timeout_min}分钟)")
    start = time.time()

    result = subprocess.run(
        ["opencode", "run", f"$(cat {prompt_file})"],
        cwd=worktree,
        capture_output=True, text=True,
        timeout=timeout_min * 60,
    )

    elapsed = time.time() - start
    print(f"[auto_heal]   ⏱ {elapsed:.0f}秒 | 退出码: {result.returncode}")
    if result.stdout:
        print(f"[auto_heal]   📋 {result.stdout[:500]}")
    if result.stderr:
        print(f"[auto_heal]   ⚠️ {result.stderr[:300]}")

    return result.returncode == 0


def verify_and_reconcile(worktree: str, branch: str) -> bool:
    """核验 worktree 中的改动，通过后合并回主分支"""
    # 检查是否有改动
    result = subprocess.run(
        ["git", "-C", worktree, "diff", "--stat"],
        capture_output=True, text=True,
    )
    if not result.stdout.strip():
        print(f"[auto_heal]   ➖ 无改动")
        return False

    print(f"[auto_heal]   📊 改动统计:\n{result.stdout}")

    # 编译验证
    for pyfile in Path(worktree).rglob("*.py"):
        if ".venv" in str(pyfile) or "__pycache__" in str(pyfile):
            continue
        comp = subprocess.run(
            ["python3", "-m", "py_compile", str(pyfile)],
            capture_output=True, text=True,
        )
        if comp.returncode != 0:
            print(f"[auto_heal]   ❌ 编译失败: {pyfile}\n{comp.stderr[:200]}")
            return False

    print(f"[auto_heal]   ✅ 编译通过")

    # 合并到 main
    merge = subprocess.run(
        ["git", "-C", str(PROJECT), "merge", branch, "--no-edit"],
        capture_output=True, text=True,
    )
    if merge.returncode != 0:
        print(f"[auto_heal]   ❌ 合并失败: {merge.stderr[:200]}")
        return False

    print(f"[auto_heal]   🔀 已合并到 main")
    return True


def cleanup_worktree(worktree: str, branch: str):
    """清理 worktree"""
    subprocess.run(
        ["git", "-C", str(PROJECT), "worktree", "remove", worktree, "--force"],
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(PROJECT), "branch", "-D", branch],
        capture_output=True,
    )
    print(f"[auto_heal]   🧹 Worktree 已清理")


def main():
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

    # Step 3: 创建工作树基目录
    WORKTREE_BASE.mkdir(parents=True, exist_ok=True)

    results = []
    for i, issue in enumerate(issues[:3]):
        print(f"\n{'─'*40}")
        print(f"📌 问题 {i+1}: {issue['type']} ({issue['code']})")
        print(f"{'─'*40}")

        worktree, branch = create_worktree(issue, i)
        if not worktree:
            results.append({"issue": issue, "status": "skipped", "reason": "worktree_failed"})
            continue

        prompt = build_opencode_prompt(issue, worktree)
        ok = run_opencode_fix(worktree, prompt)
        if not ok:
            print(f"[auto_heal]   ⚠️ OpenCode 异常退出，仍尝试核验改动")

        reconciled = verify_and_reconcile(worktree, branch)
        cleanup_worktree(worktree, branch)

        results.append({
            "issue": issue,
            "status": "merged" if reconciled else "no_change",
        })

    # Step 4: 汇总
    print(f"\n{'='*50}")
    print(f"📊 自动迭代汇总")
    print(f"{'='*50}")
    merged = sum(1 for r in results if r["status"] == "merged")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    print(f"  合并: {merged}/{len(results)}")
    print(f"  跳过: {skipped}/{len(results)}")
    if merged > 0:
        print(f"\n  ✅ 自动修复 {merged} 个问题，已合并到 main")
    print()

    # 记录结果
    log = {
        "timestamp": datetime.now().isoformat(),
        "results": results,
        "report": str(HISTORY / f"{datetime.now().strftime('%Y-%m-%d')}_auto_heal.json"),
    }
    log_path = HISTORY / f"{datetime.now().strftime('%Y-%m-%d')}_auto_heal.json"
    try:
        log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[auto_heal] 📝 日志: {log_path}")
    except Exception as e:
        print(f"[auto_heal] ⚠️ 日志写入失败: {e}")


if __name__ == "__main__":
    main()
