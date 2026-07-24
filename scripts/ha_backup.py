#!/usr/bin/env python3
"""
天枢权衡系统高可用备份脚本 — 可被 cron 调用的独立脚本

功能：
  1. 备份五池JSON文件（五池管理/*.json）→ data/backups/pools/YYYY-MM-DD_HHMMSS/
  2. 备份决策日志（data/decision_log.json）→ data/backups/logs/
  3. 备份配置文件（config.yaml）→ data/backups/config/
  4. 备份最近N份当日报告（data/历史记录/）→ data/backups/reports/
  5. 所有备份保留7天，超期自动清理
  6. 可选 git add + commit + push（--push）
  7. --dry-run 预览模式
  8. 返回备份统计：文件数、大小、耗时
  9. 写备份日志到 data/backups/ha_backup.log

用法：
  python scripts/ha_backup.py              # 完整备份
  python scripts/ha_backup.py --pools-only  # 仅备份五池
  python scripts/ha_backup.py --push         # 备份后推送到GitHub
  python scripts/ha_backup.py --dry-run      # 预览模式

依赖：仅 Python 标准库（os, shutil, json, datetime, subprocess, glob）
"""

import argparse
import glob
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

# ── 路径常量 ──────────────────────────────────────────────────────────
# 脚本位于 scripts/ha_backup.py，项目根在其父目录的父目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)  # ~/hermes-data/tianshu-quanheng/

# 源路径
POOL_DIR = os.path.join(PROJECT_ROOT, "五池管理")
DECISION_LOG = os.path.join(PROJECT_ROOT, "data", "decision_log.json")
CONFIG_FILE = os.path.join(PROJECT_ROOT, "config.yaml")
HISTORY_DIR = os.path.join(PROJECT_ROOT, "data", "历史记录")

# 备份根目录
BACKUP_ROOT = os.path.join(PROJECT_ROOT, "data", "backups")
BACKUP_POOLS = os.path.join(BACKUP_ROOT, "pools")
BACKUP_LOGS = os.path.join(BACKUP_ROOT, "logs")
BACKUP_CONFIG = os.path.join(BACKUP_ROOT, "config")
BACKUP_REPORTS = os.path.join(BACKUP_ROOT, "reports")
BACKUP_LOG_FILE = os.path.join(BACKUP_ROOT, "ha_backup.log")

RETENTION_DAYS = 7  # 备份保留天数
MAX_REPORTS = 20    # 最多备份的当日报告数

# 北京时区偏移（UTC+8）
TZ_BEIJING = timezone(timedelta(hours=8))


# ── 日志 ──────────────────────────────────────────────────────────────
def setup_logger():
    """初始化日志记录器：同时输出到文件和控制台"""
    os.makedirs(BACKUP_ROOT, exist_ok=True)

    logger = logging.getLogger("ha_backup")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    # 文件 handler（UTF-8 追加模式）
    fh = logging.FileHandler(BACKUP_LOG_FILE, mode="a", encoding="utf-8")
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(fh)

    # 控制台 handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(ch)

    return logger


# ── 工具函数 ──────────────────────────────────────────────────────────
def get_timestamp_str():
    """返回备份时间戳目录名，如 2026-07-24_153011"""
    now = datetime.now(TZ_BEIJING)
    return now.strftime("%Y-%m-%d_%H%M%S")


def get_today_prefix():
    """返回今日日期前缀，用于匹配当日报告文件名，如 '2026-07-24' 或 '20260724'"""
    now = datetime.now(TZ_BEIJING)
    return now.strftime("%Y-%m-%d"), now.strftime("%Y%m%d")


def human_size(size_bytes):
    """将字节数转换为人类可读格式"""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}" if unit != "B" else f"{size_bytes}B"
        size_bytes /= 1024
    return f"{size_bytes:.1f}TB"


