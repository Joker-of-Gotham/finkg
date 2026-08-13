# Neo4j：建库、装载、查图、可视化

需要 Neo4j 5.x。企业版支持多数据库，可以一会话一库；社区版只有一个库，用 `--database neo4j`。
Browser 地址是配置里的 Neo4j 地址加 `/browser/`。

## 凭据

**skill 不携带任何地址与账号**，全部来自使用者工作区的本地配置：

```bash
fg setup          # 交互填写并写入 financial_graph.local.json
fg doctor         # 看是否已配置、能否连通、服务版本、现有库列表
```

```json
// 工作区 financial_graph.local.json —— 必须 gitignore
{
  "neo4j_url": "http://<你的主机>:7474",
  "neo4j_user": "<用户名>",
  "neo4j_password": "<密码>",
  "neo4j_allow_http_auth": true
}
```

也可以全部走环境变量：`FG_NEO4J_URL`、`FG_NEO4J_USER`、`FG_NEO4J_PASSWORD`。

密码**只**从工作区 local 文件或环境变量读取——其他配置源里的 `neo4j_password` 会被忽略，
所以即使误提交了共享配置也不会泄露口令。

`neo4j_allow_http_auth` 必须为 true 才会把 Basic Auth 发到非回环的明文 HTTP。这在局域网里
是正常场景，但默认关闭以免误发到公网地址。工具每次发请求前都会重新校验目标 origin，
不会把凭据发到别的主机。

## 一个会话一个库

```bash
fg neo4j ensure-db      # 建库 + 约束 + 索引
```

库名默认按会话名生成 `fg-<slug>`（Neo4j 库名只允许 ASCII 字母数字点横线，3~63 字符，不能以 `system` 开头，**不能有下划线**）。要指定：`fg session new … --database my-graph` 或任何命令加 `--database`。

`ensure-db` 会建：

- `fg_node_id`：`FGNode.id` 唯一约束（保证 MERGE 幂等、MATCH 走索引）
- `fg_node_caption`、`fg_node_kind`：两个索引

社区版没有多数据库，`ensure-db` 会明确告诉你，改用 `--database neo4j` 即可。

## 数据在库里的形状

**节点**同时带技术标签 `FGNode` 和语义标签：

```cypher
(:FGNode:Company {id: "E-catl", kind: "Company", caption: "宁德时代",
                  name: "宁德时代新能源科技股份有限公司",
                  `标识.证券代码`: "300750.SZ",
                  `财务.利润表.2025年报.营业总收入`: 423701834000.0,
                  `财务.每股指标.2025年报.基本每股收益`: 16.14,
                  _prop_count: 41, _prop_groups: ["身份","报告","财务","业务","所有权","风险"],
                  _fact_ids: [...], _harvest_ids: [...],
                  _prop_sources: "{…每个属性对应的事实 ID…}",
                  _prop_meta: "{…每个属性的单位/币种/期间/口径/来源收割…}"})
```

- 业务属性用**中文点分名**直接存。属性通过参数化 map 写入，所以中文和点号都不需要转义；在 Cypher 里读它们要加反引号。
- `_` 前缀的是系统属性。`_prop_sources` 与 `_prop_meta` 是 JSON 字符串（Neo4j 属性不能存嵌套结构），需要时在客户端解析，或直接 `fg node <id>` 看结构化版本。

**关系类型是 2–8 字中文短语**（品类、阈值、板块写在属性上）：

```cypher
(:Company {caption:"赣锋锂业"})
  -[:`长协供应` {
      id: "R00004", relation: "长协供应",
      机制: "长协锁量、价格随基准指数联动，锂价波动经采购成本进入宁德时代营业成本",
      语义层: "supply_operation", 认识状态: "reported", 期间: "2025年",
      品类: "电池级碳酸锂", 合同类型: "长期协议", 计价方式: "指数联动",
      原文: "…", _fact_ids: [...], _harvest_ids: [...]}]->
(:Company {caption:"宁德时代"})
```

好处是 Browser 图例能扫读，Cypher 里也能按短中文关系名直接查。代价是关系类型仍然不少——用 `type(r)` 而不是写死类型名来做通用查询。

## 装载语义

```bash
fg neo4j load --dry-run     # 先看会写成什么样（标签分布、关系类型分布、样本行）
fg neo4j load               # 正式装；规模/跳数从不拦截
fg neo4j load --append      # 不清空已有内容
fg neo4j load --force       # 证据类错误（引文找不到、端点悬空）也装
```

装载在**单个显式事务**里完成：

1. 清空本库 `FGNode`（`--append` 时跳过）
2. 按标签分组 UNWIND 批量 MERGE 节点，按关系类型分组 MERGE 关系
3. **在同一事务里读回**：总节点数、总关系数、逐标签节点数、孤立节点数、属性最多的节点
4. 读回与预期不符 → **整体回滚**，库内容不变，并告诉你哪一项不符
5. 相符才提交

所以装载要么完整成功，要么库完全没动。装载**只拦证据类错误**（引文找不到、端点悬空）；节点数、边数、跳数、扇区覆盖从不拦截。`--force` 用于明知证据有问题仍要看形状的情况。

装载后自动写出 `browser.grass`。

## 常用查询

`fg neo4j query "<cypher>"` 跑读查询（检测到写操作会拦下，确实要写加 `--write`）。也可以直接粘进 Browser。完整集合在 `assets/cypher/`。

### 看图的整体形状

