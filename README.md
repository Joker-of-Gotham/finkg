<div align="center">

# finkg

**把检索到的金融信息一格不浪费地变成一张能被追问到底的 Neo4j 图**

一个 [Agent Skill](https://agent-config.com/guides/agent-skills-standard/)，可在 Claude Code、Cursor、Codex、OpenCode、Gemini CLI、Copilot 等宿主中直接使用

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Agent Skills](https://img.shields.io/badge/Agent%20Skills-SKILL.md-8A2BE2)](skills/finkg/SKILL.md)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB)](#系统要求)
[![Dependencies](https://img.shields.io/badge/dependencies-zero-success)](#系统要求)
[![Neo4j](https://img.shields.io/badge/Neo4j-5.x-018BFF)](docs/CONFIGURATION.md)

[快速开始](#快速开始) · [安装](#安装) · [它解决什么问题](#它解决什么问题) · [文档](#文档) · [English](#english)

</div>

---

## 它解决什么问题

用 AI 建金融知识图谱，最常见的失败不是「建不出来」，而是**建出一张看起来很大、实际没法用的图**：节点只有名字和一两个数字，边写着"相关""影响"，号称十跳的路径其实是同一种关系接龙，而检索回来的大量数据在模型上下文里过了一遍就消失了。

finkg 针对的正是这四件事。

### 1. 检索回来的信息，绝大部分被扔掉了

这不是猜测。对 LazySearch 的一次实测：问「宁德时代2025年营业收入和净利润」——

| 层 | 字符数 | 内容 |
| --- | --- | --- |
| `final_answer`（模型通常只看这个） | 474 | 几个关键数字的结论 |
| `history` 里的原始工具返回 | **11,814** | 证券实体候选表、利润表 **83 列**原始 CSV、成长能力指标表、执行过的完整 SQL、源库表名 |

**结论只占返回信息量的 4%。** finkg 把每次检索**完整落盘**并切成可分层取用的四层（结论 / 原始数据 / 查询计划 / 服务端提示），然后逐单元格核账：

```console
$ fg usage
{
  "total_data_cells": 684,        # 一次利润表检索识别出的数据单元格
  "used": 142,                    # 真正进了图的
  "disposed": 0,                  # 写明了"为什么不用"的
  "open": 542,                    # 既没用也没交代的 ← 这就是漏掉的信息
  "use_ratio": 0.2076
}
```

`open_samples` 会直接指出漏了什么：「营业总成本 3,448.24 亿 / 344,824,196,000.00」。**要么用，要么写明为什么不用，静默丢弃不算交代。**

### 2. 节点应该是详表，不是标签

节点属性用中文点分层级，第一段自动成为维度组，所以「只有财务、没有所有权和风险」一眼可见：

```
财务.利润表.2025年报.营业总收入      423701834000.0  元 CNY  ← F00001 ← h-0001 ← 原文引文
财务.每股指标.2025年报.基本每股收益   16.14           元/股
所有权.第一大股东.持股比例            24.68           %      报告期 2025-12-31
风险.供应链.关键原料集中度            ...
```

每个属性都带单位、币种、期间、合并口径、来源事实 ID、以及**能在落盘原文里逐字找到的引文**——工具会核验这一点，凭记忆改写数字会被直接拒绝。

### 3. 边要能继续分析，不只是"有关系"

关系类型在 Neo4j 里是 **2–8 字中文短语**（词或动宾），对象、品类、量化写在边属性上：

```cypher
(:Company {caption:"赣锋锂业"})
  -[:`长协供应` {
      机制: "长协锁量、价格随基准指数联动，锂价波动经采购成本进入宁德时代营业成本",
      语义层: "supply_operation", 品类: "电池级碳酸锂",
      合同类型: "长期协议", 计价方式: "指数联动", 锁量: "不低于8万吨/年"}]->
(:Company {caption:"宁德时代"})
```

写「相关」「影响」或写成一整句（「向客户长协供应电池级碳酸锂」），图例都读不动。写不出机制，说明这一跳还没研究清楚。

### 4. 纵深是机制，不是跳数门槛

只有业务实体之间的边算跳（`Document`/`Observation`/`Metric` 不算）。一条长路径要算真纵深，必须同时满足：跨层、关系多样、每跳有证据与机制、连续推断不超过 2 跳。**不设「必须 N 跳 / N 条边 / N 个节点」的装库门槛**——规模靠按扇区继续检索长出来，`fg brief` 会给出下一条该问的问题。

装库后可以直接在 Neo4j 里验证，逐跳返回中文关系、语义层和机制：

```console
$ fg neo4j hop --from-id E-policy --to-id E-szse --min-hops 5
能量密度准入政策 ─准入约束→ 动力电池产业   [政策与监管]
动力电池产业     ─拉动需求→ 电池级碳酸锂   [需求与终端市场]
电池级碳酸锂     ─主要产出→ 赣锋锂业       [供给与运营]
赣锋锂业         ─长协供应→ 宁德时代       [供给与运营]
宁德时代         ─贡献收入→ 动力电池分部   [财务与资本]
动力电池分部     ─整车供应→ 特斯拉         [需求与终端市场]
特斯拉           ─预期传导→ 宁德时代A股    [预期与估值]
宁德时代A股      ─挂牌────→ 深圳证券交易所 [法律与所有权]
                                              跳数 8 · 跨 6 层
```

### 还有两件明确不做的事

**不做仪式。** 没有 Gate、没有阶段门、没有签名、没有 HMAC、没有一次性令牌、没有工件哈希链、没有密钥环境变量。人在回路的唯一机制是：agent 在对话里用结构化提问摆出**当前判断 + 证据样本 + 2~3 个真实取舍 + 建议 + 为什么现在必须定**，你答完记进一份明文台账。台账随时可读可改，它记录的是「我们商量过什么」，不是「谁被授权了」。

**质量只判内容，不设规模门槛。** 不检查 JSON 有没有某个 key、不算工件哈希、不要求「必须有 XXX 节点 / XXX 边 / XXX 跳」。判的是：这个数字有没有单位币种期间口径、这条引文能不能在原文里找到、关系名是不是短短语、这条边说不说得清机制、这条路径是不是真跨了机制层、检索回来的数据有多少真的进了图。规模靠 `fg brief` 指出的下一个扇区继续发掘。

---

## 安装

需要 Python 3.10+（零第三方依赖）。四条路都可以，选一条。

### 1) Skills CLI（推荐，17+ 宿主自动适配）

```bash
npx skills add Joker-of-Gotham/finkg          # 装到当前项目
npx skills add Joker-of-Gotham/finkg -g       # 装到用户目录，所有项目可用
npx skills add Joker-of-Gotham/finkg --list   # 先看看里面有什么
```

### 2) SkillHub CLI

```bash
npx @skillhub/cli install Joker-of-Gotham/finkg/finkg
npx @skillhub/cli install Joker-of-Gotham/finkg/finkg --platform codex
```

### 3) git clone + 自带安装器（控制最细）

```bash
git clone https://github.com/Joker-of-Gotham/finkg.git && cd finkg
python install.py                 # 交互：检测本机 agent，选择装哪些
python install.py --all           # 装到所有检测到的 agent（用户级）
python install.py --all --project # 装到当前项目
python install.py --list          # 只看目标路径，什么都不写
python install.py --check         # 校验 SKILL.md 是否合规
```

一行版（不用先 clone）：

```bash
curl -fsSL https://raw.githubusercontent.com/Joker-of-Gotham/finkg/main/install.sh | bash -s -- --all
```

```powershell
iwr -useb https://raw.githubusercontent.com/Joker-of-Gotham/finkg/main/install.ps1 | iex
```

### 4) Claude Code 插件市场

```
/plugin marketplace add Joker-of-Gotham/finkg
/plugin install finkg
```

### 手动放置

skill 是一个自包含目录，直接复制 `skills/finkg/` 到你宿主的 skill 目录即可：

| 宿主 | 用户级 | 项目级 |
| --- | --- | --- |
| **通用**（Cursor / Codex / Gemini 原生读取） | `~/.agents/skills/finkg` | `.agents/skills/finkg` |
| Claude Code（Copilot 也读这里） | `~/.claude/skills/finkg` | `.claude/skills/finkg` |
| Cursor 专用 | `~/.cursor/skills/finkg` | `.cursor/skills/finkg` |
| Codex CLI | `~/.codex/skills/finkg` | `.agents/skills/finkg` |
| OpenCode | `~/.config/opencode/skills/finkg` | `.opencode/skills/finkg` |
| Gemini CLI | `~/.gemini/skills/finkg` | `.gemini/skills/finkg` |
| GitHub Copilot | 读 `~/.claude/skills` | `.github/skills/finkg` |
| Windsurf | `~/.windsurf/skills/finkg` | `.windsurf/skills/finkg` |

装到 `~/.agents/skills/` 与 `~/.claude/skills/` 两处即可覆盖上面绝大多数宿主。逐宿主细节见 [docs/INSTALL.md](docs/INSTALL.md)。

---

## 快速开始

**1. 配置你自己的端点。** finkg **不携带任何地址、账号和密码** —— 装完之后第一件事是配置，
否则命令会明确报错并告诉你缺什么。把 skill 目录下的 `scripts/fg.py` 记为 `$FG`：

```bash
export FG="$HOME/.agents/skills/finkg/scripts/fg.py"
python $FG setup          # 交互式填写 LazySearch 地址、Neo4j 地址/用户名/密码
```

```powershell
$env:PYTHONIOENCODING = "utf-8"
$env:FG = "$HOME\.agents\skills\finkg\scripts\fg.py"
python $env:FG setup
```

它会在**你的工作区**写出 `financial_graph.local.json` 并检查该文件是否已被 `.gitignore` 覆盖：

```json
{
  "lazysearch_url": "http://<你的 LazySearch 主机>:<端口>",
  "neo4j_url": "http://<你的 Neo4j 主机>:7474",
  "neo4j_user": "<用户名>",
  "neo4j_password": "<密码>",
  "neo4j_allow_http_auth": true
}
```

也可以全部走环境变量 `FG_LAZYSEARCH_URL` / `FG_NEO4J_URL` / `FG_NEO4J_USER` / `FG_NEO4J_PASSWORD`。
详见 [docs/CONFIGURATION.md](docs/CONFIGURATION.md)。

**2. 自检。**

```bash
python $FG doctor
```

`configured` 逐项显示是否已配置，`checks` 区分「未配置」与「配置了但连不上」。
输出里地址与凭据只显示 `<已配置>`，也不列其他项目的数据库名，基本可以直接贴进 issue。

**3. 让 agent 用起来。** 在 Claude Code / Codex 里可以直接 `$finkg`，或者就用自然语言：

> 用 finkg 建一张图，回答「锂价如何经采购成本与分部毛利传导到宁德时代的盈利与估值」

agent 会读 [SKILL.md](skills/finkg/SKILL.md)，然后先跟你对齐范围、锚点和机制问题，再开始检索。完整实战见 [docs/WALKTHROUGH.md](docs/WALKTHROUGH.md)。

---

## 结构

```
finkg/
├── skills/finkg/               ← skill 本体，自包含，可整体复制
│   ├── SKILL.md                主 playbook + 双 router（LazySearch / Neo4j）
│   ├── references/             渐进式披露，用到才读
│   │   ├── LAZYSEARCH.md       数据源路由、提问法、收割纪律、信息利用率
│   │   ├── NODE_PROFILE.md     各类节点的属性维度组与字段目录
│   │   ├── EDGE_SEMANTICS.md   具体中文关系动作、边属性契约
│   │   ├── DEPTH.md            八语义层、多跳实质性判据、独立见证
│   │   ├── HITL.md             多阶段对齐、明文台账
│   │   ├── QUALITY.md          质量报告逐项含义与修复顺序
│   │   └── NEO4J.md            建库、装载、Cypher、Browser 可视化
│   ├── scripts/                7 个模块，零第三方依赖
│   │   ├── fg.py               唯一 CLI 入口
│   │   ├── fglazy.py           LazySearch 双通道 + 返回切层 + 单元格清点
│   │   ├── fgmodel.py          事实/实体校验 + 图编译 + 质量度量
│   │   ├── fgdepth.py          多跳路径、独立见证、结构指标
│   │   ├── fgneo4j.py          Neo4j HTTP 事务桥（装载+读回核对+回滚）
│   │   ├── fgstore.py          会话存储、明文台账、编码兜底
│   │   └── fgconfig.py         配置解析与密钥隔离
│   ├── assets/templates/       实体/事实/边/机制问题 JSON 模板
│   ├── assets/cypher/          开箱可用的 Cypher 查询集
│   └── fg.defaults.json        非机密端点默认值
├── install.py / .sh / .ps1     跨宿主安装器
├── docs/                       安装、快速开始、架构、配置、实战、排错
├── examples/                   可复现的样例数据
└── tests/                      离线单测，不依赖外部服务
```

## 命令一览

| 命令 | 用途 |
| --- | --- |
| `fg doctor` | LazySearch / Neo4j / 会话自检 |
| `fg session new\|list\|show\|set` | 会话、锚点、机制问题、质量档 |
| `fg brief` | 热上下文：现在缺什么、下一步做什么 |
| `fg search "<问题>"` | 调 LazySearch 并完整落盘 |
| `fg harvest show <id> --part …` | 分层取用一次收割（渐进式披露） |
| `fg harvest mine\|dispose` | 提交挖掘成果 / 说明为什么不用 |
| `fg usage` | 信息利用率逐单元格核账 |
| `fg entity add` / `fg fact add` | 导入实体 / 事实（含引文核验） |
| `fg compile` | 由事实编译 `graph.json` |
| `fg node <id>` | 一个节点的完整详表与来源 |
| `fg depth` | 纵深报告与机制问题检查 |
| `fg quality` | 质量报告（只判内容） |
| `fg align` / `fg answer` / `fg ledger` | 人在回路明文台账 |
| `fg neo4j ensure-db\|load\|snapshot\|query\|hop\|grass\|wipe` | Neo4j 操作 |
| `fg export --output <目录>` | 导出交付包（中文表头 CSV + 图 + 事实 + 台账 + 报告） |

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/QUICKSTART.md](docs/QUICKSTART.md) | 从零到第一张图，含每步预期输出 |
| [docs/INSTALL.md](docs/INSTALL.md) | 四种安装途径 × 八个宿主的完整说明 |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | 端点、密钥优先级、会话目录、质量档 |
| [docs/WALKTHROUGH.md](docs/WALKTHROUGH.md) | 完整实战：锂价到估值的传导链 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 模块职责、数据流、设计取向与取舍 |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | 按症状索引的排错表 |
| [skills/finkg/SKILL.md](skills/finkg/SKILL.md) | agent 实际读的那份 playbook |

## 系统要求

| 项 | 要求 | 说明 |
| --- | --- | --- |
| Python | 3.10+ | 仅标准库，无需 pip install |
| Neo4j | 5.x | 企业版可一会话一库；社区版用 `--database neo4j` |
| LazySearch | HTTP 或 MCP 端点 | 用于检索；无它也能手工落盘后建图 |
| 宿主 | 任一支持 Agent Skills 的 agent | 也可以纯 CLI 使用 |

Neo4j 与 LazySearch 都不是硬依赖：没有 Neo4j 仍可产出 `graph.json` 与中文表头 CSV；没有 LazySearch 可以把任何来源的返回用 `fg harvest add` 落盘后照常挖掘。

## 设计取向

- **落盘优于记忆。** 没有 `harvest_id` 与可核验引文的事实等于不存在。
- **交代优于完美。** 不用某个数据没问题，但必须写明为什么不用。
- **实质优于规模。** 节点数、边数、跳数都是结果，不是目标。
- **对齐优于授权。** 人在回路是共同判断，不是审批链。
- **明文优于加密。** 所有产物可读可改；工具不持有任何秘密（除了你自己的数据库密码）。
- **零依赖优于生态。** 只用标准库，装在任何能跑 Python 的地方。

## 贡献

欢迎 issue 与 PR。改动 `skills/finkg/` 后请跑：

```bash
python install.py --check    # SKILL.md 规范校验 + 脚本编译
python tests/test_finkg.py   # 离线单测
```

## 许可

[MIT](LICENSE)

---

## English

**finkg** is an [Agent Skill](https://agent-config.com/guides/agent-skills-standard/) that turns LazySearch retrieval into a deep, fully traceable Chinese financial knowledge graph in Neo4j. It works in Claude Code, Cursor, Codex CLI, OpenCode, Gemini CLI, GitHub Copilot, Windsurf and other Agent-Skills hosts.

What makes it different:

- **Nothing retrieved gets silently dropped.** A measured LazySearch reply carried 474 chars of conclusion but 11,814 chars of raw tables in its `history` — conclusions are only 4% of the payload. finkg persists every reply in full, splits it into separately addressable layers, then accounts for **every data cell**: used, explicitly dispositioned, or flagged as an open gap.
- **Nodes are dossiers, not labels.** Properties use hierarchical Chinese keys whose first segment becomes a dimension group, each carrying unit, currency, period, consolidation basis, source fact and a quote verified to exist verbatim in the persisted reply.
- **Edges are analyzable.** Neo4j relationship types are literal Chinese financial actions, each required to carry a one-sentence transmission mechanism plus quantitative attributes. Vague relations ("related", "affects") are rejected.
- **Depth means mechanism, not length.** Only business-entity hops count. A long path qualifies only if it crosses ≥3 semantic layers, uses ≥⌈hops/2⌉ distinct relation types, has evidence and mechanism on every hop, never chains >2 consecutive inferences, and has ≥2 edge-disjoint independent witnesses.
- **No ceremony.** No gates, signatures, HMACs, one-time tokens, artifact hash chains or secret env vars. Human-in-the-loop is staged structured questioning recorded in a plain-text ledger.
- **Quality checks substance, not shape.** Never "is this key present" or "does this hash match" — only whether a human could actually act on the graph.

Install with `npx skills add Joker-of-Gotham/finkg -g`, or `git clone` and run `python install.py --all`. Requires Python 3.10+ with zero third-party dependencies. Docs are in Chinese; see [docs/](docs/).
