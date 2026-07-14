#!/usr/bin/env python3
"""
健康检查模块 - 验证系统状态
包括文件检查、配置检查、服务可用性检查等
"""

import os
import json
import time
import requests
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field

from safe_file_utils import safe_write_file, safe_read_json, safe_read_file
from logger import plog

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.resolve()


@dataclass
class HealthCheckResult:
    """健康检查结果"""
    name: str
    status: str  # "ok", "warning", "error"
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class HealthChecker:
    """系统健康检查器"""

    def __init__(self):
        self.checks: List[Tuple[str, callable]] = [
            ("配置文件", self._check_config),
            ("池目录", self._check_pool_dir),
            ("数据目录", self._check_data_dir),
            ("日志目录", self._check_logs_dir),
            ("持仓池", self._check_holdings_pool),
            ("API可达性", self._check_api_access),
        ]

    def check_all(self) -> Dict[str, HealthCheckResult]:
        """执行所有健康检查"""
        results = {}
        for name, check_func in self.checks:
            try:
                result = check_func()
                results[name] = result
            except Exception as e:
                results[name] = HealthCheckResult(
                    name=name,
                    status="error",
                    message=f"检查失败: {str(e)}"
                )
        return results

    def check(self, name: str) -> Optional[HealthCheckResult]:
        """执行单个检查"""
        for check_name, check_func in self.checks:
            if check_name == name:
                try:
                    return check_func()
                except Exception as e:
                    return HealthCheckResult(
                        name=name,
                        status="error",
                        message=f"检查失败: {str(e)}"
                    )
        return None

    def is_healthy(self) -> Tuple[bool, str]:
        """快速健康检查，返回(是否健康, 状态信息)"""
        results = self.check_all()
        errors = [r for r in results.values() if r.status == "error"]
        warnings = [r for r in results.values() if r.status == "warning"]

        if errors:
            return False, f"发现 {len(errors)} 个错误"
        if warnings:
            return True, f"发现 {len(warnings)} 个警告"
        return True, "所有检查通过"

    def _check_config(self) -> HealthCheckResult:
        """检查配置文件"""
        config_file = PROJECT_ROOT / "config.yaml"
        if not config_file.exists():
            return HealthCheckResult(
                name="配置文件",
                status="error",
                message="config.yaml 不存在"
            )

        try:
            import yaml
            content = safe_read_file(config_file, default=None, required=False, log_error=False)
            if content is None:
                return HealthCheckResult(
                    name="配置文件",
                    status="error",
                    message="配置文件读取失败"
                )
            config = yaml.safe_load(content)

            # 检查必要字段
            required_keys = ["api", "pools", "logging"]
            missing = [k for k in required_keys if k not in config]
            if missing:
                return HealthCheckResult(
                    name="配置文件",
                    status="warning",
                    message=f"缺少配置项: {missing}",
                    details={"config_keys": list(config.keys())}
                )

            return HealthCheckResult(
                name="配置文件",
                status="ok",
                message="配置文件正常",
                details={"config_keys": list(config.keys())}
            )
        except Exception as e:
            return HealthCheckResult(
                name="配置文件",
                status="error",
                message=f"配置文件解析失败: {str(e)}"
            )

    def _check_pool_dir(self) -> HealthCheckResult:
        """检查池目录"""
        pool_dir = PROJECT_ROOT / "五池管理"
        if not pool_dir.exists():
            return HealthCheckResult(
                name="池目录",
                status="error",
                message="五池管理目录不存在"
            )

        # 检查必要的池文件
        required_pools = ["快筛候选池", "重点观察池", "边缘池", "持仓池"]
        existing_pools = []
        missing_pools = []

        for pool_name in required_pools:
            pool_file = pool_dir / f"{pool_name}.json"
            if pool_file.exists():
                existing_pools.append(pool_name)
            else:
                missing_pools.append(pool_name)

        status = "ok" if not missing_pools else "warning"
        message = f"存在 {len(existing_pools)}/{len(required_pools)} 个池文件"

        return HealthCheckResult(
            name="池目录",
            status=status,
            message=message,
            details={
                "existing_pools": existing_pools,
                "missing_pools": missing_pools
            }
        )

    def _check_data_dir(self) -> HealthCheckResult:
        """检查数据目录"""
        data_dir = PROJECT_ROOT / "data"
        if not data_dir.exists():
            return HealthCheckResult(
                name="数据目录",
                status="error",
                message="data目录不存在"
            )

        subdirs = ["历史记录", "cache"]
        existing = [d for d in subdirs if (data_dir / d).exists()]

        return HealthCheckResult(
            name="数据目录",
            status="ok",
            message=f"data目录正常 ({len(existing)}/{len(subdirs)} 子目录)",
            details={"existing_subdirs": existing}
        )

    def _check_logs_dir(self) -> HealthCheckResult:
        """检查日志目录"""
        logs_dir = PROJECT_ROOT / "logs"
        if not logs_dir.exists():
            logs_dir.mkdir(parents=True, exist_ok=True)
            return HealthCheckResult(
                name="日志目录",
                status="warning",
                message="日志目录已自动创建"
            )

        # 检查日志文件大小
        log_files = list(logs_dir.glob("*.log"))
        total_size = sum(f.stat().st_size for f in log_files)

        # 如果日志超过100MB，给出警告
        if total_size > 100 * 1024 * 1024:
            return HealthCheckResult(
                name="日志目录",
                status="warning",
                message=f"日志文件较大 ({total_size / 1024 / 1024:.1f}MB)",
                details={"log_count": len(log_files), "total_size_mb": round(total_size / 1024 / 1024, 2)}
            )

        return HealthCheckResult(
            name="日志目录",
            status="ok",
            message=f"日志目录正常 ({len(log_files)} 个文件)",
            details={"log_count": len(log_files)}
        )

    def _check_holdings_pool(self) -> HealthCheckResult:
        """检查持仓池"""
        pool_file = PROJECT_ROOT / "五池管理" / "持仓池.json"
        if not pool_file.exists():
            return HealthCheckResult(
                name="持仓池",
                status="warning",
                message="持仓池文件不存在"
            )

        try:
            data = safe_read_json(pool_file, default=None, required=False, log_error=False)
            if data is None:
                return HealthCheckResult(
                    name="持仓池",
                    status="error",
                    message="持仓池文件读取失败"
                )

            stocks = data.get("stocks", [])
            count = len(stocks)

            return HealthCheckResult(
                name="持仓池",
                status="ok",
                message=f"持仓 {count} 只股票",
                details={"holdings_count": count}
            )
        except Exception as e:
            return HealthCheckResult(
                name="持仓池",
                status="error",
                message=f"持仓池解析失败: {str(e)}"
            )

    def _check_api_access(self) -> HealthCheckResult:
        """检查API可达性"""
        # 检查OpenCode API
        try:
            from config_loader import get_config
            cfg = get_config()
            api_url = cfg.get("api.opencode_url")

            if not api_url:
                return HealthCheckResult(
                    name="API可达性",
                    status="warning",
                    message="API地址未配置"
                )

            # 简单检查（不实际调用API）
            return HealthCheckResult(
                name="API可达性",
                status="ok",
                message="API配置正常",
                details={"api_url": api_url}
            )
        except Exception as e:
            return HealthCheckResult(
                name="API可达性",
                status="warning",
                message=f"API检查失败: {str(e)}"
            )


