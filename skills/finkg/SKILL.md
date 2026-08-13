---
name: finkg
description: 构建、扩展或审计中文金融知识图谱时使用。用 LazySearch 检索并把返回全量落盘，深挖成可回溯原文的原子事实，写入 Neo4j。节点按维度组带丰富属性（财务/行情/估值/所有权/供应/治理/风险）；边是短中文关系短语（词或动宾，对象与量化写在属性上）。纵深由机制问题引导、按扇区逐步发掘，不设节点/边/跳数门槛。逐单元格核账利用率。分阶段与人对齐，无 Gate、签名或密钥。关键词 financial knowledge graph, Neo4j, LazySearch, graph RAG, 产业链, 传导链, 财报, 股东。
license: MIT
---

# finkg：金融图谱共建

这个 skill 做一件事：**把 LazySearch 检索到的金融信息，一格不浪费地变成一张能被追问到底的 Neo4j 图**，而且每个关键判断都跟用户当面对过。

它不是本体工程工具。类型只用来区分「公司/股票/上市记录/事件/风险不是一回事」，不用来做 OWL 建模。

## 先把工具箱路径固定下来

工具箱在**本文件同级的 `scripts/fg.py`**。你刚才读本文件的那个目录就是 skill 目录，把它记为 `$SKILL`：

```bash
export FG="$SKILL/scripts/fg.py"          # macOS / Linux
$env:FG = "$SKILL\scripts\fg.py"          # Windows PowerShell
$env:PYTHONIOENCODING = "utf-8"           # Windows 必须
```

常见的 `$SKILL` 位置：`~/.claude/skills/finkg`、`~/.agents/skills/finkg`、`~/.codex/skills/finkg`、
`./.agents/skills/finkg`、`./skills/finkg`（仓库内开发时）。

下文把 `python $FG` 简写为 `fg`。**命令一律从用户的工作区根目录跑**，因为会话产物要落在工作区里。

> **Windows 两个必知**：`$env:PYTHONIOENCODING="utf-8"`；要把输出落盘**不要用 `>`**——
> PowerShell 会按控制台宽度折断长字符串，`Out-File` 也一样。用工具自己写：
> 任意命令加 `--out-file <文件>`，或 `fg template <名称> --output <文件>`。
> 要喂给工具的 JSON 请直接用文件写入工具生成，不要靠 shell 重定向拼。

## 激活后先做三件事

**1. 自检环境。**

```bash
fg doctor
```

**这个 skill 不携带任何地址、账号和密码**，它们只存在于使用者自己的工作区。所以第一次跑
`doctor` 大概率会看到 `configured` 全是 false —— 这是正常的，它会把要填什么、填到哪里
直接列在 `next` 里。照它说的做，或者让工具生成：

```bash
fg setup                                   # 交互式填写并写入工作区本地配置
fg setup --neo4j-user neo4j --allow-http-auth   # 也可以用参数（密码留空会交互输入，避免进 shell 历史）
```

生成的 `financial_graph.local.json` **必须在使用者的 `.gitignore` 里**，`fg setup` 会检查并提醒。
局域网用明文 HTTP 连 Neo4j 时 `neo4j_allow_http_auth` 要设 true，否则工具拒绝发送凭据。

配好之后 `doctor` 里 `lazysearch_health.ok` 与 `neo4j.ok` 应为 true。若显示
`reason: unreachable`，说明配置写了但服务连不上，是网络/服务的问题而不是配置格式问题。

**2. 建会话，并把范围跟用户对齐。**

```bash
fg session new "<主题>" \
  --center-question "<这张图要回答的那一个问题>" \
  --anchor "E-xxx=<锚点名称>:Company" --anchor "E-xxx-a=<锚点证券>:Stock" \
  --profile standard   # probe | standard | deep
```

**3. 读 `references/HITL.md`，然后用 AskQuestion 问第一批问题。** 不要自己替用户决定主题边界、口径和优先级。

之后每做完一件事都跑一次 `fg brief` —— 它会告诉你现在缺什么、哪些收割还没挖、哪些对齐点还在等答复。

## 渐进式披露：什么时候读哪份 reference

**不要一次读完。** 每份文档只在你真的要做那件事之前读。

| 你正要做 | 先读 |
| --- | --- |
| 设计检索、写查询、决定问哪个数据源 | `references/LAZYSEARCH.md` + `fg env` |
| 把一次收割拆成事实、决定节点该有哪些属性 | `references/NODE_PROFILE.md` |
| 写关系、判断一条边够不够可分析 | `references/EDGE_SEMANTICS.md` |
| 补纵深、判断多跳是真深还是灌水 | `references/DEPTH.md` |
| 决定该不该问用户、怎么问、怎么记 | `references/HITL.md` |
| 看懂质量报告、决定先修哪一项 | `references/QUALITY.md` |
| 建库、装载、写 Cypher、Browser 可视化 | `references/NEO4J.md` |

