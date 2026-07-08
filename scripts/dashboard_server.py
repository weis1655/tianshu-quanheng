#!/usr/bin/env python3
"""
天枢权衡 Dashboard — 健康看板数据API
读取五池状态+决策统计，提供JSON API供前端展示
"""
import sys
import json
from pathlib import Path
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT / "agents"))

HOST = "0.0.0.0"
PORT = 8899


def collect_health_data() -> dict:
    """采集所有健康指标"""
    data = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pools": {},
        "stats": {},
        "issues": [],
        "recent_decisions": [],
    }

    # 采集五池状态
    pool_dir = PROJECT_ROOT / "五池管理"
    for pool_file in sorted(pool_dir.glob("*.json")):
        pool_name = pool_file.stem
        try:
            pool_data = json.loads(pool_file.read_text(encoding="utf-8"))
            stocks = pool_data.get("stocks", [])
            score_summary = {"avg": 0, "min": 0, "max": 0}
            scores = []
            for s in stocks:
                sc = s.get("综合分", s.get("综合评分", None))
                if sc is not None:
                    try:
                        scores.append(float(sc))
                    except (ValueError, TypeError):
                        pass
            if scores:
                score_summary = {"avg": round(sum(scores)/len(scores), 1), "min": min(scores), "max": max(scores)}

            data["pools"][pool_name] = {
                "count": len(stocks),
                "score_summary": score_summary,
                "stocks": [
                    {
                        "name": s.get("名称", "?"),
                        "code": s.get("代码", ""),
                        "score": s.get("综合分", s.get("综合评分", "-")),
                        "chg": s.get("今日涨跌", ""),
                    }
                    for s in stocks[:10]  # 前10只
                ],
            }
        except Exception as e:
            data["pools"][pool_name] = {"count": 0, "error": str(e)}

    # 决策统计
    log_path = PROJECT_ROOT / "data" / "复盘记录" / "决策日志.json"
    if log_path.exists():
        try:
            log = json.loads(log_path.read_text(encoding="utf-8"))
            stats = log.get("统计", {})
            data["stats"] = {
                "total_decisions": stats.get("总决策数", 0),
                "wins": stats.get("盈利数", 0),
                "win_rate": stats.get("胜率", 0),
                "last_updated": log.get("决策记录", [{}])[-1].get("日期", "") if log.get("决策记录") else "",
            }
        except Exception:
            pass

    # 最新回头看报告中的P0数量
    review_dir = PROJECT_ROOT / "data" / "回顾报告"
    reports = sorted(review_dir.glob("*_回头看报告_v3*.md"))
    if reports:
        text = reports[-1].read_text(encoding="utf-8")
        import re
        p0_count = len(re.findall(r"### 🔴 P0-", text))
        p0_losses = len(re.findall(r"P0-实盘亏损", text))
        p0_downgrade = len(re.findall(r"P0-降级延迟", text))
        p0_skeptic = len(re.findall(r"P0-质疑报告缺失", text))
        data["issues"] = [
            {"type": "实盘亏损", "count": p0_losses, "level": "critical"},
            {"type": "降级延迟", "count": p0_downgrade, "level": "warning"},
            {"type": "质疑缺失", "count": p0_skeptic, "level": "info"},
        ]

    return data


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/health":
            self._json_response(collect_health_data())
        elif path == "/":
            self._html_response(self._render_dashboard())
        else:
            self.send_response(404)
            self.end_headers()

    def _json_response(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))

    def _html_response(self, html):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _render_dashboard(self) -> str:
        return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>天枢权衡 · 健康看板</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: -apple-system, 'Segoe UI', sans-serif; background:#0f172a; color:#e2e8f0; padding:20px; }