def copy_file(src, dst, dry_run, logger):
    """复制单个文件，返回 (文件名, 大小) 或 None"""
    if not os.path.isfile(src):
        logger.warning("  ⚠ 源文件不存在: %s", src)
        return None

    size = os.path.getsize(src)
    if dry_run:
        logger.info("  [DRY-RUN] 复制: %s → %s (%s)", src, dst, human_size(size))
        return os.path.basename(src), size

    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        shutil.copy2(src, dst)
        logger.info("  ✅ 复制: %s → %s (%s)", os.path.basename(src), dst, human_size(size))
        return os.path.basename(src), size
    except OSError as e:
        logger.error("  ❌ 复制失败: %s → %s: %s", src, dst, e)
        return None


def copy_files(file_list, dest_dir, dry_run, logger):
    """批量复制文件到目标目录，返回 (文件列表, 总大小)"""
    results = []
    total_size = 0
    for src in file_list:
        fname = os.path.basename(src)
        dst = os.path.join(dest_dir, fname)
        result = copy_file(src, dst, dry_run, logger)
        if result:
            results.append(result[0])
            total_size += result[1]
    return results, total_size


def clean_old_backups(backup_dir, dry_run, logger):
    """清理超过 RETENTION_DAYS 天的旧备份目录"""
    if not os.path.isdir(backup_dir):
        return 0

    cutoff = datetime.now(TZ_BEIJING) - timedelta(days=RETENTION_DAYS)
    removed = 0

    for entry in os.listdir(backup_dir):
        entry_path = os.path.join(backup_dir, entry)
        if not os.path.isdir(entry_path):
            continue
        # 尝试从目录名解析日期：YYYY-MM-DD_HHMMSS
        try:
            dir_date_str = entry[:10]  # "YYYY-MM-DD"
            dir_date = datetime.strptime(dir_date_str, "%Y-%m-%d").replace(tzinfo=TZ_BEIJING)
            if dir_date < cutoff:
                if dry_run:
                    logger.info("  [DRY-RUN] 清理过期: %s", entry_path)
                else:
                    shutil.rmtree(entry_path)
                    logger.info("  🧹 清理过期备份: %s", entry_path)
                removed += 1
        except (ValueError, IndexError):
            continue

    return removed


def clean_old_files(backup_dir, pattern, dry_run, logger):
    """
    清理目录中超过 RETENTION_DAYS 天的旧备份文件（非目录）

    从文件名中解析日期前缀（如 decision_log.2026-07-24_162849.json 中的
    "2026-07-24"），避免被 shutil.copy2 保留的源文件 mtime 误导。
    """
    if not os.path.isdir(backup_dir):
        return 0

    cutoff = datetime.now(TZ_BEIJING) - timedelta(days=RETENTION_DAYS)
    removed = 0

    for fpath in glob.glob(os.path.join(backup_dir, pattern)):
        if not os.path.isfile(fpath):
            continue
        fname = os.path.basename(fpath)
        # 解析文件名中的日期 "YYYY-MM-DD"（第一个出现的）
        date_str = None
        for part in fname.replace(".", "_").split("_"):
            if len(part) == 10 and part[4] == "-" and part[7] == "-":
                try:
                    datetime.strptime(part, "%Y-%m-%d")
                    date_str = part
                    break
                except ValueError:
                    continue
        if date_str:
            file_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=TZ_BEIJING)
            if file_date < cutoff:
                if dry_run:
                    logger.info("  [DRY-RUN] 清理过期文件: %s", fpath)
                else:
                    os.remove(fpath)
                    logger.info("  🧹 清理过期文件: %s", fpath)
                removed += 1

    return removed


# ── 备份任务 ──────────────────────────────────────────────────────────
def backup_pools(timestamp, dry_run, logger):
    """
    备份五池JSON文件
    五池管理/*.json → data/backups/pools/YYYY-MM-DD_HHMMSS/
    """
    if not os.path.isdir(POOL_DIR):
        logger.warning("  ⚠ 五池目录不存在: %s", POOL_DIR)
        return [], 0

    dest = os.path.join(BACKUP_POOLS, timestamp)
    json_files = sorted(glob.glob(os.path.join(POOL_DIR, "*.json")))

    if not json_files:
        logger.warning("  ⚠ 五池目录下无 .json 文件")
        return [], 0

    logger.info("  📁 五池文件: %d 个", len(json_files))
    return copy_files(json_files, dest, dry_run, logger)


