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
from agents.error_handling import check_circuit_breaker, record_success, record_failure


LLM_CALL_COUNT = 0  # 追踪本次运行的LLM调用次数


def run_phase(phase: str, pools: dict, wake_ctx: str = "") -> dict:
    """执行单个阶段"""
    global LLM_CALL_COUNT
    results = {}

    print(f"\n{'='*40}")
    print(f"🔔 阶段: {phase}")
    print(f"{'='*40}")

    if phase == "news_only":
        print("📰 执行新闻分析...")
        agent = NewsAgent()
        r = agent.run(wake_ctx=wake_ctx)
        LLM_CALL_COUNT += 1
        results["news"] = r
        ok = "✅" if r.get("success") else "❌"
        print(f"  {ok} 完成（LLM调用: {LLM_CALL_COUNT}次） | {r.get('error', r.get('source', ''))}")

    elif phase == "screen":
        print("🔍 执行快筛...")
        agent = ScreenAgent()
        r = agent.run(wake_ctx=wake_ctx)
        LLM_CALL_COUNT += 1
        results["screen"] = r
        print(f"  ✅ 完成（LLM调用: {LLM_CALL_COUNT}次）")

    elif phase == "review":
        print("🔎 执行审查...")
        agent = ReviewAgent()
        r = agent.run(wake_ctx=wake_ctx)
        LLM_CALL_COUNT += 1
        results["review"] = r
        print(f"  ✅ 完成（LLM调用: {LLM_CALL_COUNT}次）")

    elif phase == "skeptic":
        print("🎭 执行质疑者（SkepticAgent）...")
        skeptic = SkepticAgent()
        # 读取审查报告
        today = datetime.now().strftime("%Y-%m-%d")
        review_file = PROJECT_ROOT / "data" / "历史记录" / f"{today}_审查报告.md"
        review_report = review_file.read_text(encoding="utf-8") if review_file.exists() else ""

        # 读取重点观察池（二审制Gate：只质疑审查通过进入重点观察池的标的）
        pool_file = PROJECT_ROOT / "五池管理" / "重点观察池.json"
        if not (pool_file.exists() and json.loads(pool_file.read_text(encoding="utf-8")).get("stocks")):
            # ⚠️ 无升级标的时跳过 Skeptic，避免宪法冲突
            # 详见：review无标的升级到重点池→重点池为空→静默降级候选池→LLM宪法冲突拒绝审查
            print("  ⏭️ 重点观察池为空（今日无review升级标的），跳过Skeptic阶段")
            results["skeptic"] = {
                "success": True, "challenges": [], "high_risk_stocks": [],
                "high_risk_count": 0, "report": "",
                "skipped": True, "reason": "no_upgrades_to_key_watch_pool"
            }
            print(f"  ✅ 完成（跳过，无升级标的）")
            return results
        if pool_file.exists():
            data = json.loads(pool_file.read_text(encoding="utf-8"))
            stocks = data.get("stocks", [])
        else:
            stocks = []
        _ms = ReviewAgent()._get_market_state()
        r = skeptic.run(stock_list=stocks, review_report=review_report, market_context={"市场状态": _ms.get("state", "震荡"), "上证涨跌": f"{_ms.get('sh_chg',0):+.2f}%"})
        LLM_CALL_COUNT += 1
        results["skeptic"] = r
        # 写质疑结果供 DecisionAgent 注入（文件名与 DecisionAgent 读取一致）
        skeptic_file = PROJECT_ROOT / "data" / "历史记录" / f"{today}_质疑审查报告.md"
        # P0修复：写入完整质疑报告（含所有股票的详细质疑），而非简化版
        # 简化版只含 high_risk_summary 和 summary，LLM无法获取审查通过的股票详情
        skeptic_content = r.get('report', '')
        if not skeptic_content:
            # 降级：如果report为空，用简化格式但至少包含股票列表
            high_risk_count = r.get('high_risk_count', 0)
            high_risk_stocks = r.get('high_risk_stocks', [])
            challenges = r.get('challenges', [])
            
            lines = [
                f"# 【质疑审查报告】{today}\n",
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
        
        skeptic_file.write_text(skeptic_content, encoding="utf-8")
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
        today = datetime.now().strftime("%Y-%m-%d")
        ts_file = PROJECT_ROOT / "data" / "历史记录" / f"{today}_时间序列分析.md"
        ts_report = ts_file.read_text(encoding="utf-8") if ts_file.exists() else ""
        
        r = {"success": True, "report": ts_report, "saved_to": str(ts_file)}
        results["ts"] = r
        print(f"  ✅ 完成（0次LLM）| 报告: {ts_file.name}")

    return results


def build_feishu_card(phase: str, results: dict, pools: dict) -> dict:
    """构建飞书消息卡片"""
    today = datetime.now().strftime("%Y-%m-%d")

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"🦞 天枢权衡 | {today}"},
            "template": "blue"
        },
        "elements": [
            {
                "tag": "markdown",
                "content": f"**执行阶段**: {phase} | **LLM调用**: {LLM_CALL_COUNT}次"
            },
            {"tag": "hr"},
        ]
    }

    # 行情数据
    if "market" in results and results["market"].get("success"):
        analyzed = results["market"].get("analyzed", [])[:4]
        if analyzed:
            lines = ["### 📊 技术面（实时）"]
            for s in analyzed:
                score = s.get("技术面评分", 0)
                emoji = "🟢" if score >= 70 else "🟡" if score >= 50 else "🔴"
                lines.append(f"- **{s['名称']}({s['代码']})** {s['现价']}元 {s['涨跌幅']:+.2f}% {emoji}{score}分")
            card["elements"].append({"tag": "markdown", "content": "\n".join(lines)})
            card["elements"].append({"tag": "hr"})

    # 新闻分析结果
    if "news" in results and results["news"].get("success"):
        card["elements"].append({
            "tag": "markdown",
            "content": f"### 📰 新闻分析\n✅ 已完成，报告长度 {results['news']['news_length']} 字\n📄 {results['news']['saved_to'].split('/')[-1]}"
        })
        card["elements"].append({"tag": "hr"})

    # 快筛结果
    if "screen" in results and results["screen"].get("success"):
        report = results["screen"]["report"]
        # 截取前500字作为摘要
        summary = report[:400] + "..." if len(report) > 400 else report
        card["elements"].append({
            "tag": "markdown",
            "content": f"### 🔍 快筛结果\n```\n{summary}\n```"
        })
        card["elements"].append({"tag": "hr"})

    # 审查结果
    if "review" in results and results["review"].get("success"):
        card["elements"].append({
            "tag": "markdown",
            "content": f"### 🔎 审查结果\n✅ 审查完成\n📄 {results['review']['saved_to'].split('/')[-1]}"
        })
        card["elements"].append({"tag": "hr"})

    # 决策结果
    if "decision" in results and results["decision"].get("success"):
        card["elements"].append({
            "tag": "markdown",
            "content": f"### 💡 决策方案\n✅ 决策完成\n📄 {results['decision']['saved_to'].split('/')[-1]}"
        })

    # 五池状态
    card["elements"].append({"tag": "hr"})
    pool_lines = ["### 📊 五池状态"]
    for name, data in pools.items():
        stocks = data.get("stocks", [])
        count = len(stocks) if stocks else 0
        pool_lines.append(f"- **{name}**: {count}只")
    card["elements"].append({"tag": "markdown", "content": "\n".join(pool_lines)})

    # 底部
    card["elements"].append({
        "tag": "note",
        "elements": [
            {"tag": "plain_text", "content": f"生成时间: {datetime.now().strftime('%H:%M:%S')} | 🦞 天枢权衡 Multi-Agent"}
        ]
    })

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
        # ── 周末守卫：新闻分析照跑，跳过交易相关阶段 ──────
        now_weekday = datetime.now().weekday()
        is_weekend = now_weekday >= 5
        # ── 周末守卫结束 ─────────────────────────────────

        # ── 新闻分析（周末也执行，周末也有新闻） ────────────
        print(f"\n{'='*40}")
        print("📰 阶段: news")
        print(f"{'='*40}")
        def run_news():
            today = datetime.now().strftime("%Y-%m-%d")
            # P2-4：先检查本地新闻联播分析文件（06:20由另一cron生成）
            news_file = PROJECT_ROOT / "data" / "历史记录" / f"{today}_新闻联播投资分析.md"
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
            print("  执行：池健康检查（降级停留≥14天的陈旧标的）")
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

        # ── P3：边缘池清理（决策阶段后自动执行）────────────────────
        print(f"\n{'='*40}")
        print("🧹 执行边缘池清理...")
        print(f"{'='*40}")
        try:
            pm = PoolManager()
            clean_result = pm.clean_expired_edge_pool()
            removed_count = len(clean_result.get("removed", []))
            remaining = clean_result.get("remaining_count", 0)
            print(f"  ✅ 边缘池清理完成：移除{removed_count}只，剩余{remaining}只")
        except Exception as e:
            print(f"  ⚠️ 边缘池清理异常（不影响主流程）: {e}")
        # ── 边缘池清理结束 ──────────────────────────────────────
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