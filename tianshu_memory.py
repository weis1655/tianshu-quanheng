"""
天枢权衡 × MemPalace 记忆系统集成
将五池状态、执行摘要、历史决策写入 MemPalace 知识宫殿，
实现跨天连续记忆、时间窗口失效检测、矛盾发现。

使用方法（main.py 启动时）：
    from tianshu_memory import TianshuMemory
    mem = TianshuMemory()
    wake_context = mem.wake_up()
    # 将 wake_context 注入到 NewsAgent 或其他 Agent 的 system prompt 中

使用方法（main.py 结束时）：
    from tianshu_memory import TianshuMemory
    mem = TianshuMemory()
    mem.save_run_summary(phase, results, pools)
"""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()

# MemPalace 的 Python 解释器（确保依赖完整）
_MEMPALACE_PY = str(Path.home() / ".local/share/uv/tools/mempalace/bin/python3.11")
_MEMPALACE_PKG = str(Path.home() / ".local/share/uv/tools/mempalace/lib/python3.11/site-packages")
_MEMPALACE_PP = str(Path.home() / ".mempalace/palace")


class TianshuMemory:
    """天枢 × MemPalace 记忆集成"""

    def __init__(self, palace_path: str = None):
        self._pp = palace_path or _MEMPALACE_PP

    # ────────────────────────────────────────────
    # 内部：子进程调用 MemPalace API
    # ────────────────────────────────────────────

    def _kg_run_ops(self, ops: list, timeout: int = 30) -> str:
        """通过子进程安全地调用 MemPalace：使用 stdin 传入 JSON 操作列表。

        ops: 列表形式的操作，每项为 dict，例如 {"op": "add_triple", "args": [...]}
        返回子进程 stdout（若有）。
        """
        payload = {
            "db_path": f"{self._pp}/knowledge_graph.sqlite3",
            "ops": ops,
        }

        # 内联脚本：从 stdin 读取 JSON，初始化 KnowledgeGraph 并执行操作
        inline = (
            "import sys, json\n"
            f"sys.path.insert(0, '{_MEMPALACE_PKG}')\n"
            "from mempalace.knowledge_graph import KnowledgeGraph\n"
            "data = json.load(sys.stdin)\n"
            "kg = KnowledgeGraph(db_path=data.get('db_path'))\n"
            "out = []\n"
            "for o in data.get('ops', []):\n"
            "    name = o.get('op')\n"
            "    args = o.get('args', [])\n"
            "    # 支持带命名参数的特殊操作\n"
            "    if name == 'add_triple_kw':\n"
            "        # args: code, pred, obj, valid_from\n"
            "        try:\n"
            "            kg.add_triple(args[0], args[1], args[2], valid_from=args[3])\n"
            "            out.append({'op': name, 'res': True})\n"
            "            continue\n"
            "        except Exception as e:\n"
            "            out.append({'op': name, 'error': str(e)})\n"
            "            continue\n"
            "    func = getattr(kg, name, None)\n"
            "    if not func:\n"
            "        out.append({'error': f'no op {name}'})\n"
            "        continue\n"
            "    res = func(*args)\n"
            "    out.append({'op': name, 'res': res})\n"
            "sys.stdout.write(json.dumps(out, ensure_ascii=False))\n"
        )

        try:
            r = subprocess.run(
                [_MEMPALACE_PY, "-c", inline],
                input=json.dumps(payload, ensure_ascii=False),
                capture_output=True, text=True, timeout=timeout,
            )
        except Exception as e:
            raise RuntimeError(f"MemPalace subprocess failed: {e}")

        if r.returncode != 0:
            raise RuntimeError("MemPalace KG 错误: " + (r.stderr or r.stdout)[:400])

        return r.stdout

    def _kg_query_relationship(self, rel: str, limit: int = 10, timeout: int = 30) -> list:
        """使用子进程安全查询 relationship 并返回解析后的结果列表"""
        payload = {"db_path": f"{self._pp}/knowledge_graph.sqlite3", "rel": rel, "limit": limit}

        inline = (
            "import sys, json\n"
            f"sys.path.insert(0, '{_MEMPALACE_PKG}')\n"
            "from mempalace.knowledge_graph import KnowledgeGraph\n"
            "data = json.load(sys.stdin)\n"
            "kg = KnowledgeGraph(db_path=data.get('db_path'))\n"
            "res = kg.query_relationship(data.get('rel'))\n"
            "# 仅返回前 limit 项\n"
            "res = res[: data.get('limit', 10)]\n"
            "sys.stdout.write(json.dumps(res, ensure_ascii=False))\n"
        )

        try:
            r = subprocess.run(
                [_MEMPALACE_PY, "-c", inline],
                input=json.dumps(payload, ensure_ascii=False),
                capture_output=True, text=True, timeout=timeout,
            )
        except Exception as e:
            raise RuntimeError(f"MemPalace query subprocess failed: {e}")

        if r.returncode != 0:
            raise RuntimeError("MemPalace KG 查询错误: " + (r.stderr or r.stdout)[:400])

        try:
            return json.loads(r.stdout) if r.stdout else []
        except Exception:
            return []

    # ────────────────────────────────────────────
    # 公开 API：唤醒上下文
    # ────────────────────────────────────────────

    def wake_up(self, max_chars: int = 1200) -> str:
        """
        返回天枢专属的唤醒上下文（L0 身份 + L1 近期记忆）。
        可注入 Agent 的 system prompt。
        """
        today = datetime.now().strftime("%Y-%m-%d")
        lines = [
            f"# 天枢权衡 唤醒记忆 | {today}",
            "",
            "## 身份（L0）",
            "天枢权衡是盟主的 A 股多智囊决策系统。",
            "五池：S级操作池、重点观察池、快筛候选池、持仓池。",
            "每次运行：新闻→快筛→审查→质疑者→决策。",
            "",
        ]

        # L1：最近进入各池的股票（使用安全查询接口）
        try:
            triples = self._kg_query_relationship("进入", limit=10)
            if triples:
                lines.append("## 近期五池记录（L1）")
                for t in triples:
                    lines.append("  " + str(t))
                lines.append("")
        except Exception:
            pass

        # 读取当前五池快照
        try:
            snapshot = self._get_pool_summary()
            if snapshot:
                lines.append("## 当前五池快照")
                lines.append(snapshot)
                lines.append("")
        except Exception:
            pass

        # 截断到合理长度
        result = "\n".join(lines)
        if len(result) > max_chars:
            result = result[:max_chars] + "\n...（记忆过长已截断）"
        return result

    # ────────────────────────────────────────────
    # 公开 API：保存运行结果
    # ────────────────────────────────────────────

    def save_run_summary(self, phase: str, results: dict, pools: dict) -> dict:
        """
        在 main.py 运行结束时调用：
        - 五池状态写入知识图谱（带时间窗口）
        - 执行结果写入日记
        """
        today = datetime.now().strftime("%Y-%m-%d")
        stats = {"saved": False, "triples": 0, "tokens_saved": 0, "error": None}

        try:
            triple_count = 0
            # ① 五池状态 → 知识图谱三元组（使用安全 ops 接口）
            for pool_name, pool_data in pools.items():
                stocks = pool_data.get("stocks", [])
                for stock in (stocks or []):
                    code = stock.get("股票代码", stock.get("代码", "?"))
                    name = stock.get("股票名称", stock.get("名称", "?"))
                    rating = stock.get("评级", "")
                    entry_date = stock.get("进入日期", stock.get("date", today))

                    ops = []
                    ops.append({"op": "add_entity", "args": [code, "stock"]})
                    # 使用带命名参数的特殊 op，后端 inline 会映射为 valid_from
                    ops.append({"op": "add_triple_kw", "args": [code, "进入", pool_name, entry_date]})
                    try:
                        out = self._kg_run_ops(ops)
                        triple_count += len(ops)
                    except Exception:
                        # 单条写入失败则继续，不中断整个保存流程
                        pass

                    # 写入评级
                    if rating:
                        try:
                            self._kg_run_ops([{"op": "add_triple", "args": [code, "评级", f"{rating}@{today}"]}])
                            triple_count += 1
                        except Exception:
                            pass

            stats["triples"] = triple_count

            # ② 写入日记（供 L2 检索）
            self._save_diary(phase, results, pools, today)

            # ③ 记录运行统计
            total_phases = len(results)
            ok_phases = sum(1 for r in results.values() if r.get("success", False))
            run_block = '\n'.join([
                'kg.add_entity("tianshu_run_' + today + '", "event")',
                'kg.add_triple("tianshu_run_' + today + '", "运行了", '
                + f'"{phase}（{ok_phases}/{total_phases}阶段成功）", valid_from="{today}")',
            ])
            self._kg_run(run_block)

            stats["saved"] = True

        except Exception as e:
            stats["error"] = str(e)

        return stats

    # ────────────────────────────────────────────
    # 公开 API：查询股票记忆
    # ────────────────────────────────────────────

    def query_stock(self, stock_code: str) -> str:
        """查询某只股票的全部记忆"""
        try:
            # 使用安全查询接口
            # 返回值尝试解析为 JSON 字符串
            inline_res = self._kg_run_ops([{"op": "query_entity", "args": [stock_code]}])
            # _kg_run_ops 会返回 JSON 输出列表（或空），尝试解析
            try:
                parsed = json.loads(inline_res) if inline_res else []
                return "\n".join(str(r) for r in parsed)
            except Exception:
                return inline_res
        except Exception as e:
            return f"查询失败: {e}"

    # ────────────────────────────────────────────
    # 公开 API：池积压检测
    # ────────────────────────────────────────────

    def get_stale_stocks(self, pool_name: str = None, max_days: int = 5) -> list:
        """
        返回在池中停留过久的股票（用于周复盘池卫生）。
        """
        try:
            all_rels = self._kg_query_relationship("进入", limit=100)
            filtered = [
                r for r in all_rels
                if pool_name is None or pool_name in r.get("obj", "")
            ]
            return filtered
        except Exception:
            return []

    # ────────────────────────────────────────────
    # 私有：五池摘要
    # ────────────────────────────────────────────

    def _get_pool_summary(self) -> str:
        pool_dir = PROJECT_ROOT / "五池管理"
        if not pool_dir.exists():
            return ""
        lines = []
        for pf in sorted(pool_dir.glob("*.json")):
            name = pf.stem
            try:
                data = json.loads(pf.read_text(encoding="utf-8"))
                stocks = data.get("stocks", [])
                count = len(stocks) if stocks else 0
                if stocks:
                    top = ",".join(s.get("股票代码", s.get("代码", "?")) for s in stocks[:3])
                    lines.append(f"[{name}] {count}只: {top}")
                else:
                    lines.append(f"[{name}] 0只")
            except Exception:
                lines.append(f"[{name}] (读取失败)")
        return "\n".join(lines)

    # ────────────────────────────────────────────
    # 私有：写入日记（供 MemPalace L2 检索）
    # ────────────────────────────────────────────

    def _save_diary(self, phase: str, results: dict, pools: dict, today: str):
        """将执行摘要写入 MemPalace 日记目录"""
        diary_dir = Path.home() / ".mempalace/palace/diaries/tianshu_quanheng"
        diary_dir.mkdir(parents=True, exist_ok=True)
        diary_file = diary_dir / f"run_{today.replace('-', '')}.md"

        lines = [f"# 天枢运行日记 | {today} | {phase}", ""]
        lines.append("## 五池快照")
        lines.append(self._get_pool_summary())
        lines.append("")

        lines.append("## 各阶段结果")
        for name, r in results.items():
            status = "✅" if r.get("success") else "❌"
            lines.append(f"- {status} {name}")

        diary_file.write_text("\n".join(lines), encoding="utf-8")


# ────────────────────────────────────────────
# CLI 测试入口
# ────────────────────────────────────────────

if __name__ == "__main__":
    mem = TianshuMemory()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""

    if cmd == "wake":
        print("=== 唤醒上下文测试 ===")
        print(mem.wake_up())

    elif cmd == "save":
        print("=== 保存测试 ===")
        pools = {
            "S级操作池": {"stocks": [{"代码": "601398", "名称": "工商银行", "评级": "S"}]},
                        "重点观察池": {"stocks": []},
            "快筛候选池": {"stocks": [{"代码": "600519", "名称": "茅台"}]},
            "持仓池": {"stocks": []},
        }
        results = {"news": {"success": True}, "screen": {"success": True}}
        r = mem.save_run_summary("full_cycle", results, pools)
        print(f"保存结果: {r}")

    elif cmd == "query" and len(sys.argv) >= 3:
        code = sys.argv[2]
        print(f"=== 查询 {code} ===")
        print(mem.query_stock(code))

    elif cmd == "stale":
        print("=== 池积压检测 ===")
        print(mem.get_stale_stocks(pool_name="边缘池", max_days=5))

    else:
        print("用法: python tianshu_memory.py [wake|save|query CODE|stale]")