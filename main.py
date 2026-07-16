#!/usr/bin/env python3
"""
天枢权衡 - 主入口
多 Agent 串联执行

用法：
  python main.py                 # 自动判断时间执行
  python main.py news            # 只执行新闻分析
  python main.py screen          # 只执行快筛
  python main.py review          # 只执行审查
  python main.py decision        # 只执行决策
  python main.py ts              # 只执行时间序列分析(statsmodels)
  python main.py full            # 执行全流程
  python main.py status          # 查看五池状态
  python main.py weekly          # 执行周复盘（池卫生+假设验证+权重修正）
  python main.py portfolio       # 执行组合检查与再平衡
  python main.py event           # 执行事件驱动扫描（事件引擎）
  python main.py event backtest  # 执行事件回测
  python main.py order          # 条件单管理（默认查看状态）
  python main.py order scan     # 扫描一次条件单
  python main.py algo           # 算法交易执行（默认查看状态）
  python main.py algo backtest  # 算法回放对比验证
"""

import sys
import os
import json
from datetime import datetime, timedelta
from pathlib import Path
import re
import signal

_graceful_shutdown = False

def _signal_handler(sig, frame):
    global _graceful_shutdown
    _graceful_shutdown = True
    print(f"\n[守护] ⚡ 收到信号 {sig}，正在优雅关闭...")

signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)

# 加载 .env 文件（Hermes 配置目录）
hermes_home = os.path.expanduser("~/.hermes")
env_path = os.path.join(hermes_home, ".env")
if os.path.exists(env_path):
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
    except ImportError:
        # dotenv 未安装，尝试手动加载
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, _, value = line.partition("=")
                        os.environ.setdefault(key.strip(), value.strip())
        except Exception:
            pass

PROJECT_ROOT = Path(__file__).parent.resolve()

# MemPalace 记忆系统集成
sys.path.insert(0, str(PROJECT_ROOT))
from tianshu_memory import TianshuMemory

MEMORY = TianshuMemory()  # 全局单例，延迟初始化

# Agent 导入
sys.path.insert(0, str(PROJECT_ROOT / "agents"))
from orchestrator import Orchestrator
from news_agent import NewsAgent
from screen_agent import ScreenAgent
from review_agent import ReviewAgent
from decision_agent import DecisionAgent
from skeptic_agent import SkepticAgent
from pool_manager import PoolManager
from agents.error_handling import check_circuit_breaker, record_success, record_failure, get_circuit_breaker, save_circuit_state, restore_circuit_state

# 统一日志（初始化根日志器）
from agents.logger import setup_root_logger, plog
from agents.thresholds import CANDIDATE_EXPIRE_DAYS, EDGE_POOL_STALE_DAYS
setup_root_logger(level="INFO", log_dir=str(PROJECT_ROOT / "logs"))

# 启动时恢复熔断器状态（防止进程重启后保护丢失）
try:
    cb_path = PROJECT_ROOT / "data" / "circuit_breaker_state.json"
    for name in ["news_only", "screen", "review", "decision", "skeptic"]:
        restore_circuit_state(cb_path, get_circuit_breaker(name))
except Exception:
    pass  # 首次运行无持久化文件，安全跳过


LLM_CALL_COUNT = 0  # 追踪本次运行的LLM调用次数


