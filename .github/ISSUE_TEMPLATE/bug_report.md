---
name: Bug 报告
about: 有东西不按预期工作
labels: bug
---

## 症状

<!-- 你做了什么、期望看到什么、实际看到什么 -->

## 复现步骤

```bash
# 完整命令，包括参数
```

## 环境

请附上下面三条的输出（**注意先去掉主机地址等敏感信息**；密码不会出现在里面，只显示 `<set>`）：

```bash
python install.py --check
python tests/test_finkg.py 2>&1 | tail -5
python $FG doctor
```

- 操作系统与 Shell：
- Python 版本：
- Agent 宿主（Claude Code / Cursor / Codex / OpenCode / …）：
- 安装方式（npx skills / skillhub / install.py / 手动）：
- Neo4j 版本与版别（企业版 / 社区版）：

## 相关文件

如果与某个会话有关，`reports/quality.json` 或 `reports/depth.json` 通常最有用。
**不要粘贴 `harvest/` 里的原始检索内容**，它可能包含大量数据或敏感信息。

## 检查过了吗

- [ ] 看过 [docs/TROUBLESHOOTING.md](../../docs/TROUBLESHOOTING.md)
- [ ] `python install.py --check` 通过
- [ ] `python tests/test_finkg.py` 通过
