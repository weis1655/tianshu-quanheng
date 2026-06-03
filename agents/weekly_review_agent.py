#!/usr/bin/env python3
"""
周复盘 Agent - 天枢权衡自我进化核心
每周六 09:00 自动执行

功能：
1. 🧹 池卫生检查 —— 候选池超期股票降级/淘汰
2. 🔬 假设验证 —— 回头查上周决策的假设兑现情况
3. ⚖️ 权重修正 —— 根据胜率调整决策权重
4. 📋 周报输出 —— 生成完整周报发送飞书

0次LLM调用，纯规则+API
"""

import json
import re
import subprocess
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from safe_file_utils import safe_read_json

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.resolve()


def add_market_prefix(code: str) -> str:
    """添加市场前缀（sh/sz）用于腾讯API"""
    code = code.strip().upper()
    code = code.replace(".SH", "").replace(".SZ", "").replace("SH", "").replace("SZ", "")
    if len(code) == 6 and code.isdigit():
        return f"{'sh' if code.startswith(('6', '5')) else 'sz'}{code}"
    return ""


def fetch_current_price(codes: list[str]) -> dict[str, float]:
    """批量获取现价（腾讯API），返回 {代码: 涨跌幅%}"""
    if not codes:
        return {}
    tx_codes = [add_market_prefix(c) for c in codes if add_market_prefix(c)]
    if not tx_codes:
        return {}
    query = ",".join(tx_codes)
    cmd = f'curl -sL --max-time 10 "https://qt.gtimg.cn/q={query}"'
    r = subprocess.run(cmd, shell=True, capture_output=True)
    content = r.stdout.decode("gbk", errors="replace").strip()

    result = {}
    for line in content.split("\n"):
        if "v_pv_none_match" in line or "~" not in line:
            continue
        parts = line.split("~")
        if len(parts) < 33:
            continue
        raw_code = parts[2].strip()
        chg_pct = parts[32].strip()  # 涨跌幅%
        try:
            result[raw_code] = float(chg_pct)
        except (ValueError, IndexError):
            continue
    return result