def run_phase(phase: str, pools: dict, wake_ctx: str = "") -> dict:
    """执行单个阶段"""
    global LLM_CALL_COUNT
    results = {}

    # ── 阶段标记（统一日志）──
    plog("INFO", f"{'='*36}", module="phase")
    plog("INFO", f"阶段开始: {phase}", module="phase")
    plog("INFO", f"{'='*36}", module="phase")

    if phase == "news_only":
        plog("INFO", "执行新闻分析...", module="news")
        agent = NewsAgent()
        r = agent.run(wake_ctx=wake_ctx)
        LLM_CALL_COUNT += 1
        results["news"] = r
        ok = "✅" if r.get("success") else "❌"
        plog("INFO" if r.get("success") else "ERROR",
             f"{ok} 完成（LLM调用: {LLM_CALL_COUNT}次） | {r.get('error', r.get('source', ''))}",
             module="news")

    elif phase == "screen":
        plog("INFO", "执行快筛...", module="screen")
        agent = ScreenAgent()
        r = agent.run(wake_ctx=wake_ctx)
        LLM_CALL_COUNT += 1
        results["screen"] = r
        plog("INFO", f"✅ 完成（LLM调用: {LLM_CALL_COUNT}次）", module="screen")

    elif phase == "review":
        plog("INFO", "执行审查...", module="review")
        agent = ReviewAgent()
        r = agent.run(wake_ctx=wake_ctx)
        LLM_CALL_COUNT += 1
        results["review"] = r
        print(f"  ✅ 完成（LLM调用: {LLM_CALL_COUNT}次）")

    elif phase == "skeptic":
        print("🎭 执行质疑者（SkepticAgent）...")
        skeptic = SkepticAgent()
        # 读取审查报告
        ___today = datetime.now().strftime("%Y-%m-%d")
        review_file = PROJECT_ROOT / "data" / "历史记录" / f"{___today}_审查报告.md"
        review_report = ""
        try:
            review_report = review_file.read_text(encoding="utf-8") if review_file.exists() else ""
        except Exception as e:
            print(f"  ⚠️ 读取审查报告失败: {e}")

        # 读取重点观察池（二审制Gate：只质疑审查通过进入重点观察池的标的）
        pool_file = PROJECT_ROOT / "五池管理" / "重点观察池.json"
        try:
            has_stocks = pool_file.exists() and json.loads(pool_file.read_text(encoding="utf-8")).get("stocks")
        except Exception:
            has_stocks = False
        if not has_stocks:
            # ⚠️ 无升级标的时跳过 Skeptic，避免宪法冲突
            # 详见：review无标的升级到重点池→重点池为空→静默降级候选池→LLM宪法冲突拒绝审查
            print("  ⏭️ 重点观察池为空（今日无review升级标的），跳过Skeptic阶段")
            # 写入占位报告，确保DecisionAgent能读取到质疑记录
            placeholder = (
                f"# 【质疑审查报告】{___today}\n"
                f"重点观察池为空（今日无review升级标的），SkepticAgent跳过。\n"
                f"否决列表：空\n"
            )
            try:
                skeptic_file = PROJECT_ROOT / "data" / "历史记录" / f"{___today}_质疑审查报告.md"
                skeptic_file.write_text(placeholder, encoding="utf-8")
                print(f"  📝 已写入占位质疑报告（重点池为空）")
            except Exception as e:
                print(f"  ⚠️ 写入占位质疑报告失败: {e}")
            results["skeptic"] = {
                "success": True, "challenges": [], "high_risk_stocks": [],
                "high_risk_count": 0, "report": placeholder,
                "skipped": True, "reason": "no_upgrades_to_key_watch_pool"
            }
            print(f"  ✅ 完成（跳过，无升级标的）")
            return results
        if pool_file.exists():
            data = json.loads(pool_file.read_text(encoding="utf-8"))
            stocks = data.get("stocks", [])
        else:
            stocks = []
        
        # P1-3升级：扩大Skeptic覆盖范围——同时纳入S级操作池标的
        s_pool_file = PROJECT_ROOT / "五池管理" / "S级操作池.json"
        if s_pool_file.exists():
            try:
                s_data = json.loads(s_pool_file.read_text(encoding="utf-8"))
                s_stocks = s_data.get("stocks", [])
                if s_stocks:
                    # 按股票代码去重（避免与重点池重复）
                    focus_codes = {s.get("代码", s.get("股票代码", "")) for s in stocks}
                    for s in s_stocks:
                        code = s.get("代码", s.get("股票代码", ""))
                        if code and code not in focus_codes:
                            stocks.append(s)
                            focus_codes.add(code)
                    print(f"  🎭 Skeptic覆盖扩展: S级操作池+{len(s_stocks)}只, "
                          f"去重后共{len(stocks)}只")
            except Exception as e:
                print(f"  ⚠️ S级操作池读取失败: {e}")
        _ms = ReviewAgent()._get_market_state()
        # R03: 质疑审查 — 加 try/except 兜底，确保报告文件始终生成
        try:
            r = skeptic.run(stock_list=stocks, review_report=review_report, market_context={"市场状态": _ms.get("state", "震荡"), "上证涨跌": f"{_ms.get('sh_chg',0):+.2f}%"})
            LLM_CALL_COUNT += 1
        except Exception as e:
            print(f"  ❌ SkepticAgent 执行失败: {e}，写入占位报告")
            r = {"success": False, "error": str(e), "high_risk_count": 0, "high_risk_stocks": [], "challenges": [], "report": ""}
        results["skeptic"] = r
        # 写质疑结果供 DecisionAgent 注入（文件名与 DecisionAgent 读取一致）
        skeptic_file = PROJECT_ROOT / "data" / "历史记录" / f"{___today}_质疑审查报告.md"
        # P0修复：写入完整质疑报告（含所有股票的详细质疑），而非简化版
        # 简化版只含 high_risk_summary 和 summary，LLM无法获取审查通过的股票详情
        skeptic_content = r.get('report', '')
        if not skeptic_content:
            # 降级：如果report为空，用简化格式但至少包含股票列表
            high_risk_count = r.get('high_risk_count', 0)
            high_risk_stocks = r.get('high_risk_stocks', [])
            challenges = r.get('challenges', [])
            
            lines = [
                f"# 【质疑审查报告】{___today}\n",
                f"## 📊 质疑概览\n",
                f"- 总股票数: {len(challenges)}\n",
                f"- 高风险股票: {high_risk_count}只\n",
                f"- 风险等级: {'🟢 低风险' if high_risk_count == 0 else '🟡 中风险'}\n\n",
            ]
            
            if high_risk_count > 0:
                lines.append("## 🔴 高风险股票\n")
                for s in high_risk_stocks:
                    lines.append(f"- **{s.get('name')}** ({s.get('code')}): {s.get('summary', '无')}\n")
            else:
                lines.append("## 🔴 高风险股票\n")
                lines.append("（无高风险股票）\n\n")
            
            if challenges:
                lines.append("## 📋 审查通过股票\n")
                for s in challenges:
                    verdict = s.get('overall_verdict', 'challenge_required')
                    emoji = "✅" if verdict == "pass" else "⚠️"
                    lines.append(f"- {emoji} **{s.get('name')}** ({s.get('code')}): {s.get('summary', '无')}\n")
            else:
                lines.append("## 📋 审查通过股票\n")
                lines.append("（无股票）\n")
            
            skeptic_content = "".join(lines)
        
        try:
            skeptic_file.parent.mkdir(parents=True, exist_ok=True)
            skeptic_file.write_text(skeptic_content, encoding="utf-8")
        except Exception as e:
            print(f"  ⚠️ 写入质疑报告失败: {e}")
        print(f"  ✅ 完成（LLM调用: {LLM_CALL_COUNT}次） | 高风险: {r.get('high_risk_count', 0)}只")

    elif phase == "decision":
        print("💡 执行决策...")
        agent = DecisionAgent()
        r = agent.run(pools=pools, wake_ctx=wake_ctx)
        LLM_CALL_COUNT += 1
        results["decision"] = r
        print(f"  ✅ 完成（LLM调用: {LLM_CALL_COUNT}次）")

    elif phase == "ts":
        print("📈 执行时间序列分析(statsmodels)...")
        from agents.statsmodels_analysis import main as ts_main
        import io
        import contextlib
        
        # 捕获输出
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            ts_main()
        
        # 读取生成的报告
        ___today = datetime.now().strftime("%Y-%m-%d")
        ts_file = PROJECT_ROOT / "data" / "历史记录" / f"{___today}_时间序列分析.md"
        ts_report = ts_file.read_text(encoding="utf-8") if ts_file.exists() else ""
        
        r = {"success": True, "report": ts_report, "saved_to": str(ts_file)}
        results["ts"] = r
        print(f"  ✅ 完成（0次LLM）| 报告: {ts_file.name}")

    elif phase == "portfolio":
        print("📊 执行组合检查与再平衡...")
        try:
            from agents.portfolio_manager import PortfolioManager, StrategyConfig
            pm = PortfolioManager()
            # 检查熔断
            circuit_triggered = pm.check_all_circuit_breakers()
            if circuit_triggered:
                print(f"  ⚠️ 策略熔断触发: {', '.join(circuit_triggered)}")
            # 定期再平衡检查
            executed, msg = pm.periodic_rebalance()
            print(f"  {'✅' if executed else '⏸️'} 再平衡: {msg}")
            # 优胜劣汰
            eliminated = pm.survival_competition()
            if eliminated:
                print(f"  🏆 优胜劣汰淘汰: {', '.join(eliminated)}")
            # 组合风控
            alerts = pm.check_portfolio_risk(
                {s.name: [] for s in pm.get_enabled_strategies()})
            if alerts:
                for a in alerts:
                    print(f"  ⚠️ {a}")
            results["portfolio"] = {
                "success": True,
                "circuit_triggered": circuit_triggered,
                "rebalance": msg,
                "eliminated": eliminated,
                "alerts": alerts,
            }
            print(f"  ✅ 组合检查完成")
        except Exception as e:
            print(f"  ❌ 组合检查失败: {e}")
            import traceback
            traceback.print_exc()
            results["portfolio"] = {"success": False, "error": str(e)}

    return results