def check_health() -> Dict[str, Any]:
    """快速健康检查"""
    checker = HealthChecker()
    results = checker.check_all()

    # 汇总结果
    status_counts = {"ok": 0, "warning": 0, "error": 0}
    for result in results.values():
        status_counts[result.status] += 1

    is_healthy = status_counts["error"] == 0

    return {
        "healthy": is_healthy,
        "status": "healthy" if is_healthy else "unhealthy",
        "summary": status_counts,
        "checks": {name: {
            "status": r.status,
            "message": r.message,
            "details": r.details,
            "timestamp": r.timestamp
        } for name, r in results.items()},
        "checked_at": datetime.now().isoformat()
    }


def save_health_report(filepath: Optional[Path] = None):
    """保存健康检查报告"""
    result = check_health()

    if filepath is None:
        reports_dir = PROJECT_ROOT / "data" / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        # 清理超过14天的旧报告
        import time
        now = time.time()
        for f in reports_dir.glob("health_check_*.json"):
            if now - f.stat().st_mtime > 14 * 86400:
                f.unlink(missing_ok=True)
        filepath = reports_dir / f"health_check_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    # 使用 safe_write_file 替代裸 open()
    success = safe_write_file(filepath, json.dumps(result, ensure_ascii=False, indent=2))
    if not success:
        logger.error(f"[Health] 保存健康报告失败: {filepath}")

    return str(filepath)


if __name__ == "__main__":
    plog("INFO", "=== 系统健康检查 ===\n")
    result = check_health()

    plog("INFO", f"🟢 健康状态: {result['status'].upper()}")
    plog("INFO", f"   检查时间: {result['checked_at']}")
    plog("INFO", "")

    for name, check in result["checks"].items():
        icon = "✅" if check["status"] == "ok" else "⚠️" if check["status"] == "warning" else "❌"
        plog("INFO", f"{icon} {name}: {check['message']}")
    plog("INFO", "=" * 30)
    plog("INFO", f"总计: ✅{result['summary']['ok']} ⚠️{result['summary']['warning']} ❌{result['summary']['error']}")
    # 保存报告
    saved_path = save_health_report()
    plog("INFO", f"\n📄 报告已保存: {saved_path}")