#!/usr/bin/env python3
"""
天枢权衡系统高可用监控脚本

功能：
  1. 进程监控：检查 Hermes gateway 进程是否运行
  2. 数据完整性：检查五池 JSON 文件是否可解析、决策日志格式
  3. 磁盘监控：检查磁盘使用率，超过 80% 告警，超过 90% 紧急告警
  4. 内存监控：检查内存使用率
  5. API 连通性：可选检查 LLM API 端点是否可达
  6. 输出健康检查报告：JSON 格式，可被外部程序解析
  7. --alert 模式：发现问题时打印告警信息（可被外部脚本捕获并推送）

用法：
  python3 ha_monitor.py                   # 默认模式：静默检查，输出 JSON 结果
  python3 ha_monitor.py --verbose         # 输出详细报告
  python3 ha_monitor.py --alert           # 发现问题时打印告警信息
  python3 ha_monitor.py --check-api       # 额外检查 LLM API 连通性
  python3 ha_monitor.py --verbose --alert --check-api

Exit code:
  0 = 健康
  1 = 警告（至少一个警告项）
  2 = 严重（至少一个严重项）

设计为可被 cron 调用的独立脚本，仅依赖 Python 标准库。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

# ── 路径配置 ──────────────────────────────────────────────────────────────
PROJECT_ROOT = os.path.expanduser("~/hermes-data/tianshu-quanheng")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
POOL_DIR = os.path.join(PROJECT_ROOT, "五池管理")
LOGS_DIR = os.path.join(PROJECT_ROOT, "logs")
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.yaml")

# 五池文件列表
POOL_FILES = [
    "快筛候选池.json",
    "重点观察池.json",
    "重点观察池_历史池.json",
    "边缘池.json",
    "持仓池.json",
    "S级操作池.json",
]

# 关键数据文件
DECISION_LOG_PATH = os.path.join(DATA_DIR, "decision_log.json")
CIRCUIT_BREAKER_PATH = os.path.join(DATA_DIR, "circuit_breaker_state.json")

# Hermes gateway 进程匹配关键字
GATEWAY_KEYWORDS = ["hermes_cli.main gateway", "hermes", "gateway"]


# ── 健康检查结果 ──────────────────────────────────────────────────────────
class HealthReport:
    """健康检查报告容器"""

    def __init__(self):
        self.timestamp = datetime.now().isoformat()
        self.status = "healthy"  # healthy / warning / critical
        self.exit_code = 0
        self.checks = {}
        self.alerts = []

    def add_check(self, name, status, detail, value=None, severity="warning"):
        """添加检查项

        Args:
            name: 检查项名称
            status: "ok" / "warning" / "critical"
            detail: 描述信息
            value: 原始值（可选）
            severity: 默认严重级别，仅当 status 非 ok 时使用
        """
        entry = {"status": status, "detail": str(detail)}
        if value is not None:
            entry["value"] = value
        self.checks[name] = entry

        if status == "critical":
            self.status = "critical"
            self.exit_code = max(self.exit_code, 2)
            self.alerts.append(f"[CRITICAL] {name}: {detail}")
        elif status == "warning" and self.exit_code < 2:
            self.status = "warning"
            self.exit_code = max(self.exit_code, 1)
            self.alerts.append(f"[WARNING] {name}: {detail}")

    def to_dict(self):
        return {
            "timestamp": self.timestamp,
            "status": self.status,
            "exit_code": self.exit_code,
            "checks": self.checks,
            "alerts": self.alerts,
        }

    def to_json(self, indent=2):
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


# ── 检查函数 ──────────────────────────────────────────────────────────────


def check_processes(report):
    """1. 进程监控：检查 Hermes gateway 进程是否运行"""
    try:
        # 方法1: pgrep 查找所有含 hermes 的进程
        result = subprocess.run(
            ["pgrep", "-af", "hermes"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        hermes_lines = result.stdout.strip().splitlines() if result.stdout.strip() else []
        hermes_processes = [l for l in hermes_lines if l and "grep" not in l]

        # 方法2: 专门查找 gateway 进程
        gateway_result = subprocess.run(
            ["pgrep", "-af", "gateway"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        gateway_lines = gateway_result.stdout.strip().splitlines() if gateway_result.stdout.strip() else []
        gateway_processes = [l for l in gateway_lines if l and "grep" not in l]

        # 筛选出 Hermes gateway 进程
        hermes_gateway = [
            l for l in gateway_processes
            if "hermes" in l.lower() or "python" in l.lower()
        ]

        if not hermes_gateway:
            # 宽松检查：至少有一个 gateway 进程
            if not hermes_processes:
                report.add_check(
                    "process.gateway",
                    "critical",
                    "Hermes gateway 进程未运行。没有任何 hermes 或 gateway 进程存在",
                    severity="critical",
                )
            else:
                report.add_check(
                    "process.gateway",
                    "warning",
                    f"未找到明确的 Hermes gateway 进程，但存在相关进程: {len(hermes_processes)} 个",
                    value=len(hermes_processes),
                    severity="warning",
                )
        else:
            report.add_check(
                "process.gateway",
                "ok",
                f"Hermes gateway 运行正常，PID: {', '.join(l.split()[0] for l in hermes_gateway[:3])}",
                value=len(hermes_gateway),
            )

        # 记录所有 Hermes 相关进程数
        report.add_check(
            "process.hermes_total",
            "ok",
            f"Hermes 相关进程数: {len(hermes_processes)}",
            value=len(hermes_processes),
        )

    except subprocess.TimeoutExpired:
        report.add_check("process.gateway", "warning", "进程检查超时", severity="warning")
    except FileNotFoundError:
        # pgrep 不可用，回退到 ps
        try:
            result = subprocess.run(
                ["ps", "aux"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            lines = result.stdout.splitlines()
            gateway_lines = [l for l in lines if "gateway" in l.lower() and "grep" not in l]
            hermes_lines = [l for l in lines if "hermes" in l.lower() and "grep" not in l]

            if not gateway_lines:
                report.add_check(
                    "process.gateway",
                    "critical",
                    "未找到任何 gateway 进程",
                    severity="critical",
                )
            else:
                report.add_check(
                    "process.gateway",
                    "ok",
                    f"找到 {len(gateway_lines)} 个 gateway 进程",
                    value=len(gateway_lines),
                )

            report.add_check(
                "process.hermes_total",
                "ok",
                f"Hermes 相关进程数: {len(hermes_lines)}",
                value=len(hermes_lines),
            )
        except Exception as e:
            report.add_check("process.gateway", "warning", f"进程检查失败: {e}", severity="warning")


def check_data_integrity(report):
    """2. 数据完整性：检查五池 JSON 文件是否可解析、决策日志格式"""
    # 2a. 检查五池 JSON 文件
    pool_stats = {}
    for fname in POOL_FILES:
        fpath = os.path.join(POOL_DIR, fname)
        check_name = f"data.pool.{fname.replace('.json', '')}"

        if not os.path.exists(fpath):
            report.add_check(check_name, "warning", f"文件不存在: {fname}", severity="warning")
            continue

        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, dict):
                report.add_check(check_name, "warning", f"JSON 根元素不是 dict: {fname}", severity="warning")
                continue

            # 检查 stocks 字段
            stocks = data.get("stocks", [])
            stock_count = len(stocks) if isinstance(stocks, list) else 0
            pool_stats[fname] = stock_count

            # 检查统计信息
            stats = data.get("统计", {})
            update_date = stats.get("更新日期", "") if isinstance(stats, dict) else ""

            detail = f"正常，{stock_count} 个标的"
            if update_date:
                detail += f"，更新于 {update_date}"

            report.add_check(check_name, "ok", detail, value=stock_count)

        except json.JSONDecodeError as e:
            report.add_check(check_name, "critical", f"JSON 解析失败: {e}", severity="critical")
        except (IOError, OSError) as e:
            report.add_check(check_name, "critical", f"读取失败: {e}", severity="critical")

    # 2b. 检查决策日志
    dl_check_name = "data.decision_log"
    if os.path.exists(DECISION_LOG_PATH):
        try:
            with open(DECISION_LOG_PATH, "r", encoding="utf-8") as f:
                dl_data = json.load(f)

            if isinstance(dl_data, list):
                total = len(dl_data)
                # 检查格式完整性
                required_keys = {"code", "name", "entry_price", "date"}
                valid_entries = 0
                incomplete_entries = 0
                zero_pnl = 0

                for entry in dl_data:
                    if isinstance(entry, dict):
                        if required_keys.issubset(entry.keys()):
                            valid_entries += 1
                        else:
                            incomplete_entries += 1
                        if entry.get("actual_pnl") in (0, "0", None, ""):
                            zero_pnl += 1

                detail = f"{total} 条记录，{valid_entries} 条完整"
                if incomplete_entries > 0:
                    detail += f"，{incomplete_entries} 条不完整"
                report.add_check(
                    dl_check_name, "ok", detail,
                    value={"total": total, "valid": valid_entries, "incomplete": incomplete_entries},
                )

                # 如果超过 80% 的条目无盈亏数据，视为警告
                if total > 0 and zero_pnl / total > 0.8:
                    report.add_check(
                        "data.decision_log.pnl_gap",
                        "warning",
                        f"决策日志中 {zero_pnl}/{total} ({zero_pnl / total * 100:.0f}%) 条记录无盈亏数据",
                        value={"zero_pnl": zero_pnl, "total": total},
                        severity="warning",
                    )
            else:
                report.add_check(
                    dl_check_name, "warning",
                    f"决策日志格式异常: 期望 list，实际 {type(dl_data).__name__}",
                    severity="warning",
                )

        except json.JSONDecodeError as e:
            report.add_check(dl_check_name, "critical", f"JSON 解析失败: {e}", severity="critical")
        except (IOError, OSError) as e:
            report.add_check(dl_check_name, "critical", f"读取失败: {e}", severity="critical")
    else:
        report.add_check(dl_check_name, "warning", "决策日志文件不存在", severity="warning")

    # 2c. 检查熔断器状态
    cb_check_name = "data.circuit_breaker"
    if os.path.exists(CIRCUIT_BREAKER_PATH):
        try:
            with open(CIRCUIT_BREAKER_PATH, "r", encoding="utf-8") as f:
                cb_data = json.load(f)
            if isinstance(cb_data, dict) and cb_data.get("state") == "open":
                failures = cb_data.get("consecutive_failures", "?")
                report.add_check(
                    cb_check_name, "critical",
                    f"熔断器已打开 (OPEN)，{failures} 次连续失败",
                    value=cb_data,
                    severity="critical",
                )
            else:
                state = cb_data.get("state", "closed") if isinstance(cb_data, dict) else "unknown"
                report.add_check(cb_check_name, "ok", f"熔断器状态: {state}")
        except (json.JSONDecodeError, IOError, OSError) as e:
            report.add_check(cb_check_name, "warning", f"熔断器读取失败: {e}", severity="warning")
    else:
        report.add_check(cb_check_name, "ok", "熔断器文件不存在（未启用）")

    # 2d. 检查日志目录
    if os.path.isdir(LOGS_DIR):
        try:
            log_files = [f for f in os.listdir(LOGS_DIR) if f.endswith((".log", ".txt"))]
            if log_files:
                # 检查最新日志文件是否有错误
                latest_log = max(
                    [os.path.join(LOGS_DIR, f) for f in log_files],
                    key=os.path.getmtime,
                )
                # 只检查最近 100 行
                with open(latest_log, "r", encoding="utf-8", errors="replace") as f:
                    tail_lines = f.readlines()[-100:]
                error_count = sum(1 for line in tail_lines if "ERROR" in line or "CRITICAL" in line)
                warn_count = sum(1 for line in tail_lines if "WARNING" in line or "WARN" in line)

                detail = f"日志文件 {len(log_files)} 个，最新日志中最近 100 行: {error_count} ERROR, {warn_count} WARNING"
                status = "ok"
                if error_count > 5:
                    status = "warning"
                    report.add_check(
                        "data.logs.recent_errors",
                        "warning",
                        f"最新日志 {os.path.basename(latest_log)} 中有 {error_count} 条 ERROR",
                        value={"error_count": error_count, "warn_count": warn_count},
                        severity="warning",
                    )
                report.add_check("data.logs", status, detail, value={"error_count": error_count, "warn_count": warn_count})
            else:
                report.add_check("data.logs", "ok", "日志目录存在，无 .log 文件")
        except (IOError, OSError) as e:
            report.add_check("data.logs", "warning", f"日志读取失败: {e}", severity="warning")
    else:
        report.add_check("data.logs", "ok", "日志目录不存在")


def check_disk(report):
    """3. 磁盘监控：检查磁盘使用率"""
    try:
        usage = shutil.disk_usage("/")
        total_gb = usage.total / (1024 ** 3)
        used_gb = usage.used / (1024 ** 3)
        free_gb = usage.free / (1024 ** 3)
        percent = usage.used / usage.total * 100

        detail = f"已用 {used_gb:.1f}G / 总计 {total_gb:.1f}G ({percent:.1f}%)"
        value = {"total_gb": round(total_gb, 1), "used_gb": round(used_gb, 1), "free_gb": round(free_gb, 1), "percent": round(percent, 1)}

        if percent >= 90:
            report.add_check(
                "system.disk",
                "critical",
                f"磁盘使用率严重告警: {percent:.1f}%（阈值 90%）{detail}",
                value=value,
                severity="critical",
            )
        elif percent >= 80:
            report.add_check(
                "system.disk",
                "warning",
                f"磁盘使用率告警: {percent:.1f}%（阈值 80%）{detail}",
                value=value,
                severity="warning",
            )
        else:
            report.add_check("system.disk", "ok", detail, value=value)
    except Exception as e:
        report.add_check("system.disk", "warning", f"磁盘检查失败: {e}", severity="warning")


def check_memory(report):
    """4. 内存监控：检查内存使用率"""
    try:
        # 从 /proc/meminfo 读取内存信息
        with open("/proc/meminfo", "r") as f:
            meminfo = f.read()

        mem_total = None
        mem_available = None
        for line in meminfo.splitlines():
            if line.startswith("MemTotal:"):
                mem_total = int(line.split()[1])  # kB
            elif line.startswith("MemAvailable:"):
                mem_available = int(line.split()[1])  # kB
            if mem_total is not None and mem_available is not None:
                break

        if mem_total and mem_available:
            mem_used = mem_total - mem_available
            percent = mem_used / mem_total * 100
            total_gb = mem_total / (1024 * 1024)
            used_gb = mem_used / (1024 * 1024)
            available_gb = mem_available / (1024 * 1024)

            detail = f"已用 {used_gb:.1f}G / 总计 {total_gb:.1f}G ({percent:.1f}%)"
            value = {
                "total_gb": round(total_gb, 1),
                "used_gb": round(used_gb, 1),
                "available_gb": round(available_gb, 1),
                "percent": round(percent, 1),
            }

            if percent >= 90:
                report.add_check(
                    "system.memory",
                    "critical",
                    f"内存使用率严重告警: {percent:.1f}% {detail}",
                    value=value,
                    severity="critical",
                )
            elif percent >= 80:
                report.add_check(
                    "system.memory",
                    "warning",
                    f"内存使用率告警: {percent:.1f}% {detail}",
                    value=value,
                    severity="warning",
                )
            else:
                report.add_check("system.memory", "ok", detail, value=value)
        else:
            report.add_check("system.memory", "warning", "无法读取 /proc/meminfo 完整信息", severity="warning")

    except Exception as e:
        report.add_check("system.memory", "warning", f"内存检查失败: {e}", severity="warning")


def check_api(report):
    """5. API 连通性：检查 LLM API 端点是否可达"""
    api_url = None
    # 尝试从 config.yaml 读取 API URL
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if "opencode_url:" in line:
                        api_url = line.split(":", 1)[1].strip().strip('"').strip("'")
                        break
        except (IOError, OSError):
            pass

    if not api_url:
        # 默认 URL
        api_url = "https://opencode.ai/zen/v1/chat/completions"

    # 使用 curl 检查 API 端点是否可达（不发送实际请求，只检查连通性）
    try:
        start = time.time()
        result = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--connect-timeout", "10", "--max-time", "15", api_url],
            capture_output=True,
            text=True,
            timeout=20,
        )
        elapsed = time.time() - start
        http_code = result.stdout.strip()

        if http_code and http_code not in ("0", "000"):
            report.add_check(
                "system.api",
                "ok",
                f"API 端点可达 ({api_url}), HTTP {http_code}, 响应时间 {elapsed:.1f}s",
                value={"url": api_url, "http_code": int(http_code), "response_time_s": round(elapsed, 1)},
            )
        else:
            # 检查是否有错误输出
            error_msg = result.stderr.strip() if result.stderr else "连接超时或拒绝"
            report.add_check(
                "system.api",
                "warning",
                f"API 端点不可达 ({api_url}): {error_msg}",
                value={"url": api_url, "error": error_msg},
                severity="warning",
            )
    except subprocess.TimeoutExpired:
        report.add_check(
            "system.api",
            "warning",
            f"API 端点检查超时 ({api_url})",
            value={"url": api_url, "error": "timeout"},
            severity="warning",
        )
    except FileNotFoundError:
        # curl 不可用，使用 urllib 回退
        try:
            import urllib.request
            start = time.time()
            req = urllib.request.Request(api_url, method="HEAD")
            # 设置超时，但只检查连接
            resp = urllib.request.urlopen(req, timeout=10)
            elapsed = time.time() - start
            report.add_check(
                "system.api",
                "ok",
                f"API 端点可达 ({api_url}), HTTP {resp.status}, 响应时间 {elapsed:.1f}s",
                value={"url": api_url, "http_code": resp.status, "response_time_s": round(elapsed, 1)},
            )
        except Exception as e:
            report.add_check(
                "system.api",
                "warning",
                f"API 端点不可达 ({api_url}): {e}",
                value={"url": api_url, "error": str(e)},
                severity="warning",
            )
    except Exception as e:
        report.add_check(
            "system.api",
            "warning",
            f"API 端点检查异常 ({api_url}): {e}",
            value={"url": api_url, "error": str(e)},
            severity="warning",
        )


# ── 告警输出 ──────────────────────────────────────────────────────────────


def format_alert_message(report):
    """格式化告警消息，用于邮件/飞书推送"""
    lines = []
    lines.append("🏛️ 天枢权衡系统 · 健康检查告警")
    lines.append(f"⏰ {report.timestamp}")
    lines.append(f"📊 状态: {report.status.upper()}")
    lines.append("")
    lines.append("─" * 40)

    # 按严重级别分组
    criticals = [(k, v) for k, v in report.checks.items() if v["status"] == "critical"]
    warnings = [(k, v) for k, v in report.checks.items() if v["status"] == "warning"]

    if criticals:
        lines.append(f"\n🔴 严重问题 ({len(criticals)}):")
        for name, check in criticals:
            lines.append(f"  • [{name}] {check['detail']}")

    if warnings:
        lines.append(f"\n🟡 警告 ({len(warnings)}):")
        for name, check in warnings:
            lines.append(f"  • [{name}] {check['detail']}")

    ok_count = sum(1 for v in report.checks.values() if v["status"] == "ok")
    if ok_count > 0:
        lines.append(f"\n🟢 正常: {ok_count} 项")

    lines.append("")
    lines.append("─" * 40)
    lines.append("天枢权衡 HA Monitor")

    return "\n".join(lines)


def print_alert(report):
    """打印告警信息，可被外部脚本捕获并推送"""
    print(format_alert_message(report))


# ── 主逻辑 ────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="天枢权衡系统高可用监控脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python3 ha_monitor.py                    # 静默检查，输出 JSON
  python3 ha_monitor.py --verbose          # 详细输出
  python3 ha_monitor.py --alert            # 发现问题时打印告警
  python3 ha_monitor.py --check-api        # 额外检查 API 连通性
  python3 ha_monitor.py --verbose --alert --check-api
        """,
    )
    parser.add_argument("--verbose", action="store_true", help="输出详细报告")
    parser.add_argument("--alert", action="store_true", help="发现问题时打印告警信息")
    parser.add_argument("--check-api", action="store_true", help="检查 LLM API 端点连通性")
    args = parser.parse_args()

    # 执行健康检查
    report = HealthReport()

    check_processes(report)
    check_data_integrity(report)
    check_disk(report)
    check_memory(report)

    if args.check_api:
        check_api(report)

    # 输出结果
    if args.verbose:
        # 详细报告
        print("=" * 50)
        print(f"🏛️ 天枢权衡系统 · 健康检查报告")
        print(f"⏰ {report.timestamp}")
        print(f"📊 状态: {report.status.upper()} (exit code: {report.exit_code})")
        print("=" * 50)

        # 分类输出
        categories = {
            "process": "进程监控",
            "data": "数据完整性",
            "system": "系统资源",
        }
        for prefix, cat_name in categories.items():
            items = {k: v for k, v in sorted(report.checks.items()) if k.startswith(prefix)}
            if items:
                print(f"\n── {cat_name} ──")
                for name, check in items.items():
                    icon = {"ok": "✅", "warning": "⚠️", "critical": "❌"}.get(check["status"], "❓")
                    print(f"  {icon} [{name}] {check['detail']}")

        # 汇总
        total = len(report.checks)
        critical_count = sum(1 for v in report.checks.values() if v["status"] == "critical")
        warning_count = sum(1 for v in report.checks.values() if v["status"] == "warning")
        ok_count = sum(1 for v in report.checks.values() if v["status"] == "ok")
        print(f"\n── 汇总 ──")
        print(f"  总检查项: {total}")
        print(f"  ✅ 正常: {ok_count}")
        print(f"  ⚠️ 警告: {warning_count}")
        print(f"  ❌ 严重: {critical_count}")
        print(f"  最终状态: {report.status.upper()} (exit code: {report.exit_code})")
        print("=" * 50)

    # 始终输出 JSON 报告（可被外部程序解析）
    json_output = report.to_json()
    print(json_output)

    # --alert 模式：发现问题时打印告警信息
    if args.alert and report.alerts:
        print("\n" + "!" * 50)
        print_alert(report)
        print("!" * 50)

    # 退出码
    sys.exit(report.exit_code)


if __name__ == "__main__":
    main()