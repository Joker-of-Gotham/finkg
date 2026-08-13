# AGENTS.md

这是 **finkg** 仓库：一个 Agent Skill，把 LazySearch 检索到的金融信息全量落盘、深挖成可回溯原子事实，再建成一张有纵深的 Neo4j 中文金融图谱。

## 你想干什么

**想用这个 skill 建图** → 读 [`skills/finkg/SKILL.md`](skills/finkg/SKILL.md)，它是完整 playbook。
其余 reference 按需读，不要一次读完：

| 你正要做 | 先读 |
| --- | --- |
| 设计检索、写查询、决定问哪个数据源 | `skills/finkg/references/LAZYSEARCH.md` |
| 把收割拆成事实、决定节点该有哪些属性 | `skills/finkg/references/NODE_PROFILE.md` |
| 写关系、判断一条边够不够可分析 | `skills/finkg/references/EDGE_SEMANTICS.md` |
| 补纵深、判断多跳是真深还是灌水 | `skills/finkg/references/DEPTH.md` |
| 决定该不该问用户、怎么问、怎么记 | `skills/finkg/references/HITL.md` |
| 看懂质量报告、决定先修哪一项 | `skills/finkg/references/QUALITY.md` |
| 建库、装载、写 Cypher、可视化 | `skills/finkg/references/NEO4J.md` |

**想改这个仓库** → 继续往下读。

## 仓库结构

```
skills/finkg/        ← skill 本体（唯一真源，自包含，可整体复制到任何宿主）
install.py/.sh/.ps1  ← 跨宿主安装器
docs/                ← 面向人的文档（安装/快速开始/架构/配置/实战/排错）
examples/            ← 可复现样例数据
tests/               ← 离线单测，不依赖 Neo4j 或 LazySearch
.claude-plugin/      ← Claude Code 插件与市场清单
gemini-extension.json
```

`.agents/`、`.claude/` 等宿主目录是 `python install.py --here` 生成的**副本**，已 gitignore，不要在里面改代码。

## 改动纪律

- **`skills/finkg/` 是唯一真源。** 改完跑 `python install.py --here --force` 同步本仓库内的宿主镜像。
- 改 `skills/finkg/` 之后必须跑：

  ```bash
  python install.py --check     # SKILL.md 规范校验（name 与目录同名、description ≤500 单行）+ 脚本编译
  python tests/test_finkg.py    # 离线单测
  ```

- **`description` 必须 ≤500 字符且单行**，否则 Codex 会拒绝加载。改 SKILL.md frontmatter 时特别注意。
- **`name` 必须等于目录名 `finkg`。** 改名要同时改目录、`package.json`、`.claude-plugin/*`、`gemini-extension.json`、`install.py` 的 `SKILL_NAME`、以及全部文档引用。
- Python 只用标准库。引入第三方依赖会破坏「复制即可用」这个核心属性。
- 新增或修改 `scripts/` 的行为时，在 `tests/test_finkg.py` 里补一个会失败的反例测试；只改文档不算修复。
- 文档里的命令一律假定用户从**自己的工作区根目录**执行，并把 skill 目录下的 `scripts/fg.py` 记为 `$FG`。不要写死 `.claude/skills/...` 这类安装位置相关路径。

## 这个仓库不携带任何部署信息

**这是硬约束，`tests/test_finkg.py::TestNoDeploymentSpecifics` 会强制检查。**

不能出现在仓库里的：

- 主机名、内网 IP、带真实域名的服务地址
- 用户名、密码、token、任何凭据（哪怕是「示例值」——用 `<占位符>` 写法）
- 内部数据仓库的库名、表名（`dm_*.*`、`dwd_*.*` 这类）
- 内部制品库地址、内部工具的专有名称
- `financial_graph.local.json`（地址与凭据）
- `finkg.environment.md`（本机部署有哪些库表与工具）
- `financial-graph-sessions/`（会话产物，含大量原始检索数据）

后三项已 gitignore，但**提交前仍要 `git status` 扫一眼**。

部署信息的正确归属：

| 信息 | 放哪里 |
| --- | --- |
| LazySearch / Neo4j 地址、账号、密码 | 使用者工作区的 `financial_graph.local.json` 或 `FG_*` 环境变量 |
| 这套部署有哪些数据表、字段、检索工具 | 使用者工作区的 `finkg.environment.md`（`fg env --init` 生成） |
| 超时、会话目录这类与部署无关的默认值 | `skills/finkg/fg.defaults.json` |

`fgconfig.DEFAULTS` 里部署项一律留空，未配置时 `fgconfig.require()` 会抛出带配置步骤的错误。
不要为了「开箱即用」给它们填默认值。

## 外部服务

| 服务 | 端点 | 用法 |
| --- | --- | --- |
| LazySearch HTTP | 配置里的地址 + `/api/query` | `fg search`，能拿到 `history` 里的原始表，首选 |
| LazySearch MCP | 配置里的地址 + `/mcp` | 只回结论，用完必须 `fg harvest add` 补落盘 |
| Neo4j | 配置里的地址 | HTTP 事务 API，一会话一库 |

## 建图时不能违反的事

这几条同时是 skill 的核心约束和本仓库的设计立场：

- **先落盘再使用。** 没有 `harvest_id` + 能在该次收割原文里找到的 `quote` 的事实等于不存在。不要凭记忆改写数字或原文。
- **信息要用尽。** LazySearch 的 `final_answer` 常只占返回信息量的 4%，原始表在 `history` 里。每个数据单元格要么变成事实，要么 `fg harvest dispose` 写明理由；静默丢弃不算交代。
- **公司 ≠ 股票 ≠ 上市记录。** 三个节点，用发行/上市关系连接。
- **属性用中文点分层级**（`财务.利润表.2025年报.营业总收入`），第一段是维度组；不造空属性——丰富指的是已知且有证据的字段数。
- **关系是 2–8 字中文短语 + 一句话机制 + 量化属性。** 对象/品类写进 attrs。写成句子或「相关/影响/关联」都不可分析。
- **纵深服务于机制问题，靠扇区逐步发掘。** 只有业务实体之间的边算跳；不要设节点/边/跳数门槛，也不要为跳数接龙。
- **草稿只写进会话 `drafts/`，不要堆在工作区根目录。**
- **人在回路用结构化提问在对话里问**（摆当前判断、证据样本、2~3 个真实取舍、建议、为何现在必须定），答复用 `fg align` / `fg answer` 记进明文台账。
- **没有 Gate、签名、令牌、哈希链、密钥。** `fg quality` 的 `ok=false` 只表示还有引文对不上或边名写成了句子；规模/跳数不拦住 `fg neo4j load`。
- **质量只判内容。** 不检查 key 齐不齐、不算工件哈希、不把节点数量当目标。
- **一会话一 Neo4j 库。** 目标实例上可能有很多其他项目的库，动手前确认 `--database`。
- 不自主下单、开户、授信、估值签字或对外发布结论。

## Windows 注意

- 先 `$env:PYTHONIOENCODING="utf-8"`。
- 要把命令输出落盘**不要用 `>`**：PowerShell 会按控制台宽度折断长字符串，`Out-File` 也一样。用 `--out-file <文件>` 让工具自己写；要喂给工具的 JSON 直接用文件写入工具生成。
- 未开启开发者模式时无法建符号链接，`install.py` 会自动回退为复制。
