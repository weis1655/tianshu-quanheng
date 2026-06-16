# PR Record: auto-review/mempalace-trigger-fixes

此记录用于在变更已直接合并至 `main` 的情况下保留可审计的 PR 描述与变更摘要。

## 标题
chore(auto-review): safe MemPalace ops + replace curl with requests

## 概要
本次变更由自动化代码审查触发，包含安全性与可维护性修复：

- 将 `tianshu_memory.py` 中的不安全 `python -c` 字符串执行替换为基于 JSON-over-stdin 的安全子进程接口 (`_kg_run_ops`、`_kg_query_relationship`)；并将调用点改为使用新的接口以避免代码/命令注入风险。
- 将 `agents/trigger.py` 中使用 `curl` 的 `subprocess.run(..., shell=True)` 替换为 `requests.get`，加上状态码检查与 GBK 解码处理，移除 shell 注入隐患。
- 生成并提交审查报告：`.review-reports/report-2026-06-16_00-56-09.md`。

这些修改旨在降低注入攻击面、提高错误容忍性以及减少第三方依赖扫描噪声。变更为最小侵入式修复，已通过语法检查（`python -m py_compile`）。

## 相关提交
- 55a6774 — chore(auto-review): apply safe auto-fixes [skip ci] (Replace curl with requests)
- 52befd5 — chore(auto-review): apply safe auto-fixes [skip ci] (MemPalace JSON ops)
- 8d72961 — chore(auto-review): apply safe auto-fixes [skip ci] (Add review report)

（可通过 `git show <commit>` 查看详述）

## 变更文件
- `tianshu_memory.py` — 新增 `_kg_run_ops`, `_kg_query_relationship`，并替换原有不安全调用点
- `agents/trigger.py` — 用 `requests.get` 替换 `curl` 子进程调用
- `.review-reports/report-2026-06-16_00-56-09.md` — 自动审查报告

## 后续建议
1. 在 CI 中加入 `black/flake8/mypy/bandit`，尽早捕获风控与格式问题。
2. 对 `tianshu_memory` 的 MemPalace 集成做集成测试（包含对非 ASCII、包含引号/换行的字段写入情形）。
3. 考虑将 MemPalace 的集成进一步替换为本地 import（若运行环境允许）以避免跨进程复杂性。

## 记录人
自动化审查系统 & 操作人：`weis1655`（commit author）
