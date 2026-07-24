#!/usr/bin/env python3
"""Send early morning email report for 天枢权衡."""
import os, sys
from datetime import datetime

sys.path.insert(0, os.path.expanduser("~/.hermes/skills/email/scripts"))
from send_email import send_email, md_to_html

today = "2026-07-22"
base_dir = "data/历史记录"

report_names = ["技术面分析", "审查报告", "决策报告", "质疑审查报告"]
parts = []
for name in report_names:
    fpath = os.path.join(base_dir, f"{today}_{name}.md")
    if os.path.exists(fpath):
        content = open(fpath, "r", encoding="utf-8").read().strip()
        if content:
            parts.append(f"## {name}\n\n{content}")
    else:
        print(f"[WARN] {fpath} not found")

full_md = "\n\n---\n\n".join(parts) if parts else "无报告数据"

html_body = md_to_html(full_md, title=f"天枢权衡·早盘四段闭环 {today}", generated_at=today)
ok = send_email(
    subject=f"🏛️ 天枢权衡·早盘四段闭环 {today}",
    html_body=html_body,
    recipient="sjj139@139.com",
    skip_lock=True,
)
print(f"[{'OK' if ok else 'FAIL'}] Email sent to sjj139@139.com")
sys.exit(0 if ok else 1)