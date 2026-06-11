"""PoolUpdater - 池管理操作独立模块"""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any


class PoolUpdater:
    """盘池更新操作：S级池写入、去重检查等"""

    def __init__(self, root: Path, pool_manager=None):
        self.root = root
        self.pool_manager = pool_manager
        # 惰性导入 QualityGate（打破循环导入：decision_agent→pool_updater→quality_gate→review_scorer）
        from agents.quality_gate import QualityGate
        self.quality_gate = QualityGate(root)

    def update_s_pool(self, decision_result: str, pool_manager=None, scored_stocks: Optional[list] = None):
        """从决策报告提取【主推】标的，写入S级操作池
        完整逻辑移植自 DecisionAgent._update_s_pool（含今日已修复的merge逻辑）
        """
        from agents.thresholds import S_POOL_MIN_SCORE

        pm = pool_manager or self.pool_manager
        if not pm:
            return
            
        pool_file = self.root / "五池管理" / "S级操作池.json"
        pool_file.parent.mkdir(parents=True, exist_ok=True)

        matches = re.findall(r"【主推】\s*([\u4e00-\u9fa5]{2,6})\s*[（(](\d{6})[）)]", decision_result)
        if not matches:
            return

        today = datetime.now().strftime("%Y-%m-%d")
        # 获取当前行情作为入场参考价
        current_prices = self._fetch_current_prices()

        new_stocks = []
        for name, code in matches[:2]:
            # 防线一：准入分数校验（含质检门）
            entry_score = None
            if scored_stocks:
                found = [s for s in scored_stocks if str(s.get("code", s.get("代码", ""))) == code]
                if found:
                    entry_score = int(found[0].get("score", 0))
                    if entry_score < S_POOL_MIN_SCORE:
                        print(f"[PoolUpdater] 🚫 {name}({code}) 评分 {entry_score} < {S_POOL_MIN_SCORE}, 拒绝入S级操作池")
                        continue
                else:
                    # P0: 未在审查评分列表中→硬拒绝, 防LLM自行生成推荐绕过准入分检查
                    print(f"[PoolUpdater] 🚫 {name}({code}) 未在审查评分列表中, 拒绝入S级操作池")
                    continue

            # ═══ A+B 重构：质检门（历史表现+市场状态+过热二次检测）═══
            market_state = self._get_market_state()
            gate_result = self.quality_gate.check(
                name=name, code=code, score=entry_score or S_POOL_MIN_SCORE,
                market_state=market_state,
                decision_result=decision_result,
                current_price=current_prices.get(code, 0),
            )
            if not gate_result["passed"]:
                print(f"[PoolUpdater] 🚫 {name}({code}) 质检门拒绝: {gate_result['reason']}")
                continue
            # 使用质检门调整后的评分
            adjusted_score = gate_result["adjusted_score"]

            # 防线二：价格位置检查
            entry_price = current_prices.get(code, 0)
            if entry_price <= 0:
                # 兜底：尝试单独获取该标的行情（不在现有池中的新推标的）
                try:
                    from market_agent import to_api, fetch_quotes
                    q = fetch_quotes([to_api(code)])
                    if q and len(q) > 0:
                        entry_price = q[0].get("现价", 0)
                except Exception:
                    pass
            position_warning = self._check_price_position(code, entry_price)
            if position_warning:
                print(f"[PoolUpdater] 🚫 {name}({code}) {position_warning}, 拒绝入S级操作池")
                continue

            # 防线三：新条目必带评分（使用质检门调整后评分）
            s = {
                "代码": code,
                "名称": name,
                "综合分": adjusted_score,  # 质检门调整后的动态评分
                "纳入日期": today,
                "驱动来源": "决策主推",
                "核心逻辑": self._extract_logic_snippet(name, decision_result),
                "入场价": current_prices.get(code, 0),
                "t1_验证": None,
                "t3_验证": None,
                "评价": None,
            }
            new_stocks.append(s)
            print(f"[PoolUpdater] ✅ {name}({code}) 质检通过 → S级操作池 (评分: {adjusted_score})")

        self._check_s_pool_overlap(new_stocks)

        # 读取现有池数据（保留历史记录 + 合并今日标的）
        old_data = self._safe_read_json(pool_file, {})
        old_history = old_data.get("历史记录", [])

        # ── 合并旧池中未过期的标的（今日新加入的或未满1天的）──
        old_stocks = old_data.get("stocks", [])
        today_dt = datetime.now()
        retained_stocks = []
        for s in old_stocks:
            entry_date_str = s.get("纳入日期", "")
            try:
                entry_date = datetime.strptime(entry_date_str, "%Y-%m-%d")
                age = (today_dt - entry_date).days
                if age == 0:
                    retained_stocks.append(s)
            except:
                pass

        # 按code去重合并
        existing_codes = {s.get("代码", "") for s in retained_stocks}
        new_deduped = [s for s in new_stocks if s.get("代码", "") not in existing_codes]
        merged = new_deduped + retained_stocks

        data = {
            "池名称": "S级操作池",
            "池定义": "当日决策主推标的，容量≤2，T+0可追，T+1需评估",
            "stocks": merged[:2],
            "统计": {"创建日期": today, "当日进入": len(new_stocks)},
            "历史记录": old_history,
        }

        existing_dates = {r.get("日期") for r in data["历史记录"]}
        if today not in existing_dates:
            data["历史记录"].append({
                "日期": today,
                "进入": len(new_stocks),
                "标的": [{"代码": s["代码"], "名称": s["名称"], "入场价": s["入场价"]} for s in new_stocks],
                "核心逻辑": {s["名称"]: s["核心逻辑"] for s in new_stocks},
            })

        self._safe_write_json(pool_file, data)
        print(f"[PoolUpdater] ✅ S级操作池更新: {len(new_stocks)} 只主推标的")

    def _check_price_position(self, code: str, current_price: float) -> str:
        """检查当前价格在52周中的位置，返回空字符串表示通过，非空表示警告"""
        try:
            from market_agent import to_api, fetch_history
            symbol = to_api(code)
            # 获取月K线，12根约覆盖1年
            history = fetch_history(symbol, "month", 12)
            if not history:
                return ""
            # 找到52周最高价
            high_52w = max(float(item.get("最高", 0)) for item in history)
            if high_52w <= 0:
                return ""
            ratio = current_price / high_52w
            if ratio > 0.85:
                return f"追高风险: 当前价{current_price}/52周最高{high_52w}={ratio:.0%}>85%"
            return ""
        except Exception as e:
            print(f"[PoolUpdater] ⚠️ 价格位置检查失败({code}): {e}")
            return ""

    def _get_market_state(self) -> dict:
        """获取当前市场状态（从 shared_memory.json 读取，兜底默认震荡）"""
        try:
            import json
            sm_file = self.root / "data" / "shared_memory.json"
            if sm_file.exists():
                data = json.loads(sm_file.read_text(encoding="utf-8"))
                if data and isinstance(data, list):
                    sh = next((s for s in data if s.get("代码") == "000001"), None)
                    if sh:
                        sh_chg = float(sh.get("涨跌幅", 0))
                        if sh_chg > 1:
                            return {"state": "偏多", "s_pool_cap": 2}
                        elif sh_chg > 0:
                            return {"state": "震荡偏强", "s_pool_cap": 2}
                        elif sh_chg > -1:
                            return {"state": "震荡偏弱", "s_pool_cap": 1}
                        else:
                            return {"state": "偏空", "s_pool_cap": 0}
        except Exception:
            pass
        return {"state": "震荡", "s_pool_cap": 2}

    def _check_s_pool_overlap(self, new_stocks: list):
        """检查S级主推标的是否已在其他流转池中，若在重点观察池则移除（晋级S级=移出重点池）"""
        check_pools = ["快筛候选池", "重点观察池", "边缘池"]
        for s in new_stocks:
            code = s.get("代码", "")
            name = s.get("名称", "")
            for pool_name in check_pools:
                pool_file = self.root / "五池管理" / f"{pool_name}.json"
                pool_data = self._safe_read_json(pool_file, {})
                pool_codes = {str(x.get("代码", "")) for x in pool_data.get("stocks", [])}
                if code in pool_codes:
                    if pool_name == "重点观察池":
                        # P1-2026-06-04: 晋级S级=移出重点池，防跨池重复
                        removed = self.pool_manager.remove_stock("重点观察池", code) if self.pool_manager else False
                        print(f"[PoolUpdater] ⬆️ {name}({code}) 已从{pool_name}移除（晋级S级操作池）{'✅' if removed else '⚠️未成功'}")
                    elif pool_name == "快筛候选池":
                        # P1-2026-06-04: 晋级S级也应从快筛候选池移除（该标的不应同时在候选池和S级池）
                        removed = self.pool_manager.remove_stock("快筛候选池", code) if self.pool_manager else False
                        print(f"[PoolUpdater] ⬆️ {name}({code}) 已从{pool_name}移除（晋级S级操作池）{'✅' if removed else '⚠️未成功'}")
                    else:
                        print(f"[PoolUpdater] ⚠️ {name}({code}) 同时存在于 {pool_name}（非活跃池，仅警告）")

    def _extract_logic_snippet(self, name: str, decision_result: str) -> str:
        """提取该股票决策报告中的核心逻辑"""
        pattern = rf"【主推】\s*{re.escape(name)}\s*[（(]\d{{6}}[）)].*?(?=\n### |\n---\n|$)"
        m = re.search(pattern, decision_result, re.DOTALL)
        if not m:
            return ""
        paragraph = m.group(0)
        for line in paragraph.split("\n"):
            if any(k in line for k in ["核心驱动", "逻辑支撑", "驱动"]):
                text = line.split("：", 1)[-1].split("——")[0].strip()
                if text:
                    return text[:60]
        return ""

    def _fetch_current_prices(self) -> dict:
        """获取各池股票当前行情，返回 {代码: 现价} 字典"""
        try:
            from market_agent import fetch_quotes, to_api
        except Exception:
            return {}

        pool_files = [
            self.root / "五池管理" / "重点观察池.json",
            self.root / "五池管理" / "快筛候选池.json",
        ]
        codes = []
        for pf in pool_files:
            if not pf.exists():
                continue
            data = self._safe_read_json(pf, {})
            for s in data.get("stocks", []):
                code = str(s.get("代码", s.get("股票代码", ""))).strip()
                if code:
                    codes.append(code)

        if not codes:
            return {}

        quotes = fetch_quotes([to_api(c) for c in codes])
        return {q["代码"]: q.get("现价", q.get("current", 0)) for q in quotes if q.get("代码")}

    def _safe_read_json(self, path: Path, default=None):
        if default is None:
            default = {}
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return default

    def _safe_write_json(self, path: Path, data: dict):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")