```cypher
MATCH (n:FGNode) RETURN n.kind AS 类型, count(*) AS 数量 ORDER BY 数量 DESC;

MATCH ()-[r]->() RETURN type(r) AS 关系, count(*) AS 数量 ORDER BY 数量 DESC LIMIT 30;
```

### 哪些节点是实的、哪些是空壳

```cypher
MATCH (n:FGNode)
RETURN n.caption AS 节点, n.kind AS 类型, n._prop_count AS 属性数, n._prop_groups AS 维度组
ORDER BY 属性数 DESC LIMIT 25;

MATCH (n:FGNode) WHERE coalesce(n._prop_count, 0) = 0
RETURN n.kind AS 类型, collect(n.caption)[..20] AS 只有名字的节点;
```

### 一个公司节点的完整财务详表

```cypher
MATCH (n:FGNode {id: "E-catl"})
UNWIND [k IN keys(n) WHERE k STARTS WITH "财务."] AS 科目
RETURN 科目, n[科目] AS 数值 ORDER BY 科目;
```

### 边够不够可分析

```cypher
MATCH ()-[r]->()
WHERE r.机制 IS NULL OR r.机制 = ""
RETURN type(r) AS 缺机制的关系, count(*) AS 数量 ORDER BY 数量 DESC;

MATCH ()-[r]->()
RETURN r.语义层 AS 语义层, count(*) AS 边数 ORDER BY 边数 DESC;
```

### 多跳与机制链

```bash
fg neo4j hop --from-id E-lithium --to-id E-catl-a --min-hops 5 --max-hops 12 --limit 5
```

等价 Cypher（可变长度的上下界必须是字面量，不能参数化）：

```cypher
MATCH p = (a:FGNode {id: "E-lithium"})-[*5..12]-(b:FGNode {id: "E-catl-a"})
RETURN [n IN nodes(p) | n.caption]           AS 路径节点,
       [r IN relationships(p) | type(r)]      AS 关系链,
       [r IN relationships(p) | r.语义层]     AS 语义层链,
       [r IN relationships(p) | r.机制]       AS 机制链,
       size(apoc.coll.toSet([r IN relationships(p) | r.语义层])) AS 跨层数,   -- 无 APOC 时删掉此行
       length(p)                              AS 跳数
ORDER BY 跳数 DESC LIMIT 5;
```

**不依赖 APOC 的跨层计数**：

```cypher
MATCH p = (a:FGNode {id: "E-lithium"})-[*5..12]-(b:FGNode {id: "E-catl-a"})
WITH p, [r IN relationships(p) | r.语义层] AS layers
UNWIND layers AS l
WITH p, layers, count(DISTINCT l) AS 跨层数
WHERE 跨层数 >= 3
RETURN [n IN nodes(p) | n.caption] AS 路径节点, layers AS 语义层链,
       跨层数, length(p) AS 跳数
ORDER BY 跳数 DESC, 跨层数 DESC LIMIT 10;
```

### 从锚点能走多远

```cypher
MATCH (a:FGNode {id: "E-catl"})
CALL {
  WITH a MATCH p = (a)-[*1..12]-(b:FGNode)
  RETURN b, min(length(p)) AS d
}
RETURN d AS 距离, count(b) AS 节点数 ORDER BY 距离;
```

### 每个属性的出处

```cypher
MATCH (n:FGNode {id: "E-catl"})
RETURN apoc.convert.fromJsonMap(n._prop_meta) AS 属性口径;   -- 有 APOC 时
```

没有 APOC 就用 `fg node E-catl`，它直接给结构化的属性 + 单位 + 期间 + 来源事实 + 原文引文。

## Browser 可视化

```bash
fg neo4j grass          # 生成 browser.grass
```

在 Browser 里：

1. `:use fg-<你的库名>` 切库
2. 左侧 `:style` 面板 → 把 `browser.grass` 拖进去
3. `MATCH (n:FGNode)-[r]->(m) RETURN n, r, m LIMIT 300`

caption 已经作为节点属性存在库里，所以即使不上传样式，节点也能显示中文名。样式文件负责的是按类型着色、按关系类型着色、加大锚点直径。

关系类型多的时候 Browser 图例会很长——这是中文关系名的正常代价，换来的是不点开就能读懂。

## 排错

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `Neo4j 认证失败（HTTP 401）` | 密码没读到或不对 | `fg doctor` 看 `neo4j_password` 是不是 `<set>`；检查工作区 `financial_graph.local.json` |
| `不愿意把 Basic Auth 发到非回环的明文 HTTP` | 未开 LAN HTTP | 设 `"neo4j_allow_http_auth": true` |
| `数据库名只能用 ASCII 字母数字点横线` | 库名带下划线或中文 | 换名，或让工具自动生成 |
| `UnsupportedAdministrationCommand` | 社区版没有多库 | `--database neo4j` |
| `装载已回滚（库内容未变）：节点数不符` | 有节点的 `from`/`to` 指向不存在的实体 | `fg validate` 看悬空端点；`fg compile` 后再装 |
| `Tried to execute Administration command after executing Read query` | 一个事务里混了管理命令与读查询 | 分开跑（工具内部已分开） |
| 装完 Browser 里一个节点都没有 | 没切库 | `:use fg-<库名>` |

## 清理

```bash
fg neo4j wipe --confirm          # 清空本库内容，库还在
fg neo4j wipe --confirm --drop   # 连库一起删
```

`fg doctor` 会列出所有现存库。这台 Neo4j 上还有很多其他项目的库，**动手前先确认 `--database` 指的是你自己那个**。
