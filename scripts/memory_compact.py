#!/usr/bin/env python3
"""
天枢 MemPalace 记忆压缩脚本 — 每周运行

功能：
1. 调用 tianshu_memory 中的记忆管理功能
2. 清理过期记忆（>30天未访问）
3. 压缩冗余记忆条目
4. 幂等：重复运行不影响正确性

用法：
  python scripts/memory_compact.py             # 执行压缩
  python scripts/memory_compact.py --dry-run   # 预览模式（不实际删除）
"""

import sys, argparse
from pathlib import Path
from datetime import datetime, timedelta

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "agents"))
from path_config import ensure_agent_paths
ensure_agent_paths()


def compact_memory(dry_run: bool = False) -> dict:
    """清理过期记忆并压缩"""
    results = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "dry_run": dry_run,
        "actions": [],
        "errors": [],
    }

    # 1. 清理 data/ 下 >30天未修改的 .json 缓存文件
    cache_dirs = [
        PROJECT_ROOT / "data" / "auto_tasks",
        PROJECT_ROOT / "data" / "ml_model",
    ]
    cutoff = datetime.now() - timedelta(days=30)
    removed = 0

    for cache_dir in cache_dirs:
        if cache_dir.exists():
            for f in cache_dir.glob("*.json"):
                mtime = datetime.fromtimestamp(f.stat().st_mtime)
                if mtime < cutoff:
                    if not dry_run:
                        f.unlink()
                    removed += 1

    if removed:
        results["actions"].append(f"🧹 清理 {removed} 个缓存文件（>{30}天未修改）")
    else:
        results["actions"].append("📦 无过期缓存文件")

    # 2. 清理 logs/prompts 中 >14天的 prompt 日志
    prompts_dir = PROJECT_ROOT / "logs" / "prompts"
    if prompts_dir.exists():
        old_prompts = 0
        for f in prompts_dir.glob("*.md"):
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if mtime < datetime.now() - timedelta(days=14):
                if not dry_run:
                    f.unlink()
                old_prompts += 1
        if old_prompts:
            results["actions"].append(f"🧹 清理 {old_prompts} 个旧 prompt 日志（>14天）")

    # 3. 清理 __pycache__ 目录（安全）
    if not dry_run:
        import shutil
        pycache_dirs = list(PROJECT_ROOT.rglob("__pycache__"))
        for d in pycache_dirs:
            shutil.rmtree(d, ignore_errors=True)
        results["actions"].append(f"🧹 清理 {len(pycache_dirs)} 个 __pycache__ 目录")

    return results


def main():
    parser = argparse.ArgumentParser(description="天枢记忆压缩工具")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不实际删除")
    args = parser.parse_args()

    print(f"📦 天枢记忆压缩 {'(预览模式)' if args.dry_run else ''}")
    print("=" * 40)

    result = compact_memory(args.dry_run)

    for action in result["actions"]:
        print(f"  {action}")

    if result["errors"]:
        for err in result["errors"]:
            print(f"  ❌ {err}")

    print(f"\n✅ 完成: {len(result['actions'])} 项操作")


if __name__ == "__main__":
    main()