class WeeklyReviewAgent:
    """周复盘 Agent"""

    # 池卫生规则
    CANDIDATE_STALE_DAYS = 14      # 候选池超14天未升级 → 降级/淘汰
    KEYWATCH_STALE_DAYS = 21       # 重点池超21天 → 降级候选
    PAUSE_STALE_DAYS = 30          # 边缘池超30天 → 彻底淘汰

    def __init__(self, root: Path = None):
        self.root = root or PROJECT_ROOT
        self.history_dir = self.root / "data" / "历史记录"
        self.pool_dir = self.root / "五池管理"
        self.report_dir = self.history_dir
        self.today = datetime.now()
        self.week_start = self.today - timedelta(days=7)
        self.week_str = self.week_start.strftime("%Y-%m-%d")

    # 日期字段映射（各池字段名不一致，统一映射）
    DATE_FIELD_MAP = {
        "快筛候选池": "纳入日期",
        "重点观察池": "纳入日期",
        "边缘池": "纳入日期",
        "持仓池": "建仓日期",
    }

    POOL_NAMES = [
        "快筛候选池", "重点观察池", "边缘池", "持仓池"
    ]

    # 边缘池回归规则（P1-1）
    EDGE_POOL_REGRESSION_DAYS = 14  # 边缘池停留≥14天且有新催化 → 回归快筛候选池

    # ─────────────────────────────────────────────
    # 1. 池卫生检查（0次LLM）
    # ─────────────────────────────────────────────
    def pool_hygiene(self) -> dict:
        """检查五池健康状态，标记需清理的股票
        直接读取五池 JSON 文件，按实际纳入日期计算在池天数
        """
        from pool_manager import PoolManager
        pm = PoolManager(pool_dir=self.pool_dir)

        report = []
        actions = []

        for pool_name in self.POOL_NAMES:
            stocks = pm.get_stocks(pool_name)
            date_field = self.DATE_FIELD_MAP.get(pool_name, "纳入日期")
            stale_soft = self._get_stale_threshold(pool_name)
            stale_hard = self._get_stale_threshold_30(pool_name)

            for s in stocks:
                code = s.get("股票代码") or s.get("代码", "?")
                name = s.get("股票名称") or s.get("名称", "?")
                date_str = s.get(date_field, "").strip()

                if not date_str:
                    # 无日期字段则跳过（持仓池等无需按时间淘汰的池）
                    continue

                try:
                    entry_date = datetime.strptime(date_str, "%Y-%m-%d")
                except ValueError:
                    continue

                days = (self.today - entry_date).days

                if days > stale_hard:
                    actions.append({
                        "pool": pool_name,
                        "stock": s,
                        "days": days,
                        "action": "彻底淘汰",
                        "reason": f"在{pool_name}已{days}天，超{stale_hard}天上限"
                    })
                elif days > stale_soft:
                    actions.append({
                        "pool": pool_name,
                        "stock": s,
                        "days": days,
                        "action": self._get_downgrade_action(pool_name),
                        "reason": f"在{pool_name}已{days}天，超{stale_soft}天建议流转期"
                    })

        # 候选池最多保留15只，超量清理最旧的
        candidate_stocks = pm.get_stocks("快筛候选池")
        if len(candidate_stocks) > 15:
            # 分离有日期和无日期的
            dated = [(s, s.get(self.DATE_FIELD_MAP["快筛候选池"], "")) for s in candidate_stocks]
            dated = [(s, d) for s, d in dated if d]
            dated.sort(key=lambda x: x[1])  # 按日期升序（最旧的在前）
            overflow = [s for s, _ in dated[15:]]
            for s in overflow:
                name = s.get("股票名称") or s.get("名称", "?")
                actions.append({
                    "pool": "快筛候选池",
                    "stock": s,
                    "days": 0,
                    "action": "超量降级",
                    "reason": f"候选池超15只上限，清理最旧{name}"
                })

        # ── P1-1：边缘池自动回归检查 ─────────────────────────
        edge_stocks = pm.get_stocks("边缘池")
        candidate_codes = {s.get("代码", s.get("股票代码", "")) for s in candidate_stocks}
        
        for s in edge_stocks:
            code = s.get("代码", s.get("股票代码", ""))
            name = s.get("股票名称") or s.get("名称", "?")
            date_str = s.get("纳入日期", "").strip()
            
            if not date_str or code in candidate_codes:
                continue
            
            try:
                entry_date = datetime.strptime(date_str, "%Y-%m-%d")
                days = (self.today - entry_date).days
            except ValueError:
                continue
            
            # 停留≥14天 → 建议回归快筛候选池
            if days >= self.EDGE_POOL_REGRESSION_DAYS:
                actions.append({
                    "pool": "边缘池",
                    "stock": s,
                    "days": days,
                    "action": "回归候选池",
                    "reason": f"在边缘池已{days}天(≥{self.EDGE_POOL_REGRESSION_DAYS}天)，建议回归快筛候选池重新审查"
                })

        report.append(f"## 🧹 池卫生检查")
        report.append(f"检查日期：{self.today.strftime('%Y-%m-%d')}")
        report.append(f"覆盖周期：{self.week_str} ~ {self.today.strftime('%Y-%m-%d')}")

        if not actions:
            report.append("\n✅ 全部池状态健康，无需清理")
        else:
            report.append(f"\n发现 **{len(actions)}** 条待处理项：")
            for a in actions:
                name = a["stock"].get("股票名称") or a["stock"].get("名称", "?")
                code = a["stock"].get("股票代码") or a["stock"].get("代码", "?")
                report.append(
                    f"- **{name}({code})** 在{a['pool']}已{a['days']}天 → "
                    f"**{a['action']}**：{a['reason']}"
                )

        return {
            "report_lines": report,
            "actions": actions,
            "pool_manager": pm   # 透传给后续步骤执行实际流转
        }

    def _parse_pool_from_report(self, content: str, pool_stocks: dict):
        """从审查报告内容解析各池的股票分布"""
        import re
        # 匹配 "→ 升级 → 重点观察池" 等流转指令
        flow_pattern = re.findall(
            r"(\d{6})\s*[（(]([\u4e00-\u9fa5]{2,6})[）)]\s*→\s*([\u4e00-\u9fa5]{2,6}池)",
            content
        )
        for code, name, pool in flow_pattern:
            if pool in pool_stocks:
                # 避免重复
                existing = {s.get("股票代码", "") for s in pool_stocks[pool]}
                if code not in existing:
                    pool_stocks[pool].append({
                        "股票代码": code,
                        "股票名称": name,
                        "入池日期": self.today.strftime("%Y-%m-%d"),
                    })

        # 也尝试从报告文件修改时间推断
        report_date_match = re.search(r"(\d{4}-\d{2}-\d{2})", content)
        report_date = report_date_match.group(1) if report_date_match else self.today.strftime("%Y-%m-%d")

    def _get_stale_threshold(self, pool_name: str) -> int:
        thresholds = {
            "快筛候选池": self.CANDIDATE_STALE_DAYS,
            "重点观察池": self.KEYWATCH_STALE_DAYS,
            "边缘池": self.PAUSE_STALE_DAYS,
        }
        return thresholds.get(pool_name, 999)

    def _get_stale_threshold_30(self, pool_name: str) -> int:
        # 硬上限：候选21天，重点30天，边缘45天
        thresholds = {
            "快筛候选池": 21,
            "重点观察池": 30,
            "边缘池": 45,
        }
        return thresholds.get(pool_name, 999)

    def _get_downgrade_action(self, pool_name: str) -> str:
        actions = {
            "快筛候选池": "降级边缘池",
            "重点观察池": "降级快筛候选池",
                    }
        return actions.get(pool_name, "待清理")
    # ─────────────────────────────────────────────
    # 2. 假设验证（0次LLM）→ 联动五池流转
    # ─────────────────────────────────────────────
    def verify_hypotheses(self, pm=None) -> dict:
        """回头查上周的决策假设是否兑现，并联动五池流转
        - ❌未兑现：自动降级（候选→暂缓，重点→候选）
        - ✅兑现：记录，供后续权重修正参考
        """
        from review_evo import ReviewEvo
        evo = ReviewEvo(root=self.root)
        if pm is None:
            from pool_manager import PoolManager
            pm = PoolManager(pool_dir=self.pool_dir)

        decision_log = self.root / "data" / "复盘记录" / "决策日志.json"
        if not decision_log.exists():
            return {"report_lines": ["\n## 🔬 假设验证\n⚠️ 暂无决策日志，无假设可验证"], "verified": [], "pool_actions": []}

        log = safe_read_json(decision_log, default=None, required=False, log_error=False)
        if log is None:
            return {"report_lines": ["\n## 🔬 假设验证\n⚠️ 决策日志读取失败"], "verified": [], "pool_actions": []}

        records = log.get("决策记录", [])

        # 取上周的决策记录（新增）
        week_records = [
            r for r in records
            if r.get("日期", "") >= self.week_str
            and r.get("假设")  # 只验证有假设的记录
        ]

        # 也取已到验证时间点的记录（验证时间点 <= 今天，且尚未验证）
        verify_records = [
            r for r in records
            if r.get("验证时间点", "")
            and r.get("实际结果") is None
            and r.get("验证时间点", "") <= self.today.strftime("%Y-%m-%d")
            and r.get("假设")
        ]

        # 去重（避免重复处理）
        seen = set()
        all_to_verify = []
        for r in week_records + verify_records:
            key = r.get("股票代码", "")
            if key and key not in seen:
                seen.add(key)
                all_to_verify.append(r)

        report = []
        verified = []
        pool_actions = []

        if not all_to_verify:
            report.append("\n## 🔬 假设验证")
            report.append(f"覆盖周期：{self.week_str} ~ {self.today.strftime('%Y-%m-%d')}")
            report.append("\n📭 本周无待验证假设，无需验证")
        else:
            report.append("\n## 🔬 假设验证")
            report.append(f"覆盖周期：{self.week_str} ~ {self.today.strftime('%Y-%m-%d')}")
            report.append(f"\n待验证：**{len(all_to_verify)}** 条假设")

            # 建立股票代码→所在池的映射（扫描所有池）
            stock_pool_map = self._scan_stock_pools(pm)

            codes = [r.get("股票代码", "") for r in all_to_verify]
            prices = fetch_current_price(codes)

            for r in all_to_verify:
                code = r.get("股票代码", "")
                name = r.get("股票名称", "?")
                entry_price = r.get("推荐价格", 0)
                hypothesis = r.get("假设", "")
                expected_logic = r.get("预期逻辑", "")
                date = r.get("日期", "?")
                chg_pct = prices.get(code, 0)

                # 判断假设是否兑现（基于涨跌幅）
                if chg_pct >= 2.0:
                    verdict = "✅兑现"
                    logic_verdict = "逻辑正确"
                elif chg_pct >= 0:
                    verdict = "⏳待确认"
                    logic_verdict = "方向正确但幅度不足"
                elif chg_pct >= -2.0:
                    verdict = "⚠️存疑"
                    logic_verdict = "逻辑待观察"
                else:
                    verdict = "❌未兑现"
                    logic_verdict = "逻辑存疑，需重新审视"

                # 更新复盘结果（同时触发五池联动）
                if r.get("实际结果") is None:
                    pool_action = evo.update_result(code, chg_pct, pm=pm)
                    if pool_action:
                        pool_actions.append(pool_action)

                verified.append({
                    "code": code, "name": name,
                    "hypothesis": hypothesis,
                    "expected_logic": expected_logic,
                    "entry_price": entry_price,
                    "current_change": chg_pct,
                    "verdict": verdict,
                    "logic_verdict": logic_verdict,
                    "date": date,
                })

                report.append(f"\n### {name}({code})")
                report.append(f"- 📅 决策日期：{date}")
                report.append(f"- 💡 假设：{hypothesis or '（无记录）'}")
                report.append(f"- 🔗 预期逻辑：{expected_logic or '（无记录）'}")
                report.append(f"- 📊 实际涨跌：{chg_pct:+.2f}%（推荐价 {entry_price}元）")
                report.append(f"- {verdict} | {logic_verdict}")

                # 找到该股票所在池
                current_pool = stock_pool_map.get(code)
                if current_pool and verdict in ("❌未兑现", "⚠️存疑"):
                    downgrade_target = self._get_downgrade_pool(current_pool)
                    if downgrade_target:
                        report.append(
                            f"- 🔄 池流转：{current_pool} → **{downgrade_target}** "
                            f"（假设{verdict}，触发自动降级）"
                        )
                        pool_actions.append({
                            "code": code, "name": name,
                            "from_pool": current_pool,
                            "to_pool": downgrade_target,
                            "reason": f"假设{verdict}",
                            "verdict": verdict,
                        })

        return {"report_lines": report, "verified": verified, "pool_actions": pool_actions}

    def _scan_stock_pools(self, pm) -> dict:
        """扫描所有池，建立 {股票代码: 池名} 的映射"""
        stock_map = {}
        for pool_name in self.POOL_NAMES:
            stocks = pm.get_stocks(pool_name)
            for s in stocks:
                code = s.get("股票代码") or s.get("代码", "")
                if code:
                    stock_map[code] = pool_name
        return stock_map

    def _get_downgrade_pool(self, current_pool: str) -> str | None:
        """根据当前池返回降级目标池（None表示不流转）"""
        mapping = {
            "快筛候选池": "边缘池",
            "重点观察池": "快筛候选池",
                    }
        return mapping.get(current_pool)

    # ─────────────────────────────────────────────
    # 3. 权重修正（0次LLM）
    # ─────────────────────────────────────────────
    def adjust_weights(self) -> dict:
        """根据胜率自动调整决策权重"""
        from review_evo import ReviewEvo
        evo = ReviewEvo(root=self.root)

        report = []
        driver_stats = evo.get_driver_stats()
        stats = evo.calculate_win_rate()
        weights = evo.get_weights()

        report.append("\n## ⚖️ 权重修正")
        report.append(f"\n**累计胜率**：{stats.get('胜率', 0):.1f}%（{stats.get('盈利数', 0)}/{stats.get('总数', 0)}次已复盘）")

        if driver_stats:
            report.append("\n**按驱动类型胜率**：")
            sorted_drivers = sorted(driver_stats.items(), key=lambda x: -x[1]["胜率"])
            for driver, s in sorted_drivers:
                bar = "█" * int(s["胜率"] / 10) + "░" * (10 - int(s["胜率"] / 10))
                report.append(
                    f"- {driver}：{s['胜率']:.0f}% {bar} "
                    f"（{s['盈利']}/{s['次数']}次）"
                )

            # 执行权重调整
            adjustment = evo.adjust_weights()
            new_weights = adjustment.get("weights", {})
            report.append(f"\n**调整结果**：{adjustment.get('action', '无调整')}")

            if new_weights:
                report.append("当前权重参数：")
                report.append(
                    f"- 技术面：{new_weights.get('技术面权重', 30)}%\n"
                    f"- 基本面：{new_weights.get('基本面权重', 25)}%\n"
                    f"- 新闻驱动：{new_weights.get('新闻驱动权重', 25)}%\n"
                    f"- 情绪评分：{new_weights.get('情绪评分权重', 20)}%"
                )
        else:
            report.append("\n📭 数据不足（<3次已复盘），暂不调整权重")

        return {"report_lines": report, "driver_stats": driver_stats}

    # ─────────────────────────────────────────────
    # 4. 主流程
    # ─────────────────────────────────────────────
    def run(self, dry_run: bool = False) -> dict:
        """执行周复盘全流程

        Args:
            dry_run: True=仅报告，不实际执行流转动作
        """
        # D3：周五约束 — 非周五仅检查不执行流程
        if self.today.weekday() >= 5:
            print(f"📅 周末模式：周复盘跳过（{self.today.strftime('%A')}），仅做池健康检查")
            hygiene = self.pool_hygiene()
            return {
                "success": True,
                "report": "周末模式：周复盘跳过\n\n" + "\n".join(hygiene["report_lines"]),
                "hygiene_actions": hygiene["actions"],
                "verified": [],
                "pool_actions": [],
                "executed": [],
                "driver_stats": {},
            }
        if self.today.weekday() != 4:  # 4=Friday
            print(f"  ⚠️ 周复盘设计为周五运行（今天{self.today.strftime('%A')}），继续执行但可能缺少整周数据")
        print("📋 开始周复盘...")

        # Step 1: 池卫生检查
        hygiene = self.pool_hygiene()
        pm = hygiene["pool_manager"]
        print(f"  🧹 池卫生：{len(hygiene['actions'])} 条待处理")

        # D2：执行边缘池自动回归（≥14天 → 移入快筛候选池）
        regression_actions = [a for a in hygiene["actions"]
                              if a.get("action") == "回归候选池" and not dry_run]
        for ra in regression_actions:
            stock = ra["stock"]
            code = stock.get("代码") or stock.get("股票代码", "")
            name = stock.get("股票名称") or stock.get("名称", "?")
            ok = pm.move_stock("边缘池", "快筛候选池", code)
            if ok:
                print(f"  ✅ [D2] 边缘池回归：{name}({code}) → 快筛候选池")
            else:
                print(f"  ⚠️ [D2] 回归失败：{name}({code})")

        # D3：执行hygiene降级流转——仅硬上限触发的执行，软上限仅报告
        # ── 彻底淘汰（超硬上限）──
        eliminate_actions = [a for a in hygiene["actions"]
                             if a.get("action") == "彻底淘汰" and not dry_run]
        for da in eliminate_actions:
            stock = da["stock"]
            code = stock.get("代码") or stock.get("股票代码", "")
            name = stock.get("股票名称") or stock.get("名称", "?")
            ok = pm.remove_stock(da["pool"], code)
            print(f"  {'✅' if ok else '⚠️'} [hygiene] 彻底淘汰: {name}({code}) → 已删除")

        # ── 软上限降级跳过（仅报告，暂不执行）──
        skip_actions = [a for a in hygiene["actions"]
                        if a.get("action") in ("降级边缘池", "降级快筛候选池", "待清理")]
        for da in skip_actions:
            stock = da["stock"]
            code = stock.get("代码") or stock.get("股票代码", "")
            name = stock.get("股票名称") or stock.get("名称", "?")
            days = da["days"]
            soft_limit = self._get_stale_threshold(da["pool"])
            if dry_run:
                print(f"  📋 [hygiene] {da['action']}: {name}({code}) 在池{days}天 (dry-run)")
            else:
                print(f"  ⚠️ [hygiene] 跳过(未超硬上限): {name}({code}) 在池{days}天(软上限{soft_limit}天)")

        # ── 超量降级处理（候选池超15只上限）──
        overflow_actions = [a for a in hygiene["actions"]
                            if a.get("action") == "超量降级" and not dry_run]
        for oa in overflow_actions:
            stock = oa["stock"]
            code = stock.get("代码") or stock.get("股票代码", "")
            name = stock.get("股票名称") or stock.get("名称", "?")
            ok = pm.remove_stock(oa["pool"], code)
            print(f"  {'✅' if ok else '⚠️'} [hygiene] 超量清理: {name}({code}) 已从{oa['pool']}移除")

        # D4：S级操作池过期清理
        if not dry_run:
            s_result = pm.clean_expired_s_pool()
            if s_result.get("cleaned"):
                print(f"  🧹 S级操作池：已清理 {len(s_result['removed'])} 只过期标的")
        else:
            print(f"  📋 [dry-run] S级操作池清理跳过")

        # Step 2: 假设验证
        verify = self.verify_hypotheses(pm=pm)
        print(f"  🔬 假设验证：{len(verify['verified'])} 条已验证")

        # Step 3: 权重修正
        weights = self.adjust_weights()
        print(f"  ⚖️ 权重修正：完成")

        # Step 4: 执行池流转动作（假设验证触发）
        pool_actions = verify.get("pool_actions", [])
        executed = []
        if dry_run:
            print(f"  🔄 DRY-RUN：跳过实际流转，共 {len(pool_actions)} 条待执行")
        else:
            for pa in pool_actions:
                code = pa.get("code", "")
                from_pool = pa.get("from_pool", "")
                to_pool = pa.get("to_pool", "")
                name = pa.get("name", code)
                if from_pool and to_pool:
                    ok = pm.move_stock(from_pool, to_pool, code)
                    status = "✅" if ok else "❌"
                    print(f"  {status} 流转：{name}({code}) {from_pool} → {to_pool}")
                    executed.append({**pa, "success": ok})

        # 合并报告
        all_lines = []
        all_lines.extend(hygiene["report_lines"])
        all_lines.extend(verify["report_lines"])
        all_lines.extend(weights["report_lines"])

        # 流转执行汇总
        if pool_actions:
            all_lines.append("\n## 🔄 池流转执行汇总")
            if dry_run:
                all_lines.append("*DRY-RUN 模式，未实际执行*")
            for pa in pool_actions:
                name = pa.get("name", pa.get("code", "?"))
                all_lines.append(
                    f"- {name}({pa.get('code','')}) "
                    f"{pa.get('from_pool','?')} → {pa.get('to_pool','?')} "
                    f"| {pa.get('reason','')}"
                )

        # 签名
        all_lines.append(f"\n---\n*天枢权衡周复盘 · {self.today.strftime('%Y-%m-%d %H:%M')}*")

        report_content = "\n".join(all_lines)

        # 保存报告
        week_file = self.report_dir / f"{self.today.strftime('%Y-%m-%d')}_周复盘报告.md"
        week_file.write_text(report_content, encoding="utf-8")
        print(f"  💾 报告已保存：{week_file.name}")

        return {
            "success": True,
            "report": report_content,
            "saved_to": str(week_file),
            "hygiene_actions": hygiene["actions"],
            "verified": verify["verified"],
            "pool_actions": pool_actions,
            "executed": executed if not dry_run else [],
            "driver_stats": weights["driver_stats"],
        }


if __name__ == "__main__":
    agent = WeeklyReviewAgent()
    result = agent.run()
    if result["success"]:
        print("\n✅ 周复盘完成")
        print(f"📄 {result['saved_to']}")