def backup_decision_log(timestamp, dry_run, logger):
    """
    备份决策日志
    data/decision_log.json → data/backups/logs/decision_log.YYYY-MM-DD_HHMMSS.json
    """
    if not os.path.isfile(DECISION_LOG):
        logger.warning("  ⚠ 决策日志不存在: %s", DECISION_LOG)
        return [], 0

    fname = f"decision_log.{timestamp}.json"
    dst = os.path.join(BACKUP_LOGS, fname)
    result = copy_file(DECISION_LOG, dst, dry_run, logger)
    if result:
        return [result[0]], result[1]
    return [], 0


def backup_config(timestamp, dry_run, logger):
    """
    备份配置文件
    config.yaml → data/backups/config/config.YYYY-MM-DD_HHMMSS.yaml
    """
    if not os.path.isfile(CONFIG_FILE):
        logger.warning("  ⚠ 配置文件不存在: %s", CONFIG_FILE)
        return [], 0

    fname = f"config.{timestamp}.yaml"
    dst = os.path.join(BACKUP_CONFIG, fname)
    result = copy_file(CONFIG_FILE, dst, dry_run, logger)
    if result:
        return [result[0]], result[1]
    return [], 0


def backup_reports(timestamp, dry_run, logger):
    """
    备份最近N份当日报告
    data/历史记录/ 下的当日报告 → data/backups/reports/
    """
    if not os.path.isdir(HISTORY_DIR):
        logger.warning("  ⚠ 历史记录目录不存在: %s", HISTORY_DIR)
        return [], 0

    today_dash, today_compact = get_today_prefix()

    # 匹配今日报告：支持 "2026-07-24_*" 和 "20260724_*" 两种格式
    today_files = []
    for fname in os.listdir(HISTORY_DIR):
        fpath = os.path.join(HISTORY_DIR, fname)
        if not os.path.isfile(fpath):
            continue
        if fname.startswith(today_dash) or fname.startswith(today_compact):
            today_files.append(fpath)

    # 按修改时间排序，取最近 MAX_REPORTS 份
    today_files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    today_files = today_files[:MAX_REPORTS]

    if not today_files:
        logger.info("  ℹ 今日无报告文件")
        return [], 0

    logger.info("  📁 今日报告: %d 个", len(today_files))
    dest = os.path.join(BACKUP_REPORTS, timestamp)
    return copy_files(today_files, dest, dry_run, logger)