h1 { font-size:24px; margin-bottom:20px; color:#38bdf8; }
.subtitle { color:#94a3b8; font-size:14px; margin-bottom:24px; }
.pools { display:grid; grid-template-columns:repeat(auto-fit, minmax(280px,1fr)); gap:16px; margin-bottom:24px; }
.pool-card { background:#1e293b; border-radius:12px; padding:16px; border:1px solid #334155; }
.pool-card h3 { font-size:16px; margin-bottom:8px; }
.pool-card .count { font-size:32px; font-weight:700; color:#38bdf8; }
.pool-card .score { font-size:13px; color:#94a3b8; margin:8px 0; }
.pool-card table { width:100%; font-size:12px; border-collapse:collapse; }
.pool-card td { padding:4px 0; border-bottom:1px solid #1e293b; }
.chg-positive { color:#22c55e; }
.chg-negative { color:#ef4444; }
.stats-row { display:flex; gap:16px; margin-bottom:24px; }
.stat-card { background:#1e293b; border-radius:12px; padding:16px; flex:1; text-align:center; border:1px solid #334155; }
.stat-card .value { font-size:28px; font-weight:700; }
.stat-card .label { font-size:12px; color:#94a3b8; margin-top:4px; }
.issues { background:#1e293b; border-radius:12px; padding:16px; border:1px solid #334155; }
.issues h3 { margin-bottom:8px; }
.issue-item { display:flex; justify-content:space-between; padding:8px 0; border-bottom:1px solid #334155; font-size:14px; }
.issue-critical { color:#ef4444; }
.issue-warning { color:#f59e0b; }
.issue-info { color:#38bdf8; }
.server-status { margin-top:24px; padding:12px; background:#1e293b; border-radius:8px; font-size:12px; color:#94a3b8; text-align:center; }
</style>
</head>
<body>
<h1>🏛️ 天枢权衡 · 健康看板</h1>
<div class="subtitle" id="timestamp">加载中...</div>

<div class="stats-row" id="stats"></div>
<div class="pools" id="pools"></div>
<div class="issues" id="issues"></div>
<div class="server-status">数据每30秒自动刷新 · <a href="/api/health" style="color:#38bdf8;">查看JSON数据</a></div>

<script>
function loadData() {
    fetch('/api/health')
        .then(r => r.json())
        .then(d => {
            document.getElementById('timestamp').textContent = '更新于 ' + d.timestamp;

            // 统计卡片
            const s = d.stats || {};
            document.getElementById('stats').innerHTML = `
                <div class="stat-card"><div class="value" style="color:#38bdf8">${s.total_decisions || '-'}</div><div class="label">总决策</div></div>
                <div class="stat-card"><div class="value" style="color:${(s.win_rate||0) >= 50 ? '#22c55e' : '#ef4444'}">${s.win_rate || '-'}%</div><div class="label">胜率</div></div>
                <div class="stat-card"><div class="value" style="color:#22c55e">${s.wins || 0}</div><div class="label">盈利</div></div>
                <div class="stat-card"><div class="value" style="color:#94a3b8">${s.last_updated || '-'}</div><div class="label">最近决策</div></div>
            `;

            // 五池卡片
            let poolsHtml = '';
            for (const [name, pool] of Object.entries(d.pools)) {
                const avg = pool.score_summary?.avg || 0;
                const stocksHtml = pool.stocks?.map(s =>
                    `<tr><td>${s.name}</td><td>${s.code}</td><td>${s.score}</td><td class="${s.chg?.startsWith('-') ? 'chg-negative' : s.chg ? 'chg-positive' : ''}">${s.chg || '-'}</td></tr>`
                ).join('') || '<tr><td colspan="4">空</td></tr>';
                poolsHtml += `<div class="pool-card">
                    <h3>${name}</h3>
                    <div class="count">${pool.count}</div>
                    <div class="score">均分${avg} · 范围${pool.score_summary?.min || 0}-${pool.score_summary?.max || 0}</div>
                    <table><thead><tr><th>名称</th><th>代码</th><th>评分</th><th>涨跌</th></tr></thead><tbody>${stocksHtml}</tbody></table>
                </div>`;
            }
            document.getElementById('pools').innerHTML = poolsHtml;

            // 问题列表
            let issuesHtml = '<h3>⚠️ 回头看问题</h3>';
            if (d.issues?.length) {
                d.issues.forEach(i => {
                    const cls = i.level === 'critical' ? 'issue-critical' : i.level === 'warning' ? 'issue-warning' : 'issue-info';
                    issuesHtml += `<div class="issue-item"><span class="${cls}">${i.type}</span><span>${i.count} 个</span></div>`;
                });
            }
            document.getElementById('issues').innerHTML = issuesHtml;
        })
        .catch(e => {
            document.getElementById('timestamp').textContent = '❌ 加载失败: ' + e.message;
        });
}
loadData();
setInterval(loadData, 30000);
</script>
</body>
</html>"""
    def log_message(self, format, *args):
        pass  # 静默


if __name__ == "__main__":
    server = HTTPServer((HOST, PORT), DashboardHandler)
    print(f"🏛️ 天枢权衡 Dashboard → http://{HOST}:{PORT}")
    print(f"   JSON API → http://{HOST}:{PORT}/api/health")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard 已停止")
        server.server_close()