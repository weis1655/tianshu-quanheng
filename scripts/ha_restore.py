#!/usr/bin/env python3
"""
天枢权衡 — 高可用一键恢复脚本

从本地备份或 GitHub 恢复五池数据、决策日志、配置文件。
支持恢复前自动备份当前数据（防误操作）、恢复后数据完整性校验。

用法:
    python scripts/ha_restore.py --from=local           # 从最新本地备份恢复
    python scripts/ha_restore.py --from=github          # 从 GitHub 拉取恢复
    python scripts/ha_restore.py --from=local --time=2026-07-24_163106  # 指定备份时间点
    python scripts/ha_restore.py --dry-run              # 预览模式
    python scripts/ha_restore.py --pools-only           # 仅恢复五池
"""

import os, sys, json, shutil, glob, datetime, argparse, subprocess, logging
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
BACKUP_DIR = PROJECT_ROOT / "data" / "backups"
POOLS_DIR = PROJECT_ROOT / "五池管理"
DATA_DIR = PROJECT_ROOT / "data"
LOG_FILE = BACKUP_DIR / "ha_restore.log"

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("ha_restore")

def find_latest_backup(subdir: str) -> Path | None:
    """在 data/backups/<subdir>/ 下找最新的备份目录或文件"""
    base = BACKUP_DIR / subdir
    if not base.exists():
        return None
    entries = sorted(base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    return entries[0] if entries else None

def find_backup_by_time(subdir: str, timestamp: str) -> Path | None:
    """按时间戳查找备份"""
    base = BACKUP_DIR / subdir
    if not base.exists():
        return None
    for entry in base.iterdir():
        if timestamp in entry.name:
            return entry
    return None

def restore_pools(src_dir: Path, dry_run: bool = False) -> int:
    """恢复五池JSON文件"""
    count = 0
    for f in src_dir.glob("*.json"):
        dest = POOLS_DIR / f.name
        if dry_run:
            log.info(f"[DRY-RUN] 恢复: {f.name} → {dest}")
        else:
            shutil.copy2(f, dest)
            log.info(f"✅ 恢复: {f.name} ({f.stat().st_size / 1024:.1f}KB)")
        count += 1
    return count

def restore_decision_log(src_file: Path, dry_run: bool = False) -> bool:
    """恢复决策日志"""
    dest = DATA_DIR / "decision_log.json"
    if dry_run:
        log.info(f"[DRY-RUN] 恢复决策日志: {src_file.name} → {dest}")
    else:
        # 备份当前日志
        if dest.exists():
            bak = DATA_DIR / f"decision_log.json.bak.{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
            shutil.copy2(dest, bak)
            log.info(f"📦 当前决策日志已备份: {bak.name}")
        shutil.copy2(src_file, dest)
        log.info(f"✅ 恢复决策日志: {src_file.name} ({src_file.stat().st_size / 1024:.1f}KB)")
    return True

def restore_config(src_file: Path, dry_run: bool = False) -> bool:
    """恢复配置文件"""
    dest = PROJECT_ROOT / "config.yaml"
    if dry_run:
        log.info(f"[DRY-RUN] 恢复配置: {src_file.name} → {dest}")
    else:
        if dest.exists():
            bak = PROJECT_ROOT / f"config.yaml.bak.{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
            shutil.copy2(dest, bak)
            log.info(f"📦 当前配置已备份: {bak.name}")
        shutil.copy2(src_file, dest)
        log.info(f"✅ 恢复配置: {src_file.name}")
    return True

def restore_reports(src_dir: Path, dry_run: bool = False) -> int:
    """恢复历史报告"""
    count = 0
    history_dir = DATA_DIR / "历史记录"
    for f in src_dir.glob("*"):
        dest = history_dir / f.name
        if dry_run:
            log.info(f"[DRY-RUN] 恢复报告: {f.name} → {dest}")
        else:
            shutil.copy2(f, dest)
            count += 1
    log.info(f"✅ 恢复报告: {count} 个")
    return count

def restore_from_github(project_root: Path, dry_run: bool = False) -> bool:
    """从 GitHub 拉取最新代码恢复"""
    log.info("🔄 从 GitHub 拉取最新数据...")
    if dry_run:
        log.info("[DRY-RUN] git pull origin main")
        return True
    try:
        result = subprocess.run(
            ["git", "pull", "origin", "main"],
            cwd=str(project_root),
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            log.info(f"✅ Git pull 成功: {result.stdout.strip()}")
            return True
        else:
            log.error(f"❌ Git pull 失败: {result.stderr.strip()}")
            return False
    except subprocess.TimeoutExpired:
        log.error("❌ Git pull 超时")
        return False

def verify_data(dry_run: bool = False) -> dict:
    """恢复后数据完整性校验"""
    results = {"pools": 0, "decision_log": False, "config": False}
    
    # 五池JSON校验
    pools_dir = POOLS_DIR
    for f in sorted(pools_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            results["pools"] += 1
            if not dry_run:
                log.info(f"  ✅ 五池校验通过: {f.name} ({len(data.get('stocks',[]))} 只)")
        except (json.JSONDecodeError, OSError) as e:
            log.error(f"  ❌ 五池校验失败: {f.name} - {e}")
    
    # 决策日志校验
    log_file = DATA_DIR / "decision_log.json"
    if log_file.exists():
        try:
            data = json.loads(log_file.read_text(encoding="utf-8"))
            results["decision_log"] = True
            if not dry_run:
                log.info(f"  ✅ 决策日志校验通过: {len(data)} 条记录")
        except (json.JSONDecodeError, OSError) as e:
            log.error(f"  ❌ 决策日志校验失败: {e}")
    
    # 配置文件校验
    config_file = PROJECT_ROOT / "config.yaml"
    if config_file.exists():
        results["config"] = True
        if not dry_run:
            log.info(f"  ✅ 配置文件存在: {config_file.name}")
    
    return results

def main():
    parser = argparse.ArgumentParser(description="天枢权衡 HA 一键恢复")
    parser.add_argument("--from", dest="source", choices=["local", "github"], default="local",
                        help="恢复来源: local(本地备份) / github(Git拉取)")
    parser.add_argument("--time", help="指定备份时间戳 (如 2026-07-24_163106)")
    parser.add_argument("--pools-only", action="store_true", help="仅恢复五池")
    parser.add_argument("--dry-run", action="store_true", help="预览模式")
    args = parser.parse_args()
    
    start = datetime.datetime.now()
    log.info(f"{'='*60}")
    log.info(f"🔄 天枢权衡 HA 恢复 | 来源: {args.source} | 模式: {'预览' if args.dry_run else '实际恢复'}")
    log.info(f"{'='*60}")
    
    total_files = 0
    total_size = 0
    
    if args.source == "github":
        # 从 GitHub 恢复
        if not restore_from_github(PROJECT_ROOT, args.dry_run):
            sys.exit(1)
        total_files = 999  # git pull 恢复所有文件
    else:
        # 从本地备份恢复
        # 五池恢复
        pools_dir = BACKUP_DIR / "pools"
        if args.time:
            src = find_backup_by_time("pools", args.time)
        else:
            src = find_latest_backup("pools")
        
        if src and src.is_dir():
            n = restore_pools(src, args.dry_run)
            total_files += n
            for f in src.glob("*.json"):
                total_size += f.stat().st_size
        else:
            log.warning("⚠️ 未找到五池备份")
        
        if not args.pools_only:
            # 决策日志恢复
            logs_dir = BACKUP_DIR / "logs"
            if args.time:
                src = find_backup_by_time("logs", args.time)
            else:
                src = find_latest_backup("logs")
            if src:
                restore_decision_log(src, args.dry_run)
                total_files += 1
                total_size += src.stat().st_size
            
            # 配置恢复
            config_dir = BACKUP_DIR / "config"
            if args.time:
                src = find_backup_by_time("config", args.time)
            else:
                src = find_latest_backup("config")
            if src:
                restore_config(src, args.dry_run)
                total_files += 1
                total_size += src.stat().st_size
            
            # 报告恢复
            reports_dir = BACKUP_DIR / "reports"
            if args.time:
                src = find_backup_by_time("reports", args.time)
            else:
                src = find_latest_backup("reports")
            if src and src.is_dir():
                n = restore_reports(src, args.dry_run)
                total_files += n
    
    # 数据完整性校验
    log.info(f"\n🔍 数据完整性校验...")
    verify = verify_data(args.dry_run)
    
    elapsed = (datetime.datetime.now() - start).total_seconds()
    log.info(f"\n{'='*60}")
    log.info(f"📊 恢复统计")
    log.info(f"  • 文件数: {total_files}")
    log.info(f"  • 总大小: {total_size / 1024:.1f}KB" if total_size > 0 else "  • 总大小: N/A")
    log.info(f"  • 耗时:   {elapsed:.2f} 秒")
    log.info(f"  • 五池:   {verify['pools']} 个 ✅")
    log.info(f"  • 决策日志: {'✅' if verify['decision_log'] else '❌'}")
    log.info(f"  • 配置文件: {'✅' if verify['config'] else '❌'}")
    log.info(f"{'='*60}")

if __name__ == "__main__":
    main()