同样的道理适用于**收割本身**：一次 LazySearch 返回常有几万字符，不要整块塞进上下文。用分层取用：

```bash
fg harvest show h-0001 --part summary       # 先看规模与出处
fg harvest show h-0001 --part cells --unused-only   # 还没用的数据单元格
fg harvest show h-0001 --part data --grep "毛利|存货"  # 只要相关原始表
fg harvest show h-0001 --part provenance    # 源库表 + 执行过的 SQL
```

## LazySearch 路由

地址从配置读（见上一节）。两条通道**都会落盘**，落盘才有可追溯性和渐进式披露：

- `fg search "<问题>"` —— 走 HTTP `/api/query`，除结论外还拿到**完整 history**：每次内部工具调用的原始表格、执行过的 SQL、源库表名。信息量通常是结论的 10~30 倍，是首选。
- MCP 工具 `query_financial_data`（配置里的地址 + `/mcp`）—— 交互方便、结论已排版好，但拿不到中间原始表。用它之后必须补落盘：

```bash
fg harvest add "<刚才问的问题>" --file <把返回文本存成的文件> --channel mcp
```

### 先读环境档案

**每套部署能查到的数据表和可用的检索工具都不一样**，所以这些不写死在 skill 里，而是记在
使用者自己的环境档案 `finkg.environment.md`（工作区本地文件，不入库）：

```bash
fg env                       # 读：这套环境有哪些库表、工具、口径约定、已知坑
fg env --grep "股东|持股"     # 只看相关部分
fg env --init                # 还没有就先建一个
```

**设计检索之前先读它。** 里面记着的表名与工具名可以直接点进查询，取数会精确得多。
档案是空的就先做一轮探索（下面第 5 条），并把发现补进去——这是一次性投入，长期复用。

### 按扇区提问

不必知道底层表名也能问，LazySearch 会自己路由；知道了则更准。十个扇区的完整清单与
示例问法见 `references/LAZYSEARCH.md`，这里只列最常用的：

| 你要的东西 | 查询里这样说 |
| --- | --- |
| 证券实体消歧、代码、资产类别、市值 | 「解析 X 的证券实体，A股/港股/美股候选都列出并说明区别」 |
| 三大报表逐科目 | 「X 某期合并利润表**全部科目与数值，不要摘要**」 |
| 成长/盈利/偿债/营运能力指标 | 「X 最近 3 个报告期财务指标全部字段」 |
| 主营业务构成、分部收入毛利 | 「X 最新主营业务构成及对应营收、成本、毛利率、占比、同比」 |
| 十大股东、持股比例与性质 | 「X 最新报告期前十大股东、持股数量、持股比例、股东性质」 |
| 行情、市值、估值倍数 | 「X 最近 N 个交易日日线与市值、PE(TTM)、PB」 |
| 海外股票财报、分析师预期 | 「<ticker> 的 income statement / analyst estimates」 |
| 宏观指标时序 | 「CPI / PPI / PMI 某频率某区间时间序列」 |
| 大宗商品产业链 | 「<品种> 价格 / 产量 / 开工率 / 库存 / 进出口时序」 |
| 进出口贸易 | 「HS 编码 X 某期按贸易伙伴的金额与数量」 |
| 公告、诉讼、处罚、事件定性 | 「X 最近的公告 / 监管处罚 / 诉讼」 |
| 文档深挖 | 「深度分析 <URL>，提取第 N 章全部表格与数值」 |
| 同比、CAGR、占比、相关性 | 「用 Python 计算 …，输出中间表」 |

### 写查询的五条纪律

1. **一次问一个可判定的事项，但要求穷尽**：「全部科目」「全部行」「不要摘要」。
2. **把口径写进问题**：币种、单位、报告期还是时点、合并还是母公司、是否复权、数据状态。
3. **有歧义就要求列全候选**：「如果有多个同名实体，全部列出并说明区别」。
4. **要求给出源表名与执行的 SQL**，这是事实能被回溯的前提。
5. **把新发现记回环境档案。** `fg harvest show <id> --part provenance` 会给出这次实际用到的
   工具名、源库表和完整 SQL（含全部字段的中文别名）——那是下次精确取数的现成清单，
   顺手补进 `finkg.environment.md`。

## Neo4j 路由

地址、用户名、密码都从配置读（见上一节）。**每个会话一个专用数据库**（默认按会话名生成 `fg-<slug>`）。