def build_feishu_card(phase: str, results: dict, pools: dict) -> dict:
    """构建飞书消息卡片（交互式版 v2 — WO-203）"""
    ___today = datetime.now().strftime("%Y-%m-%d")

    # 池数量统计
    pool_stats = {}
    for name, data in pools.items():
        stocks = data.get("stocks", [])
        pool_stats[name] = len(stocks) if stocks else 0

    # 构建标记
    stage_emojis = {
        "full_cycle": "🔄",
        "news_only": "📰",
        "screen": "🔍",
        "review": "🔎",
        "skeptic": "🎭",
        "decision": "💡",
    }

    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": (
                    f"**阶段**: {stage_emojis.get(phase, '🔄')} {phase}\n"
                    f"**LLM 调用**: {LLM_CALL_COUNT} 次\n"
                    f"**五池合计**: {sum(pool_stats.values())} 只"
                )
            }
        },
        {"tag": "hr"},
    ]

    # ─── 五池状态（用表格形式，比列表更紧凑）───
    pool_emoji = {
        "重点观察池": "👀", "快筛候选池": "🔬", "边缘池": "📦",
        "持仓池": "💰", "S级操作池": "⭐",
    }
    pool_lines = ["| 池 | 数量 | 明细 |"]
    pool_lines.append("|---|:----:|------|")
    for name in ["重点观察池", "快筛候选池", "S级操作池", "边缘池", "持仓池"]:
        data = pools.get(name, {})
        stocks = data.get("stocks", []) if isinstance(data, dict) else []
        count = len(stocks)
        if stocks and count > 0:
            top3 = " ".join([f"{s.get('名称','?')}({s.get('综合分','-')})" for s in stocks[:3]])
            detail = top3[:40]
        else:
            detail = "空"
        emoji = pool_emoji.get(name, "📊")
        pool_lines.append(f"| {emoji} {name} | {count} | {detail} |")
    elements.append({
        "tag": "markdown",
        "content": "### 🗂️ 五池状态\n" + "\n".join(pool_lines),
    })

    # ─── 行情快照 ───
    if "market" in results and results["market"].get("success"):
        analyzed = results["market"].get("analyzed", [])[:3]
        if analyzed:
            market_lines = ["### 📊 实时行情"]
            for s in analyzed:
                score = s.get("技术面评分", 0)
                emoji = "🟢" if score >= 75 else "🟡" if score >= 65 else "🔴"
                market_lines.append(
                    f"**{s['名称']}({s['代码']})** {s['现价']}元 {s['涨跌幅']:+.2f}% {emoji}{score}分"
                )
            elements.append({"tag": "markdown", "content": "\n".join(market_lines)})

    # ─── 审查结果摘要 ───
    if "review" in results and results["review"].get("success"):
        review_text = f"### 🔎 审查结果\n✅ 审查完成"
        # 如有截断标记
        if results["review"].get("truncated"):
            review_text += "\n⚠️ 报告存在截断"
        saved = results["review"].get("saved_to", "")
        if saved:
            review_text += f"\n📄 `{saved.split('/')[-1]}`"
        elements.append({"tag": "markdown", "content": review_text})

    # ─── 决策方案（增强版）───
    if "decision" in results and results["decision"].get("success"):
        expr = results["decision"].get("express_note", "")
        report_path = results["decision"].get("saved_to", "")
        default_report = results["decision"].get("report", "弱市不操作")

        if expr:
            content = expr.replace("\\\\n", "\n")
            status = "🟡 弱市模式"
        elif report_path:
            content = f"✅ 决策完成\n📄 `{report_path.split('/')[-1]}`"
            status = "✅ 正常"
        else:
            content = f"✅ {default_report}"
            status = "⏭️ 跳过"

        elements.append({
            "tag": "markdown",
            "content": f"### 💡 决策方案\n{status}\n{content}"
        })

    # ─── Skeptic 质疑结果 ───
    if "skeptic" in results and results["skeptic"].get("success"):
        hrc = results["skeptic"].get("high_risk_count", 0)
        skipped = results["skeptic"].get("skipped", False)
        if skipped:
            reason = results["skeptic"].get("reason", "")
            sk_text = f"⏭️ Skeptic跳过（{reason}）"
        else:
            sk_text = f"{'🔴' if hrc > 0 else '🟢'} Skeptic完成，高风险{hrc}只"
        elements.append({"tag": "markdown", "content": f"### 🎭 质疑审查\n{sk_text}"})

    # ─── 收盘提示（快筛+边缘池清理完成）───
    elements.append({"tag": "hr"})
    elements.append({
        "tag": "note",
        "elements": [{
            "tag": "plain_text",
            "content": f"🏛️ 天枢权衡 v6.2 | {___today} | 自动执行，仅供参考"
        }]
    })

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"🦞 天枢权衡 | {___today}"},
            "template": "blue"
        },
        "elements": elements,
    }

    return card


def print_pool_status(pools: dict):
    """打印五池状态"""
    print(f"\n{'='*50}")
    print("📊 五池状态")
    print(f"{'='*50}")
    for name, data in pools.items():
        stocks = data.get("stocks", [])
        print(f"\n【{name}】({len(stocks)}只)")
        if stocks:
            for s in stocks[:5]:
                code = s.get("股票代码", s.get("代码", "?"))
                name_s = s.get("股票名称", s.get("名称", "?"))
                print(f"  • {code} {name_s}")
            if len(stocks) > 5:
                print(f"  ... 还有 {len(stocks)-5} 只")
        else:
            print("  （空）")
    print()


