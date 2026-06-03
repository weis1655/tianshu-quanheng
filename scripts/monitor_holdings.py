#!/usr/bin/env python3
"""
T+N 持仓监控脚本

功能：
1. 读取持仓池，记录每只股票的建仓日期
2. 定时检查持仓股票是否满足 T+N 卖出条件
3. 超出 T+N 窗口的股票，发送飞书通知提醒
4. 记录每日持仓快照（含持仓天数）

T+N 规则（默认 N=1，T+1制度下建仓次日可卖）：
- T+0：建仓当天（当日可买不可卖，T+1限制）
- T+1：建仓次日（首个可交易日，理论上可卖）
- 超过 N 个交易日未卖出，进入"超期持仓"监控

用法（cron 每5分钟执行）：
  */5 * * * * cd /home/seven/hermes-data/tianshu-quanheng && python3 scripts/monitor_holdings.py >> logs/monitor_holdings.log 2>&1
"""

import sys
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional

# 项目路径
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "agents"))

from logger import StructuredLogger


class HoldingsMonitor:
    """T+N 持仓监控"""

    def __init__(self, root: Path = None):
        self.root = root or PROJECT_ROOT
        self.pool_dir = self.root / "五池管理"
        self.snapshot_dir = self.root / "data" / "持仓快照"
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.logger = StructuredLogger("HoldingsMonitor")
        self.notifier = self._load_notifier()

    def _load_notifier(self):
        """延迟加载通知器"""
        try:
            from notifier import Notifier
            return Notifier()
        except Exception:
            return None

    def _is_trading_day(self, date_str: str) -> bool:
        """
        判断是否为交易日（简化版：排除周末）
        实际生产中应接入交易日历 API
        """
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d")
            # 排除周六周日
            if d.weekday() >= 5:
                return False
            return True
        except Exception:
            return False

    def _count_trading_days(self, start_date_str: str, end_date_str: str) -> int:
        """
        计算两个日期之间的交易日数（包含起止日）
        简化实现：排除周末，粗略估算
        """
        try:
            start = datetime.strptime(start_date_str, "%Y-%m-%d")
            end = datetime.strptime(end_date_str, "%Y-%m-%d")
            count = 0
            current = start
            while current <= end:
                if current.weekday() < 5:  # 周一到周五
                    count += 1
                current += timedelta(days=1)
            return count
        except Exception:
            return 0

    def load_holdings(self) -> List[Dict[str, Any]]:
        """加载持仓池数据"""
        pool_file = self.pool_dir / "持仓池.json"
        if not pool_file.exists():
            return []
        try:
            data = json.loads(pool_file.read_text(encoding="utf-8"))
            stocks = data.get("stocks", [])
            return stocks
        except Exception as e:
            self.logger.warning("load_holdings_failed", error=str(e))
            return []

    def get_holding_days(self, entry_date_str: str) -> int:
        """获取持仓天数（含建仓日）"""
        today = datetime.now().strftime("%Y-%m-%d")
        return self._count_trading_days(entry_date_str, today)

    def check_t_plus_n(self, holding_days: int, threshold: int = 1) -> Dict[str, Any]:
        """
        检查 T+N 状态

        Returns:
            {
                "phase": "T+0" | "T+1" | "超期",
                "can_sell": True/False,
                "days": int,
                "alert": "已超期N天，请评估是否卖出",
                "severity": "info" | "warning" | "danger"
            }
        """
        # T+0 = 建仓当天（T日），不可卖
        # T+1 = 首个可交易日，理论上可卖
        # 持仓天数 = N+1 表示 T+N 当天可卖
        if holding_days <= 1:
            phase = "T+0"
            can_sell = False
            alert = "T+0，建仓当天不可卖"
            severity = "info"
        elif holding_days == 2:
            phase = "T+1"
            can_sell = True
            alert = "T+1，今日理论上可卖出"
            severity = "info"
        else:
            exceed_days = holding_days - 2  # 超出T+1的天数
            phase = f"超期{exceed_days}天"
            can_sell = True
            alert = f"已持仓 {holding_days - 1} 个交易日（含建仓日），建议评估是否卖出"
            severity = "danger" if exceed_days >= 3 else "warning"

        return {
            "phase": phase,
            "can_sell": can_sell,
            "days": holding_days,
            "alert": alert,
            "severity": severity,
        }

    def fetch_current_price(self, code: str) -> Optional[Dict[str, Any]]:
        """获取股票实时行情"""
        try:
            import sys
            for mod in list(sys.modules.keys()):
                if 'market_agent' in mod:
                    del sys.modules[mod]
            from market_agent import fetch_quotes, to_api
            api_code = to_api(code)
            quotes = fetch_quotes([api_code])
            if quotes:
                return quotes[0]
        except Exception:
            pass
        return None

    def build_snapshot(self, holdings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """构建持仓快照（含T+N状态和实时行情）"""
        today = datetime.now().strftime("%Y-%m-%d")
        snapshot = []

        for s in holdings:
            code = str(s.get("代码") or s.get("股票代码", "")).strip()
            name = s.get("名称") or s.get("股票名称", "?")
            entry_date = s.get("纳入日期") or s.get("建仓日期") or today
            holding_days = self.get_holding_days(entry_date)
            t_status = self.check_t_plus_n(holding_days)

            quote = self.fetch_current_price(code)
            entry_price = s.get("推荐买入价") or s.get("买入价") or 0
            current_price = 0
            pnl_pct = 0

            if quote:
                current_price = quote.get("现价", 0)
                if entry_price and entry_price > 0:
                    pnl_pct = (current_price - entry_price) / entry_price * 100

            snapshot.append({
                "代码": code,
                "名称": name,
                "建仓日期": entry_date,
                "持仓天数": holding_days,
                "T+N状态": t_status["phase"],
                "可卖出": t_status["can_sell"],
                "建仓价": entry_price,
                "现价": current_price,
                "盈亏率": f"{pnl_pct:+.2f}%",
                "盈亏率_数值": pnl_pct,
                "警告": t_status["alert"],
                "严重级别": t_status["severity"],
                "快照时间": today,
            })

        return snapshot

    def save_snapshot(self, snapshot: List[Dict[str, Any]]):
        """保存持仓快照"""
        today = datetime.now().strftime("%Y-%m-%d")
        snapshot_file = self.snapshot_dir / f"持仓快照_{today}.json"
        try:
            with open(snapshot_file, "w", encoding="utf-8") as f:
                json.dump({
                    "快照日期": today,
                    "快照时间戳": datetime.now().isoformat(),
                    "持仓数量": len(snapshot),
                    "快照": snapshot,
                }, f, ensure_ascii=False, indent=2)
            self.logger.info("snapshot_saved", path=str(snapshot_file), count=len(snapshot))
        except Exception as e:
            self.logger.warning("snapshot_save_failed", error=str(e))

    def _send_feishu(self, content: str):
        """发送飞书通知"""
        if not self.notifier:
            return
        try:
            self.notifier.send_text(content)
        except Exception as e:
            self.logger.warning("feishu_send_failed", error=str(e))

    def _check_tn_verification(self, snapshot: List[Dict[str, Any]]) -> List[str]:
        """
        T+N 记忆闭环：检查持仓是否到达验证节点，到达则回填实际结果。

        验证节点定义：
        - 持仓超过 3 个交易日（含建仓日）→ T+2，开始验证

        Returns:
            已回填的股票代码列表
        """
        try:
            from review_evo import ReviewEvo
        except Exception:
            return []

        TN_THRESHOLD = 3  # 持仓 ≥3 个交易日（T+2）开始验证
        verified = []
        today = datetime.now().strftime("%Y-%m-%d")

        evo = ReviewEvo()

        for s in snapshot:
            code = s.get("代码", "")
            name = s.get("名称", "")
            holding_days = s.get("持仓天数", 0)
            entry_price = s.get("建仓价", 0)
            current_price = s.get("现价", 0)
            entry_date = s.get("建仓日期", "")

            if holding_days < TN_THRESHOLD:
                continue
            if not entry_price or entry_price <= 0:
                continue
            if not current_price or current_price <= 0:
                continue

            # 计算实际涨跌幅
            pnl_pct = (current_price - entry_price) / entry_price * 100

            # 回填决策日志
            ok1 = evo.record_verification(
                code=code,
                decision_date=entry_date,
                actual_pnl_pct=round(pnl_pct, 2),
                tn_date=today
            )

            # 获取原始记录并追加反思
            if ok1:
                history = evo.get_stock_history(code, limit=1)
                record = history[0] if history else {}
                reflection = evo.append_reflection(code, round(pnl_pct, 2), record)
                print(f"  ✅ {name}({code}) T+{holding_days-1}验证: {pnl_pct:+.2f}% | {reflection}")
                verified.append(f"{name}({code})")

        return verified

    def run(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        执行持仓监控

        Args:
            dry_run: True=仅打印不通知（用于测试）

        Returns:
            {
                "success": bool,
                "total": int,
                "overdue": int,
                "can_sell": int,
                "snapshots": [...],
                "feishu_sent": bool
            }
        """
        today = datetime.now().strftime("%Y-%m-%d")
        now = datetime.now().strftime("%H:%M")

        self.logger.info("monitor_run", time=now, dry_run=dry_run)

        # 加载持仓
        holdings = self.load_holdings()
        if not holdings:
            self.logger.info("no_holdings")
            return {"success": True, "total": 0, "holdings": [], "message": "持仓池为空"}

        # 构建快照
        snapshot = self.build_snapshot(holdings)

        # 统计
        total = len(snapshot)
        can_sell = sum(1 for s in snapshot if s["可卖出"])
        overdue = sum(1 for s in snapshot if s["T+N状态"].startswith("超期"))
        danger = [s for s in snapshot if s["严重级别"] == "danger"]

        # 保存快照
        self.save_snapshot(snapshot)

        # T+N 验证回填（记忆闭环核心）
        tn_verified = self._check_tn_verification(snapshot)
        if tn_verified:
            print(f"  📊 T+N 验证回填: {tn_verified}")

        # 飞书通知：仅在有超期持仓时通知
        feishu_sent = False
        if danger and not dry_run:
            lines = [f"⚠️ **【持仓超期警告】** {today} {now}\n"]
            for s in danger:
                lines.append(
                    f"- **{s['名称']}({s['代码']})** "
                    f"{s['T+N状态']} | 现价{s['现价']} | {s['盈亏率']} | "
                    f"{s['警告']}"
                )
            lines.append(f"\n> 共 {len(danger)} 只超期持仓，请评估是否卖出")
            content = "\n".join(lines)
            self._send_feishu(content)
            feishu_sent = True
        elif overdue and not dry_run:
            lines = [f"📋 **【持仓监控】** {today} {now}\n"]
            for s in snapshot:
                if not s["可卖出"]:
                    continue
                emoji = "🔴" if s["严重级别"] == "danger" else "🟡" if s["严重级别"] == "warning" else "🟢"
                lines.append(
                    f"{emoji} {s['名称']}({s['代码']}) "
                    f"{s['T+N状态']} | {s['盈亏率']}"
                )
            content = "\n".join(lines)
            self._send_feishu(content)
            feishu_sent = True

        # 打印摘要
        print(f"[HoldingsMonitor] {today} {now}")
        print(f"  持仓总数: {total}")
        print(f"  可卖出: {can_sell} 只")
        print(f"  超期持仓: {overdue} 只")
        if danger:
            for s in danger:
                print(f"  🔴 {s['名称']}({s['代码']}) {s['T+N状态']} {s['盈亏率']}")
        if feishu_sent and not dry_run:
            print(f"  📱 飞书通知已发送")
        elif dry_run:
            print(f"  [DRY RUN] 飞书通知未发送")

        return {
            "success": True,
            "total": total,
            "can_sell": can_sell,
            "overdue": overdue,
            "snapshots": snapshot,
            "danger": danger,
            "feishu_sent": feishu_sent,
        }


def main():
    """入口"""
    import argparse
    parser = argparse.ArgumentParser(description="T+N 持仓监控")
    parser.add_argument("--dry-run", action="store_true", help="仅打印，不发飞书通知")
    parser.add_argument("--threshold", type=int, default=1, help="T+N 阈值（默认1）")
    args = parser.parse_args()

    monitor = HoldingsMonitor()
    result = monitor.run(dry_run=args.dry_run)
    return result


if __name__ == "__main__":
    main()