```bash
fg neo4j ensure-db                       # 建库 + FGNode.id 唯一约束 + 索引
fg neo4j load                            # 单事务整库替换 + 读回核对，核对不上整体回滚
fg neo4j load --dry-run                  # 先看会写成什么样
fg neo4j snapshot                        # 库里现在有什么
fg neo4j hop --from-id E-a --to-id E-b --min-hops 6 --max-hops 12
fg neo4j query "MATCH (n:Company) RETURN n.caption, n._prop_count ORDER BY n._prop_count DESC"
fg neo4j grass                           # Browser 配色，拖进 :style 面板
```

图在库里的形状：节点同时带 `FGNode` 与语义标签（`Company` / `Stock` / `Event` …），**关系类型是 2–8 字中文短语**（`持股`、`长协供应`、`准入约束`），`机制` / `语义层` / 品类与量化写在关系属性上。装库**不看节点数、边数、跳数**；只有引文找不到、端点悬空这类证据错误才会拒绝（`--force` 可覆盖）。规模不够时看 `fg brief` 的下一步查询，不要靠凑数过关。

## 六件不能违反的事

1. **先落盘，再使用。** 没有 `harvest_id` + 能在该次收割原文中找到的 `quote` 的事实，等于不存在——工具会直接指出 quote 找不到。不要凭记忆改写数字或原文。
2. **一条事实只说一个值或一个关系。** 复合单元格里的营收、成本、比率、期间必须拆开，各自带自己的单位、币种、期间、口径。
3. **公司、股票、上市记录是三样东西。** 公司报财务，股票有行情与估值，上市记录管板块、币种、交易规则。混成一个节点会让所有财务/行情属性口径失真。
4. **关系必须是 2–8 字中文短语，机制与量化放在边上。** 「长协供应」+ `attrs.品类=电池级碳酸锂` + 一句话机制。写成「向客户长协供应电池级碳酸锂」这种句子，图例读不动；写「相关」「影响」「关联」会被判为不可分析。
5. **纵深服务于机制问题，靠扇区逐步发掘。** 先和用户确定「哪条链路必须能走通」，再按 `LAZYSEARCH.md` 的十个扇区去问。**不要设「必须有 XXX 节点 / XXX 边 / XXX 跳」的门槛**，也不要为跳数接龙。`fg brief` 会指出下一个该问的扇区和还没用的单元格。
6. **不自主下单、开户、授信、估值签字或对外发布结论。** 高影响判断交给用户。

## 工作循环

六个动作，**按需反复，不是固定流水线**。每个动作之后跑 `fg brief`。

**① 定范围。** 中心问题、锚点公司与证券、时点、口径、要回答的机制问题。机制问题写进会话，纵深报告会照着它检查：

```bash
fg session set --mechanism-question '{"id":"M1","question":"锂价如何经采购成本与分部毛利传导到盈利与估值",
  "from":"E-lithium","to":"E-catl-a","min_hops":5,"independent":2,
  "layers":["supply_operation","financial_capital","expectation_valuation"]}'
```

**② 广度收割。** 按 `references/LAZYSEARCH.md` 的十个扇区铺开检索，一个扇区一到多次 `fg search`，打上 `--tag`。宁可多问，不要少问——落盘的成本很低，漏掉一整类信息的代价很高。

**③ 深挖成事实。** 对每次收割逐表逐行逐单元格地挖。草稿**只写进会话目录**，不要堆在工作区根目录：

```bash
fg template entity          # 写入 financial-graph-sessions/<会话>/drafts/entities.json
fg template fact            # 写入 drafts/facts.json
# 用编辑器改 drafts 里的文件
fg harvest mine h-0001 --done    # 自动读取 drafts/entities.json 与 drafts/fact*.json
fg harvest dispose h-0001 --scope "数据状态" --reason "库内数据版本标记，不是业务事实"
```

`--done` 表示这次收割已挖尽。真的不需要的数据用 `dispose` 写明理由——**「没用」和「说明过为什么不用」都算交代，静默丢弃不算**。

**④ 编译看形状。**

```bash
fg compile && fg node E-catl && fg usage
```

`fg node` 看某个节点的完整详表（每个属性的值、单位、期间、来源事实、原文引文）。`fg usage` 看信息利用率——检索回来多少单元格、用了多少、交代了多少、还有多少悬着。

**⑤ 补纵深。**

```bash
fg depth --min-hops 6
```

看 `weak_because`：跨层不够、关系重复、某跳没证据、连续推断过长，各有各的补法，都不是「再接一跳」能解决的。细则见 `references/DEPTH.md`。

**⑥ 装库读图。** 不必等「达标」——证据没问题就可以装，边挖边看。

```bash
fg quality && fg neo4j ensure-db && fg neo4j load
fg neo4j hop --from-id E-lithium --to-id E-catl-a --min-hops 5
fg export --output <交付目录> --include-harvest
```

