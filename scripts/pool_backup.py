#!/usr/bin/env python3
"""
天枢五池自动备份脚本 — 每日收盘后运行

功能：
1. 备份 五池管理/ 下所有池JSON到 __backups__/ 目录
2. 保留最近30天的备份
3. 幂等：同名备份文件覆盖，不影响运行时数据
4. 手动触发入口：python scripts/pool_backup.py

兼容性：
- 不修改任何池文件，只读+复制
- 保留原始文件的时间戳和内容
"""

import sys
import shutil
from pathlib import Path
from datetime import datetime, timedelta

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
POOL_DIR = PROJECT_ROOT / "五池管理"
BACKUP_DIR = POOL_DIR / "__backups__"
RETENTION_DAYS = 30


def backup_pools() -> list[str]:
    """备份所有池JSON文件到__backups__目录"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    results = []

    for pool_file in sorted(POOL_DIR.glob("*.json")):
        if pool_file.name.startswith(".") or pool_file.parent == BACKUP_DIR:
            continue
        # 跳过备份文件
        if pool_file.suffix == ".json" and pool_file.stem.endswith(".bak"):
            continue

        backup_name = f"{pool_file.stem}.{today}.bak"
        backup_path = BACKUP_DIR / backup_name

        try:
            shutil.copy2(pool_file, backup_path)
            results.append(f"✅ {pool_file.name} → {backup_name}")
        except Exception as e:
            results.append(f"❌ {pool_file.name}: {e}")

    return results


def clean_old_backups() -> list[str]:
    """清理超过 RETENTION_DAYS 天的旧备份"""
    cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)
    results = []
    removed = 0

    for backup_file in sorted(BACKUP_DIR.glob("*.bak")):
        try:
            # 从文件名解析日期 .YYYYMMDD.bak
            parts = backup_file.stem.split(".")
            if len(parts) >= 2:
                date_str = parts[-1]
                file_date = datetime.strptime(date_str, "%Y%m%d")
                if file_date < cutoff:
                    backup_file.unlink()
                    removed += 1
        except (ValueError, IndexError, OSError):
            continue

    if removed:
        results.append(f"🧹 清理了 {removed} 个过期备份（>{RETENTION_DAYS}天）")
    else:
        results.append("📦 无过期备份需要清理")

    return results


def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"📋 天枢五池备份 | {now}")
    print("=" * 40)

    results = backup_pools()
    for r in results:
        print(f"  {r}")

    clean_results = clean_old_backups()
    for r in clean_results:
        print(f"  {r}")

    print(f"\n✅ 备份完成: {sum(1 for r in results if r.startswith('✅'))}/{len(results)}")


if __name__ == "__main__":
    main()