# ── Git 操作 ──────────────────────────────────────────────────────────
def git_push(logger, dry_run):
    """执行 git add + commit + push"""
    logger.info("  📦 Git 提交与推送...")

    git_dir = PROJECT_ROOT
    if not os.path.isdir(os.path.join(git_dir, ".git")):
        logger.warning("  ⚠ 不是Git仓库: %s", git_dir)
        return False

    timestamp = get_timestamp_str()
    commit_msg = f"chore: HA backup snapshot {timestamp}"

    commands = [
        ["git", "add", "-A"],
        ["git", "commit", "-m", commit_msg, "--allow-empty"],
        ["git", "push"],
    ]

    if dry_run:
        logger.info("  [DRY-RUN] 将执行:")
        for cmd in commands:
            logger.info("    $ %s", " ".join(cmd))
        return True

    for cmd in commands:
        try:
            result = subprocess.run(
                cmd,
                cwd=git_dir,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                output = result.stdout.strip()
                if output:
                    for line in output.split("\n"):
                        logger.info("    %s", line)
            else:
                stderr = result.stderr.strip()
                if stderr and "nothing to commit" not in stderr.lower():
                    logger.warning("    ⚠ %s: %s", " ".join(cmd[:2]), stderr)
        except subprocess.TimeoutExpired:
            logger.error("    ❌ 超时: %s", " ".join(cmd[:2]))
            return False
        except OSError as e:
            logger.error("    ❌ Git 执行失败: %s", e)
            return False

    logger.info("  ✅ Git 推送完成")
    return True


# ── 清理（7天过期） ────────────────────────────────────────────────────
def cleanup_all(dry_run, logger):
    """清理所有备份类别的过期数据"""
    total_removed = 0

    # pools 和 reports 是按目录组织的
    for backup_dir in [BACKUP_POOLS, BACKUP_REPORTS]:
        total_removed += clean_old_backups(backup_dir, dry_run, logger)

    # logs 和 config 是按文件组织的
    for backup_dir, pattern in [(BACKUP_LOGS, "*.json"), (BACKUP_CONFIG, "*.yaml")]:
        total_removed += clean_old_files(backup_dir, pattern, dry_run, logger)

    return total_removed


# ── 主逻辑 ────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="天枢权衡系统高可用备份脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/ha_backup.py              # 完整备份
  python scripts/ha_backup.py --pools-only  # 仅备份五池
  python scripts/ha_backup.py --push         # 备份后推送到GitHub
  python scripts/ha_backup.py --dry-run      # 预览模式
        """,
    )
    parser.add_argument("--pools-only", action="store_true", help="仅备份五池文件")
    parser.add_argument("--push", action="store_true", help="备份后执行 git push")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不实际执行操作")
    args = parser.parse_args()

    # 初始化
    logger = setup_logger()
    start_time = time.time()
    timestamp = get_timestamp_str()

    mode_label = "预览" if args.dry_run else ("五池仅备份" if args.pools_only else "完整备份")
    logger.info("=" * 60)
    logger.info("📋 天枢权衡 HA 备份 | %s | 模式: %s", timestamp, mode_label)
    logger.info("=" * 60)

    all_files = []
    total_size = 0

    # ── 执行备份任务 ──────────────────────────────────────────────
    if not args.pools_only:
        # 完整备份：执行所有任务
        tasks = [
            ("📦 五池文件", lambda: backup_pools(timestamp, args.dry_run, logger)),
            ("📜 决策日志", lambda: backup_decision_log(timestamp, args.dry_run, logger)),
            ("⚙ 配置文件", lambda: backup_config(timestamp, args.dry_run, logger)),
            ("📊 今日报告", lambda: backup_reports(timestamp, args.dry_run, logger)),
        ]
    else:
        # 仅五池
        tasks = [
            ("📦 五池文件", lambda: backup_pools(timestamp, args.dry_run, logger)),
        ]

    for label, task_fn in tasks:
        logger.info("")
        logger.info("── %s ──", label)
        files, size = task_fn()
        all_files.extend(files)
        total_size += size

    # ── 清理过期备份 ──────────────────────────────────────────────
    logger.info("")
    logger.info("── 🧹 过期清理（%d天） ──", RETENTION_DAYS)
    removed = cleanup_all(args.dry_run, logger)
    if removed == 0:
        logger.info("  ℹ 无过期备份需要清理")
    else:
        logger.info("  ✅ 清理了 %d 个过期备份", removed)

    # ── Git 推送 ──────────────────────────────────────────────────
    if args.push:
        logger.info("")
        logger.info("── 📦 Git 推送 ──")
        git_push(logger, args.dry_run)

    # ── 统计 ──────────────────────────────────────────────────────
    elapsed = time.time() - start_time
    logger.info("")
    logger.info("=" * 60)
    logger.info("📊 备份统计")
    logger.info("  • 文件数: %d", len(all_files))
    logger.info("  • 总大小: %s", human_size(total_size))
    logger.info("  • 耗时:   %.2f 秒", elapsed)

    if args.dry_run:
        logger.info("  • 模式:   🔍 预览模式（未实际写入）")
    else:
        logger.info("  • 模式:   ✅ 实际备份")

    logger.info("  • 日志:   %s", BACKUP_LOG_FILE)
    logger.info("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())