## 什么时候必须停下来问用户

用 AskQuestion 在对话里问，摆出**当前判断 + 证据样本 + 2~3 个真实取舍 + 你的建议 + 为什么现在必须定**。拿到答复后记账：

```bash
fg align --stage expansion --question "<问题>" --option "<A>" --option "<B>" \
  --recommendation "<你的建议>" --why-now "<为何现在>"
fg answer "<用户的决定>" --effect "<你据此改了什么>"
```

必须问的场合：

- 同名实体、证券代码、上市地消歧不唯一；
- 两个来源对同一指标同一期间给出不同数值，或口径（合并/母公司、复权、重述）无法确定；
- 出现多个高价值扩展方向，优先级取决于用户的真实用途；
- 一条关键边只能靠推断成立，且存在合理的替代解释；
- 已经对齐过的范围、锚点、机制问题需要推翻或回退；
- 涉及付费来源、个人数据、对外写入或任何交易动作。

**没有 Gate、没有签名、没有令牌、没有哈希链。** 台账 `ledger.jsonl` 是明文，随时可以打开看、可以手改。它记录的是「我们商量过什么」，不是「谁被授权了」。质量报告同理：`ok=false` 只表示还有引文对不上或边名写成了句子；节点数/边数/跳数从来不是门槛，也不拦住装库。

## 常见走偏

| 症状 | 真实原因 | 怎么办 |
| --- | --- | --- |
| 图看起来很大但没法回答问题 | 大量只有名字的节点 | `fg quality` 看 `name_only_nodes`，补属性或删掉 |
| 每个节点都只有名称和一两个数 | 只用了 `final_answer`，没挖 history 里的原始表 | `fg harvest show <id> --part data`，`--part cells --unused-only` |
| 边很多但没法深入分析 | 关系空泛、写成句子、无机制、无量化属性 | `references/EDGE_SEMANTICS.md`，看 `vague_relations` / `sentence_relations` |
| 有 10 跳路径但读起来不像机制 | 跨层不足或同一种关系重复接龙 | `fg depth` 看 `weak_because`，`references/DEPTH.md` |
| 一根长链，任何一跳断掉就断 | 2-core 接近 0、桥边率接近 100% | 为同一结论找第二条边不重叠的独立通路 |
| 数字对不上、口径打架 | 报告期 vs 时点、合并 vs 母公司、复权口径混用 | `fg quality` 的 `conflicts`，用 `fg align` 交给用户判 |
| 信息利用率上不去 | 挖掘停在「看过就算」 | `fg usage` 找最差的几次收割，逐格挖或逐格 dispose |

## 命令索引

| 命令 | 用途 |
| --- | --- |
| `fg doctor` | 配置 / LazySearch / Neo4j / 会话自检 |
| `fg setup` | 生成工作区本地配置（地址、账号、密码只存在这里） |
| `fg env [--init] [--grep]` | 环境档案：这套部署有哪些数据表与检索工具 |
| `fg session new\|list\|show\|set` | 会话、锚点、机制问题、质量档 |
| `fg brief` | 热上下文：缺什么、下一步做什么 |
| `fg search "<问题>"` | 调 LazySearch 并落盘 |
| `fg harvest add\|list\|show\|mine\|dispose` | 收割仓库与分层取用 |
| `fg usage` | 信息利用率逐单元格核账 |
| `fg entity add` / `fg fact add` | 导入实体 / 事实 |
| `fg validate` | 事实与实体的内容质量校验 |
| `fg compile` | 由事实编译 `graph.json` |
| `fg node <id>` | 一个节点的完整详表与来源 |
| `fg depth` | 纵深报告与机制问题检查 |
| `fg quality` | 质量报告（只判内容，不判格式） |
| `fg align` / `fg answer` / `fg ledger` | 人在回路明文台账 |
| `fg neo4j ensure-db\|load\|snapshot\|query\|hop\|grass\|wipe` | Neo4j 操作 |
| `fg export --output <目录>` | 导出交付包 |
| `fg template [名称]` | JSON 模板 |

会话落在工作区 `financial-graph-sessions/<会话名>/`：`harvest/` 原始返回、`drafts/` 挖掘草稿、`facts.jsonl`、`entities.jsonl`、`graph.json`、`ledger.jsonl`、`reports/`。全是明文，随时可读可改。**不要把 `entities.json` / `facts.json` / `quality.json` 写到工作区根目录**——`fg brief` 发现根目录 stray 文件会直接点名。

工作区里还有两个本地文件，**都不应进版本库**：`financial_graph.local.json`（地址与凭据）、
`finkg.environment.md`（这套部署有哪些库表与工具）。skill 自身不携带任何部署信息。