def main():
    global LLM_CALL_COUNT

    # 解析命令行参数
    phase_arg = sys.argv[1] if len(sys.argv) > 1 else None

    # 初始化 Orchestrator
    orch = Orchestrator()
    pools = orch.get_pools()

    # 解析阶段
    if phase_arg in ["news", "news_only"]:
        phase = "news_only"
    elif phase_arg in ["screen", "筛选", "快筛"]:
        phase = "screen"
    elif phase_arg in ["review", "审查"]:
        phase = "review"
    elif phase_arg in ["decision", "决策"]:
        phase = "decision"
    elif phase_arg in ["ts", "时间序列", "arima", "statsmodels"]:
        phase = "ts"
    elif phase_arg in ["skeptic", "质疑"]:
        phase = "skeptic"
    elif phase_arg in ["full", "all", "全流程"]:
        phase = "full_cycle"
    elif phase_arg in ["天枢", "权衡", "tianshu"]:
        # 天枢权衡入口，默认识别为 full_cycle
        phase = "full_cycle"
    elif phase_arg in ["status", "池", "状态"]:
        print_pool_status(pools)
        return
    elif phase_arg in ["trigger", "触发", "条件"]:
        # 触发条件检测
        from agents.trigger import check_all_triggers
        check_all_triggers()
        return
    elif phase_arg in ["feedback", "反馈", "闭环"]:
        # 反馈闭环
        from agents.feedback_loop import FeedbackLoop
        fb = FeedbackLoop()
        fb.run()
        return
    elif phase_arg in ["sector", "板块", "轮动"]:
        # 板块轮动
        from agents.sector_rotation import save_sector_rotation
        save_sector_rotation()
        return
    elif phase_arg in ["portfolio", "组合", "组合管理"]:
        from agents.portfolio_manager import PortfolioManager
        pm = PortfolioManager()
        p_args = sys.argv[2:] if len(sys.argv) > 2 else []
        if not p_args:
            today = datetime.now().strftime("%Y-%m-%d %H:%M")
            print(f"=== 组合状态 ({today}) ===")
            print(f"  总资金: {pm._state.total_capital:,.0f}")
            print(f"  已用: {pm._state.used_capital:,.0f} | 可用: {pm._state.free_capital:,.0f}")
            print(f"  累计盈亏: {pm._state.cumulative_pnl:,.2f}")
            print(f"  回撤: {pm._state.drawdown:.2f}%")
            print(f"  策略数: {len(pm.list_strategies())} (启用{len(pm.get_enabled_strategies())})")
            for s in pm.list_strategies():
                st = pm.get_strategy_status(s.name)
                icon = "✅" if s.enabled else "⏸️"
                print(f"  {icon} {s.name:<20} {s.allocation*100:>5.0f}% v{s.version:<5} {st['status']:<18} 收益{st['total_return']:>6.1f}% 回撤{st['drawdown']:>5.1f}%")
        else:
            import subprocess
            cmd = [sys.executable, str(PROJECT_ROOT / "agents" / "portfolio_manager.py")] + p_args
            subprocess.run(cmd)
        return
    elif phase_arg in ["event", "事件", "事件驱动"]:
        p_args = sys.argv[2:] if len(sys.argv) > 2 else ["scan"]
        import subprocess
        cmd = [sys.executable, str(PROJECT_ROOT / "agents" / "event_engine.py")] + p_args
        subprocess.run(cmd)
        return
    elif phase_arg in ["order", "条件单", "止盈止损"]:
        p_args = sys.argv[2:] if len(sys.argv) > 2 else ["status"]
        import subprocess
        cmd = [sys.executable, str(PROJECT_ROOT / "agents" / "conditional_order.py")] + p_args
        subprocess.run(cmd)
        return
    elif phase_arg in ["algo", "算法", "算法交易"]:
        p_args = sys.argv[2:] if len(sys.argv) > 2 else ["list"]
        import subprocess
        cmd = [sys.executable, str(PROJECT_ROOT / "agents" / "algo_execution.py")] + p_args
        subprocess.run(cmd)
        return
    elif phase_arg in ["cache", "缓存"]:
        # 清理过期缓存
        from agents.memory_cache import MemoryCache
        MemoryCache().clear_expired()
        return
    elif phase_arg in ["health", "健康", "检查"]:
        # 健康检查
        from agents.health import check_health, save_health_report
        result = check_health()
        print(f"\n🟢 健康状态: {result['status'].upper()}")
        print(f"   检查时间: {result['checked_at']}")
        print()
        for name, check in result["checks"].items():
            icon = "✅" if check["status"] == "ok" else "⚠️" if check["status"] == "warning" else "❌"
            print(f"{icon} {name}: {check['message']}")
        print()
        print("=" * 30)
        print(f"总计: ✅{result['summary']['ok']} ⚠️{result['summary']['warning']} ❌{result['summary']['error']}")
        saved_path = save_health_report()
        print(f"\n📄 报告已保存: {saved_path}")
        return result
    elif phase_arg in ["metrics", "指标", "统计"]:
        # 查看运行指标
        from agents.metrics import get_metrics
        m = get_metrics()
        summary = m.get_summary()
        print("\n📊 运行指标:")
        print(f"  会话时长: {summary['session_duration_seconds']}秒")
        print(f"  LLM调用: {summary['llm']['total_calls']}次 ({summary['llm']['error_rate']}失败)")
        print(f"  Agent运行: {summary['agents']['total_runs']}次 (成功率: {summary['agents']['success_rate']})")
        print(f"  池操作: {summary['pools']['total_operations']}次")
        return summary
    elif phase_arg in ["agents", "插件", "plugins"]:
        # 查看 Agent 列表
        from agents.plugin_manager import get_registry
        registry = get_registry()
        registry.discover()
        print("\n📦 Agent 插件列表:")
        for plugin in registry.list_all():
            status = "✅" if plugin.enabled else "❌"
            print(f"  {status} {plugin.name}")
        print(f"\n总计: {len(registry.list_all())} 个 Agent ({len(registry.list_enabled())} 已启用)")
        return registry.list_all()
    elif phase_arg in ["circuit", "熔断"]:
        # 查看熔断器状态
        from agents.error_handling import list_circuit_breakers
        breakers = list_circuit_breakers()
        if not breakers:
            print("📊 暂无熔断器记录")
        else:
            print("\n🔴 熔断器状态:")
            for name, status in breakers.items():
                print(f"  {name}: {status['state']} (成功率: {status['success_rate']})")
        return breakers
    elif phase_arg in ["weekly", "周", "周复盘"]:
        # 周复盘（池卫生+假设验证+权重修正）
        from agents.weekly_review_agent import WeeklyReviewAgent
        agent = WeeklyReviewAgent()
        result = agent.run()
        if result["success"]:
            print(f"\n✅ 周复盘完成")
            print(f"   池清理：{len(result['hygiene_actions'])} 条")
            print(f"   假设验证：{len(result['verified'])} 条")
            saved_to = result.get('saved_to')
            if saved_to:
                print(f"   📄 {saved_to.split('/')[-1]}")
            else:
                print(f"   ⚠️ 周末模式，未保存单独报告文件")
        return result
    elif phase_arg in ["schedule", "调度"]:
        # 查看调度任务
        from agents.scheduler import get_scheduler
        scheduler = get_scheduler()
        entries = scheduler.list_entries()
        if not entries:
            print("📅 暂无调度任务")
        else:
            print("\n📅 调度任务列表:")
            for entry in entries:
                status = "✅" if entry['enabled'] else "❌"
                print(f"  {status} [{entry['type']}] {entry['name']} - 下次: {entry['next_run']}")
        return entries
    else:
        phase = orch.decide_phase(phase_arg)

    print(f"🚀 天枢权衡 启动 | 阶段: {phase}")
    print(f"📅 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # ── MemPalace 唤醒：加载跨天记忆到上下文 ──────────────────────
    try:
        wake_ctx = MEMORY.wake_up()
        if wake_ctx:
            print(f"\n{'='*50}")
            print(f"📚 MemPalace 唤醒 → {len(wake_ctx)} chars → 已注入 agents system prompt")
            print(f"{'='*50}")
        else:
            wake_ctx = ""
            print(f"\n📚 MemPalace 唤醒：空上下文（无跨天记忆）")
    except Exception as e:
        wake_ctx = ""
        print(f"\n⚠️  MemPalace 唤醒失败（不影响运行）: {e}")

    results = {}

    # 执行
    if phase == "news_only":
        if not check_circuit_breaker("news_only"):
            print(f"[熔断器] ⛔ news_only 熔断，跳过")
            results = {}
        else:
            results = run_phase("news_only", pools)
        if _graceful_shutdown:
            print("[守护] 已中断")
            return results
        if results.get("success"):
            record_success("news_only")
        else:
            record_failure("news_only")
    elif phase == "full_cycle":
        # ── 交易日守卫：判断是否为A股交易日 ───────────────────
        from agents.trading_calendar import is_trading_day
        today_date = datetime.now().date()
        if not is_trading_day(today_date):
            print(f"\n  📅 今日 {today_date} 非交易日，跳过交易相关阶段，仅执行新闻分析")
            # 只跑新闻，跳过交易阶段
            results["news"] = run_phase("news_only", pools, wake_ctx=wake_ctx).get("news", {})
            card = build_feishu_card(phase, results, orch.get_pools())
            print("📱 飞书卡片内容预览:")
            print(json.dumps(card, ensure_ascii=False, indent=2))
            return results
        # ── 周末守卫（兼容旧逻辑，非交易日守卫已覆盖）──────────
        now_weekday = datetime.now().weekday()
        is_weekend = now_weekday >= 5
        # F08: 长假后恢复 — 检测距最近交易日间隔>3天则强制全量池刷新+跳过Skeptic
        _post_holiday_mode = False
        if not is_weekend:
            try:
                from agents.trading_calendar import get_prev_trading_day
                prev_day = get_prev_trading_day(today_date, max_back=10)
                if prev_day:
                    gap = (today_date - prev_day).days
                    if gap > 3:  # 长假间隔>3天
                        _post_holiday_mode = True
                        print(f"\n  📅 长假后恢复（距上个交易日{gap}天），强制全量池刷新+跳过Skeptic")
            except Exception:
                pass
        # ── 周末守卫结束 ─────────────────────────────────

        # ── 新闻分析（周末也执行，周末也有新闻） ────────────
        print(f"\n{'='*40}")
        print("📰 阶段: news")
        print(f"{'='*40}")
        def run_news():
            ___today = datetime.now().strftime("%Y-%m-%d")
            # P2-4：先检查本地新闻联播分析文件（06:20由另一cron生成）
            news_file = PROJECT_ROOT / "data" / "历史记录" / f"{___today}_新闻联播投资分析.md"
            if news_file.exists():
                raw = news_file.read_text(encoding="utf-8")
                if len(raw) >= 500 and "新闻联播核心内容速览" in raw:
                    import re
                    table_rows = re.findall(r'\|\s*\d+\s*\|(.+?)\|(.+?)\|', raw)
                    if table_rows and len(table_rows) >= 3:
                        items = []
                        for cat, desc in table_rows:
                            cat = cat.strip()
                            desc = desc.strip()
                            items.append(f"【{cat}】{desc}" if cat else desc)
                        news_text = "\n".join(items)
                        print(f"  [News] 📂 复用本地新闻联播分析 ({len(raw)} chars → {len(news_text)} chars, {len(items)}条)")
                        from news_agent import NewsAgent
                        agent = NewsAgent()
                        r = agent.run(news_content=news_text, wake_ctx=wake_ctx)
                        global LLM_CALL_COUNT
                        LLM_CALL_COUNT += 1
                        ok = "✅" if r.get("success") else "❌"
                        print(f"  {ok} 完成（LLM调用: {LLM_CALL_COUNT}次） | 来源: 本地文件")
                        return ("news", r)
                    else:
                        print(f"  [News] ⚠️ 本地联播文件存在但表格解析失败，回退实时抓取")
                else:
                    print(f"  [News] ⚠️ 本地联播文件存在但质量不足 ({len(raw)} chars)，回退实时抓取")
            # fallback：实时抓取
            if not check_circuit_breaker("news_only"):
                print(f"[熔断器] ⛔ news_only 熔断，跳过")
                r = {}
            else:
                r = run_phase("news_only", pools, wake_ctx=wake_ctx)
            if _graceful_shutdown:
                print("[守护] 已中断")
                return results
            if r.get("success"):
                record_success("news_only")
            else:
                record_failure("news_only")
            return ("news", r["news"])

        news_result = run_news()
        results[news_result[0]] = news_result[1]

        # ── 质量门控：新闻不合格则终止 ─────────────────────
        news_data = results.get("news", {})
        if not news_data.get("success"):
            print(f"\n❌ 【严重】新闻质量不合格: {news_data.get('error', news_data.get('quality_check', '未知'))}")
            print("   无有效新闻输入，终止。")
            card = build_feishu_card(phase, results, orch.get_pools())
            print("\n📱 飞书卡片内容预览:")
            print(json.dumps(card, ensure_ascii=False, indent=2))
            return results
        # ── 新闻门控通过 ─────────────────────────────────

        # ── 周末模式：新闻已分析，跳过交易阶段 ──────────────
        if is_weekend:
            print(f"\n{'='*40}")
            print(f"📅 周末模式 (weekday={now_weekday})：新闻已分析，跳过交易相关阶段")
            print(f"{'='*40}")
            # F04: 实际执行池维护（降级停留≥14天的陈旧标的 + 边缘池清理）
            try:
                pm = PoolManager()
                # 候选池过期清理
                pm.clean_expired_candidates(max_age_days=CANDIDATE_EXPIRE_DAYS)
                print(f"  ✅ 候选池过期清理完成（>={CANDIDATE_EXPIRE_DAYS}天）")
            except Exception as e:
                print(f"  ⚠️ 候选池清理异常: {e}")
            try:
                pm = PoolManager()
                clean_result = pm.clean_expired_edge_pool(max_age_days=EDGE_POOL_STALE_DAYS)
                removed = len(clean_result.get("removed", []))
                print(f"  ✅ 边缘池清理完成：移除{removed}只过期标的")
            except Exception as e:
                print(f"  ⚠️ 边缘池清理异常: {e}")
            try:
                from scripts.sweep_downgrade import sweep_all_pools
                pm = PoolManager()
                sweep_report = sweep_all_pools(pm)
                if sweep_report["total_demoted"] > 0:
                    print(f"  🧹 全池降级扫描: {sweep_report['total_demoted']}只已降级")
                else:
                    print(f"  ✅ 全池降级扫描: 无低分滞留")
            except Exception as e:
                print(f"  ⚠️ 全池扫描异常: {e}")
            results["weekend_mode"] = True
            card = build_feishu_card(phase, results, orch.get_pools())
            print("📱 飞书卡片内容预览:")
            print(json.dumps(card, ensure_ascii=False, indent=2))
            return results
        # ── 周末守卫结束 ─────────────────────────────────

        # ── 行情（仅工作日） ───────────────────────────────
        print(f"\n{'='*40}")
        print("🔔 阶段: market")
        print(f"{'='*40}")
        from market_agent import MarketAgent
        market_agent = MarketAgent()
        r_market = market_agent.run()
        results["market"] = r_market
        print(f"  ✅ 完成（行情数据，0次LLM）")

        # ── 池价格刷新 ─────────────────────────────────────
        print(f"\n{'='*40}")
        print("🔄 刷新池价格...")
        print(f"{'='*40}")
        pm = PoolManager()
        try:
            refreshed_watch = pm.refresh_key_watch_prices()
            print(f"  ✅ 重点观察池: {len(refreshed_watch)} 只已刷新")
        except Exception as e:
            print(f"  ⚠️ 重点观察池刷新失败: {e}")
        try:
            refreshed_holdings = pm.refresh_holdings_prices()
            print(f"  ✅ 持仓池: {len(refreshed_holdings)} 只已刷新")
        except Exception as e:
            print(f"  ⚠️ 持仓池刷新失败: {e}")
        try:
            refreshed_candidate = pm.refresh_screen_candidate_prices()
            print(f"  ✅ 快筛候选池: {len(refreshed_candidate)} 只已刷新（含存量降级扫描）")
        except Exception as e:
            print(f"  ⚠️ 快筛候选池刷新失败: {e}")
        try:
            refreshed_s_pool = pm.refresh_s_operation_prices()
            print(f"  ✅ S级操作池: {len(refreshed_s_pool)} 只已刷新（含存量降级扫描）")
        except Exception as e:
            print(f"  ⚠️ S级操作池刷新失败: {e}")
        # ── 池价格刷新结束 ─────────────────────────────────

        # ── T+1 追踪数据采集 ─────────────────────────────
        market_result = results.get("market", {})
        if market_result.get("success"):
            try:
                from closed_loop_tracker import ClosedLoopTracker
                tracker = ClosedLoopTracker()
                yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
                yesterday_file = PROJECT_ROOT / "data" / "闭环追踪" / f"{yesterday}_闭环追踪.json"
                if yesterday_file.exists():
                    hist = json.loads(yesterday_file.read_text(encoding="utf-8"))
                    tracked = hist.get("stocks", {})
                    market_data = market_result.get("market_data", {})
                    if not market_data:
                        market_data = market_result.get("data", {})
                    for code, info in tracked.items():
                        if info.get("decision") and code in market_data:
                            md = market_data[code]
                            decision = info["decision"]
                            plan = decision.get("plan", {})
                            try:
                                tracker.record_t1_performance(
                                    code=code,
                                    t1_date=datetime.now().strftime("%Y-%m-%d"),
                                    t1_open=md.get("open", md.get("现价", md.get("current", 0))),
                                    t1_close=md.get("现价", md.get("current", md.get("close", 0))),
                                    decision_price=plan.get("decision_price", md.get("昨收", 0)),
                                    stop_loss=plan.get("stop_loss", 0),
                                    target_1=plan.get("target_price", plan.get("first_target", 0)),
                                )
                            except Exception as e:
                                print(f"  [T+1] ⚠️ {code} 追踪失败: {e}")
                    print(f"  [T+1] ✅ 回溯 {len(tracked)} 只标的涨跌幅")
            except ImportError as e:
                print(f"  [T+1] ⚠️ 追踪模块加载失败: {e}")
            except Exception as e:
                print(f"  [T+1] ⚠️ 追踪异常: {e}")
        # ── T+1 采集结束 ──────────────────────────────────

        # 然后串行：快筛→审查→决策（P0-1：失败级联终止）
        pools = orch.get_pools()
        if not check_circuit_breaker("screen"):
            print(f"[熔断器] ⛔ screen 熔断，跳过")
            r_screen = {}
        else:
            r_screen = run_phase("screen", pools, wake_ctx=wake_ctx)
        if _graceful_shutdown:
            print("[守护] 已中断")
            return results
        results.update(r_screen)
        if not results.get("screen", {}).get("success"):
            print("\n❌ 【级联终止】快筛失败，停止后续阶段")
            card = build_feishu_card(phase, results, orch.get_pools())
            print("\n📱 飞书卡片内容预览:")
            print(json.dumps(card, ensure_ascii=False, indent=2))
            return results
        record_success("screen")

        pools = orch.get_pools()
        if not check_circuit_breaker("review"):
            print(f"[熔断器] ⛔ review 熔断，跳过")
            r_review = {}
        else:
            r_review = run_phase("review", pools, wake_ctx=wake_ctx)
        if _graceful_shutdown:
            print("[守护] 已中断")
            return results
        results.update(r_review)
        if not results.get("review", {}).get("success"):
            print("\n❌ 【级联终止】审查失败，停止后续阶段")
            card = build_feishu_card(phase, results, orch.get_pools())
            print("\n📱 飞书卡片内容预览:")
            print(json.dumps(card, ensure_ascii=False, indent=2))
            return results
        record_success("review")

        # ── 审查报告截断检测（v5.91）──────────────────────────
        # 检查每只股票的四维表是否完整（缺失 流转方向 = 截断）
        review_report_path = results.get("review", {}).get("saved_to", "")
        review_truncated = False
        if review_report_path:
            try:
                report_text = Path(review_report_path).read_text(encoding="utf-8")
                # 按 ## 分割股票区块
                stock_blocks = re.findall(r'## \w+ .+?(?=\n## |\n---\n## 五池|\Z)', report_text, re.DOTALL)
                for block in stock_blocks:
                    if re.search(r'^## \d{6} ', block, re.MULTILINE):
                        if "流转方向" not in block and "综合评分" not in block:
                            review_truncated = True
                            name_match = re.search(r'## \d{6} (.+)', block)
                            stock_name = name_match.group(1) if name_match else "未知"
                            print(f"  ⚠️ 【截断检测】{stock_name} 四维表不完整，缺失流转方向")
                            break
                if review_truncated:
                    print("  ⚠️ 【截断降级】审查报告存在截断，标记 truncated=True，继续后续阶段")
                    results["review"]["truncated"] = True
            except Exception as e:
                print(f"  ⚠️ 【截断检测】读取审查报告失败: {e}")

        # ── 弱市极速模式（P2-1：非完全跳过，改为简化审查）──────────
        _ms = ReviewAgent()._get_market_state()
        ____today = datetime.now().strftime("%Y-%m-%d")
        if _ms.get("state", "") in ("震荡偏弱", "偏空"):
            # 弱市简化审查：扫描重点池+S池，检查明显风险信号
            print(f"\n  📉 市场状态[{_ms.get('state','')}]偏弱，执行简化审查模式")
            simplified_blocked = []  # [(code, name, reason)]
            simplified_passed = []
            simplified_observations = []

            # 加载重点观察池
            key_pool_file = PROJECT_ROOT / "五池管理" / "重点观察池.json"
            all_stocks_for_review = []
            if key_pool_file.exists():
                try:
                    pool_data = json.loads(key_pool_file.read_text(encoding="utf-8"))
                    all_stocks_for_review.extend(pool_data.get("stocks", []))
                except Exception:
                    pass

            # 也检查S级操作池
            s_pool_file = PROJECT_ROOT / "五池管理" / "S级操作池.json"
            if s_pool_file.exists():
                try:
                    s_data = json.loads(s_pool_file.read_text(encoding="utf-8"))
                    s_codes = {s.get("代码","") for s in all_stocks_for_review}
                    for s in s_data.get("stocks", []):
                        if s.get("代码","") not in s_codes:
                            all_stocks_for_review.append(s)
                except Exception:
                    pass

            # 简化审查：检查明显风险信号（PE异常/单日暴涨/高换手）
            for s in all_stocks_for_review:
                code = str(s.get("代码", s.get("股票代码", "")))
                name = str(s.get("名称", s.get("股票名称", "?")))
                score = float(s.get("综合分", s.get("综合评分", 0)))
                # 解析行情数据
                try:
                    chg_str = str(s.get("今日涨跌", "0%")).replace("%", "").replace("+", "")
                    daily_chg = float(chg_str)
                except (ValueError, TypeError):
                    daily_chg = 0
                try:
                    pe = float(s.get("PE", 0) or 0)
                except (ValueError, TypeError):
                    pe = 0
                try:
                    turnover = float(s.get("换手率", 0) or 0)
                except (ValueError, TypeError):
                    turnover = 0

                risk_flags = []

                # R1: 单日暴涨 >15%
                if daily_chg > 15:
                    risk_flags.append(f"单日涨幅{daily_chg:.1f}%>15%，短期过热")

                # R2: PE异常（>80或负值）
                if pe > 80:
                    risk_flags.append(f"PE{pe:.0f}>80，估值偏高")
                elif pe < 0 and pe != 0:
                    risk_flags.append(f"PE{pe:.0f}为负，持续亏损")

                # R3: 换手率异常高
                if turnover > 12:
                    risk_flags.append(f"换手率{turnover:.1f}%>12%，筹码松动")

                # R4: 评分过低
                if score <= 0:
                    risk_flags.append(f"评分{score}分，已触及安全底线")
                elif score < 50:
                    risk_flags.append(f"评分{score}分<50，基本面存疑")

                # R5: 量比过大（>5，主力出货信号）
                try:
                    vol_ratio = float(s.get("量比", s.get("vol_ratio", 0)) or 0)
                    if vol_ratio > 5:
                        risk_flags.append(f"量比{vol_ratio:.1f}>5，放量出货嫌疑")
                except (ValueError, TypeError):
                    pass

                # R6: 价格处于近20日高位（>80%分位，追高风险）
                try:
                    high_20d = float(s.get("近20日最高", s.get("high_20d", 0)) or 0)
                    cur_price = float(s.get("当前价", s.get("close", 0)) or 0)
                    if high_20d > 0 and cur_price > 0:
                        pct = cur_price / high_20d
                        if pct > 0.85:
                            risk_flags.append(f"价格处于20日高位{pct*100:.0f}%，追高风险")
                except (ValueError, TypeError):
                    pass

                # R7: 成交量异常放大（今日量>5日均量3倍）
                try:
                    vol_today = float(s.get("今日量", s.get("vol", 0)) or 0)
                    vol_avg5 = float(s.get("5日均量", s.get("vol_avg5", 0)) or 0)
                    if vol_avg5 > 0 and vol_today > vol_avg5 * 3:
                        risk_flags.append(f"成交量异常放大(今日量/5日均量={vol_today/vol_avg5:.1f}倍)")
                except (ValueError, TypeError):
                    pass

                if risk_flags:
                    simplified_blocked.append((code, name, risk_flags))
                else:
                    simplified_passed.append((code, name, score))

            # 构建简化质疑报告
            report_lines = [
                f"# 【质疑审查报告】{____today}（弱市简化审查）",
                "",
                f"## 📊 审查概况",
                f"- 审查范围：重点观察池 + S级操作池",
                f"- 审查标的：{len(all_stocks_for_review)}只",
                f"- 审查方法：弱市简化模式（纯规则，无LLM调用）",
                f"- 风险规则：日涨幅>15%/PE>80或负/换手率>12%/评分<50/量比>5/价格处20日高位>85%/成交量放大>3倍",
                "",
            ]
            if simplified_blocked:
                report_lines.append(f"## 🔴 否决列表（{len(simplified_blocked)}只）")
                for code, name, flags in simplified_blocked:
                    report_lines.append(f"- **{name}**（{code}）：{'；'.join(flags)}")
                    simplified_observations.append({
                        "code": code, "name": name,
                        "reason": "；".join(flags),
                    })
                report_lines.append("")
            else:
                report_lines.append("## 🔴 否决列表")
                report_lines.append("（无否决）")
                report_lines.append("")

            if simplified_passed:
                report_lines.append(f"## ✅ 审查通过（{len(simplified_passed)}只）")
                for code, name, score in simplified_passed:
                    report_lines.append(f"- {name}（{code}）{score:.0f}分")
                report_lines.append("")

            # 写入质疑审查报告
            report_content = "\n".join(report_lines)
            try:
                skeptic_report_path = PROJECT_ROOT / "data" / "历史记录" / f"{____today}_质疑审查报告.md"
                skeptic_report_path.parent.mkdir(parents=True, exist_ok=True)
                skeptic_report_path.write_text(report_content, encoding="utf-8")
                print(f"  📝 已写入弱市简化审查报告")
            except Exception as e:
                print(f"  ⚠️ 写入审查报告失败: {e}")

            # 写入质疑审查裁决（供 DecisionAgent Gate 读取）
            try:
                verdict_data = {
                    "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "mode": "weak_market_simplified",
                    "blocked": simplified_observations,
                    "passed_codes": [c for c, _, _ in simplified_passed],
                }
                verdict_path = PROJECT_ROOT / "data" / "历史记录" / f"{____today}_质疑审查裁决.json"
                verdict_path.write_text(json.dumps(verdict_data, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"  📝 已写入弱市审查裁决（否决{len(simplified_blocked)}只）")
            except Exception as e:
                print(f"  ⚠️ 写入审查裁决失败: {e}")

            # 输出结果
            results["skeptic"] = {
                "success": True,
                "challenges": simplified_observations,
                "high_risk_stocks": simplified_observations,
                "high_risk_count": len(simplified_blocked),
                "report": report_content,
                "skipped": False,
                "mode": "weak_market_simplified",
            }
            record_success("skeptic")

            # 决策方案
            blocked_codes_set = {c for c, _, _ in simplified_blocked}
            # 在≥85分的极致标的中排除被否决的
            urgent_candidates = []
            for code, name, score in simplified_passed:
                if score >= 85 and code not in blocked_codes_set:
                    urgent_candidates.append({"code": code, "name": name, "score": score})

            if urgent_candidates and len(urgent_candidates) <= 3:
                names = [c["name"] for c in urgent_candidates]
                print(f"\n  📉 市场偏弱，但{len(urgent_candidates)}只标的通过简化审查，可关注")
                results["decision"] = {
                    "success": True,
                    "report": "弱市可关注",
                    "express_note": f"📉 市场偏弱建议谨慎，以下标的通过简化审查：\\n" +
                                    "\\n".join([f"  • {c['name']}({c['code']}) {c['score']:.0f}分" for c in urgent_candidates])
                }
            else:
                results["decision"] = {"success": True, "report": "弱市不操作"}
            record_success("decision")
        else:
            # ── 质疑者 Gate：审查通过后必经 SkepticAgent ──────────
            pools = orch.get_pools()
            if not check_circuit_breaker("skeptic"):
                print(f"[熔断器] ⛔ skeptic 熔断，跳过")
                r_skeptic = {}
            else:
                r_skeptic = run_phase("skeptic", pools, wake_ctx=wake_ctx)
            if _graceful_shutdown:
                print("[守护] 已中断")
                return results
            results.update(r_skeptic)
            record_success("skeptic") if results.get("skeptic", {}).get("success") else record_failure("skeptic")

            # 宪法守卫：质疑报告缺失时跳过决策，防止无审查决策
            if not results.get("skeptic", {}).get("success"):
                print("\n❌ 【宪法守卫】质疑报告缺失或失败，跳过决策阶段（违反DecisionAgent宪法：决策前必须提供质疑审查报告）")
                results["decision"] = {"success": False, "error": "质疑报告缺失，决策阶段被宪法守卫拦截"}
                record_failure("decision")
            else:
                pools = orch.get_pools()
                if not check_circuit_breaker("decision"):
                    print(f"[熔断器] ⛔ decision 熔断，跳过")
                    r_decision = {}
                else:
                    r_decision = run_phase("decision", pools, wake_ctx=wake_ctx)
                if _graceful_shutdown:
                    print("[守护] 已中断")
                    return results
                results.update(r_decision)
                record_success("decision") if results.get("decision", {}).get("success") else record_failure("decision")

        # ── P3：边缘池清理 + 候选池过期清理（决策阶段后自动执行）────
        print(f"\n{'='*40}")
        print("🧹 执行池清理...")
        print(f"{'='*40}")
        try:
            pm = PoolManager()
            # 候选池过期清理（工作日也执行，防止候选池滞留）
            pm.clean_expired_candidates(max_age_days=CANDIDATE_EXPIRE_DAYS)
            print(f"  ✅ 候选池过期清理完成（>={CANDIDATE_EXPIRE_DAYS}天）")
        except Exception as e:
            print(f"  ⚠️ 候选池清理异常（不影响主流程）: {e}")
        try:
            pm = PoolManager()
            clean_result = pm.clean_expired_edge_pool()
            removed_count = len(clean_result.get("removed", []))
            remaining = clean_result.get("remaining_count", 0)
            print(f"  ✅ 边缘池清理完成：移除{removed_count}只，剩余{remaining}只")
        except Exception as e:
            print(f"  ⚠️ 边缘池清理异常（不影响主流程）: {e}")
        # ── 池清理结束 ─────────────────────────────────────────────
    elif phase == "screen":
        if not check_circuit_breaker("screen"):
            print(f"[熔断器] ⛔ screen 熔断，跳过")
            results = {}
        else:
            results = run_phase("screen", pools, wake_ctx=wake_ctx)
        if _graceful_shutdown:
            print("[守护] 已中断")
            return results
        if results.get("success"):
            record_success("screen")
        else:
            record_failure("screen")
    elif phase == "review":
        if not check_circuit_breaker("review"):
            print(f"[熔断器] ⛔ review 熔断，跳过")
            results = {}
        else:
            results = run_phase("review", pools, wake_ctx=wake_ctx)
        if _graceful_shutdown:
            print("[守护] 已中断")
            return results
        if results.get("success"):
            record_success("review")
        else:
            record_failure("review")
    elif phase == "skeptic":
        if not check_circuit_breaker("skeptic"):
            print(f"[熔断器] ⛔ skeptic 熔断，跳过")
            results = {}
        else:
            results = run_phase("skeptic", pools, wake_ctx=wake_ctx)
        if _graceful_shutdown:
            print("[守护] 已中断")
            return results
        if results.get("success"):
            record_success("skeptic")
        else:
            record_failure("skeptic")
    elif phase == "decision":
        if not check_circuit_breaker("decision"):
            print(f"[熔断器] ⛔ decision 熔断，跳过")
            results = {}
        else:
            results = run_phase("decision", pools, wake_ctx=wake_ctx)
        if _graceful_shutdown:
            print("[守护] 已中断")
            return results
        if results.get("success"):
            record_success("decision")
        else:
            record_failure("decision")

    # 打印结果摘要
    print(f"\n{'='*50}")
    print(f"✅ 执行完成 | 本次LLM调用: {LLM_CALL_COUNT}次")
    print(f"{'='*50}")

    # ── 五池健康审计 ──────────────────────────────────────
    try:
        from scripts.pool_health_audit import audit
        pool_issues = audit()
        if pool_issues:
            print(f"\n⚠️ 五池健康审计发现 {len(pool_issues)} 个问题:")
            for i in pool_issues:
                print(f"  ❌ {i}")
        else:
            print(f"\n✅ 五池健康审计通过")
    except Exception:
        pass  # 审计失败不阻断主流程
    # ──────────────────────────────────────────────────────

    # ── F2: 全池低分标的降级扫描（降级延迟修复 — 使用独立扫描脚本，覆盖全部5池）──
    try:
        from scripts.sweep_downgrade import sweep_all_pools
        pm = PoolManager()
        report = sweep_all_pools(pm)
        if report["total_demoted"] > 0:
            print(f"  🧹 全池低分扫描: 共降级 {report['total_demoted']} 只低分标的(评分<{65})至边缘池")
            for pool in report["scanned_pools"]:
                if pool["demoted"] > 0:
                    print(f"       {pool['pool']}: 降级 {pool['demoted']} 只")
        else:
            print(f"  ✅ 全池低分扫描: 无低分标的残留")
    except Exception as e:
        print(f"  ⚠️ 全池低分扫描异常（不影响主流程）: {e}")

    # ── F3: 准确率模式刷新（WO-201）─────────────────────────
    try:
        from scripts.win_rate_analyzer import main as wr_main
        wr_main()
        print(f"  ✅ 准确率模式已刷新")
    except Exception as e:
        print(f"  ⚠️ 准确率模式刷新异常（不影响主流程）: {e}")
    # ──────────────────────────────────────────────────────
    # ──────────────────────────────────────────────────────

    # ── MemPalace 保存：五池状态+执行结果写入知识图谱 ─────────────
    try:
        pools_final = orch.get_pools()
        save_stats = MEMORY.save_run_summary(phase, results, pools_final)
        print(f"\n📚 MemPalace 保存: {save_stats['triples']} 条三元组")
    except Exception as e:
        print(f"\n⚠️  MemPalace 保存失败（不影响结果）: {e}")

    # 打印飞书卡片（供手动发送参考）
    card = build_feishu_card(phase, results, orch.get_pools())  # 重读池状态（agents运行后最新）
    print("\n📱 飞书卡片内容预览:")
    print(json.dumps(card, ensure_ascii=False, indent=2))

    return results


if __name__ == "__main__":
    main()