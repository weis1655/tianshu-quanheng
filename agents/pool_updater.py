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

    def update_s_pool(self, decision_result: str, pool_manager=None):
        """从决策报告提取【主推】标的，写入S级操作池
        完整逻辑移植自 DecisionAgent._update_s_pool（含今日已修复的merge逻辑）
        """
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
        for name, code in matches[:3]:
            s = {
                "代码": code,
                "名称": name,
                "纳入日期": today,
                "驱动来源": "决策主推",
                "核心逻辑": self._extract_logic_snippet(name, decision_result),
                "入场价": current_prices.get(code, 0),
                "t1_验证": None,
                "t3_验证": None,
                "评价": None,
            }
            new_stocks.append(s)

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
            "池定义": "当日决策主推标的，容量≤3，T+0可追，T+1需评估",
            "stocks": merged[:3],
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
                    else:
                        print(f"[PoolUpdater] ⚠️ {name}({code}) 同时存在于 {pool_name}（非重点池，仅警告）")

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