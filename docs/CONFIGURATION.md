# 配置

## 这个仓库不携带任何部署信息

地址、账号、密码**没有默认值**，全部由你在自己的工作区配置。未配置时命令会明确报错并
列出要填什么、填到哪里，不会静默连到某个内置地址。

这样做有两个理由：一是公开仓库里不应出现任何主机、账号或口令；二是每套部署都不一样，
内置默认值只会让人误以为能开箱即用。

## 一条命令配好

```bash
fg setup                                        # 交互式填写
fg setup --neo4j-user neo4j --allow-http-auth   # 参数式（密码留空则交互输入，避免进 shell 历史）
fg doctor                                       # 确认
```

`fg setup` 会写入工作区 `financial_graph.local.json`，并检查它是否已被 `.gitignore` 覆盖，
没有就警告。

## 全部可配置项

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `lazysearch_url` | **空，必填** | LazySearch origin，形如 `http://<主机>:<端口>`（不带路径） |
| `neo4j_url` | **空，必填** | Neo4j HTTP origin，形如 `http://<主机>:7474`（不带路径、不带凭据） |
| `neo4j_user` | **空，必填** | 用户名 |
| `neo4j_password` | **空，必填** | **只能从工作区 local 文件或环境变量读** |
| `neo4j_allow_http_auth` | `false` | 允许把 Basic Auth 发到非回环明文 HTTP；局域网场景要设 `true` |
| `neo4j_database` | 空 | 留空则按会话名自动生成 `fg-<slug>` |
| `lazysearch_mcp_path` | `/mcp` | MCP 端点路径（**末尾不要斜杠**，`/mcp/` 会 307 重定向） |
| `lazysearch_timeout` | `600` | 单次检索超时秒数；深度检索可能跑几分钟 |
| `sessions_dir` | `financial-graph-sessions` | 会话根目录，相对工作区或绝对路径 |

前四项是「部署项」。`fg doctor` 的 `configured` 字段会逐项显示是否已配置。

## 优先级

后者覆盖前者：

```
内置默认（只有非部署项）
  ↓
<skill>/fg.defaults.json           ← 只有超时、会话目录这类与部署无关的项
  ↓
<工作区>/financial_graph.json       ← 团队共享的非机密配置，可提交（放不进密码）
  ↓
<工作区>/financial_graph.local.json ← 本机私有，唯一允许放密码的地方，务必 gitignore
  ↓
FG_* 环境变量                        ← 如 FG_NEO4J_PASSWORD、FG_LAZYSEARCH_URL
  ↓
命令行参数                           ← --neo4j-url / --database / --lazysearch-url
```

环境变量名 = `FG_` + 键名大写。例如 `FG_SESSIONS_DIR`、`FG_LAZYSEARCH_TIMEOUT`。

看当前生效的配置（地址与凭据只显示 `<已配置>` / `<未配置>`，值不会被打印）：

```bash
fg doctor              # 默认：不含主机、账号、密码，也不列其他项目的数据库名
fg doctor --verbose    # 本机排错：额外显示库名
```

默认输出基本可以直接贴进 issue，只需留意它含本机路径（`workspace` / `sessions_root`）。

## 密钥处理

密码**只**从两处读取：

1. 工作区 `financial_graph.local.json`
2. 环境变量 `FG_NEO4J_PASSWORD`

其他配置源里的 `neo4j_password` 会被**忽略**，包括 `fg.defaults.json` 和
`financial_graph.json`。这样即使误提交了共享配置，也不会泄露密码。

```json
// <工作区>/financial_graph.local.json —— 加进你的 .gitignore
{
  "lazysearch_url": "http://<你的 LazySearch 主机>:<端口>",
  "neo4j_url": "http://<你的 Neo4j 主机>:7474",
  "neo4j_user": "<用户名>",
  "neo4j_password": "<密码>",
  "neo4j_allow_http_auth": true
}
```

仓库里有 `financial_graph.local.json.example` 可以复制。

工具不持有任何其他秘密——没有签名密钥、没有 attestation key、没有 token。

## 环境档案

除了连接信息，还有一类**本地信息**不应进仓库：你这套部署里有哪些数据表、字段、检索工具、
口径约定。它们记在工作区 `finkg.environment.md`：

```bash
fg env --init         # 从模板创建
fg env                # 读
fg env --grep "股东"   # 只看相关部分
```

agent 在设计检索前会读它，记得越准取数越精确。同样要加进 `.gitignore` ——
这是你机构的内部信息。填写方法见 `skills/finkg/references/LAZYSEARCH.md`。

### 明文 HTTP

局域网 Neo4j 通常是明文 HTTP。工具默认拒绝把 Basic Auth 发到非回环的 HTTP 地址，
必须显式开启：

```json
{ "neo4j_allow_http_auth": true }
```

回环地址（`127.0.0.1`、`localhost`）不需要这个开关。用 HTTPS 也不需要。

每次请求前工具会重新校验目标 origin，不会把凭据发到配置之外的主机。

## 工作区在哪

工作区决定了配置文件和会话产物的位置。判定顺序：

1. 环境变量 `FG_WORKSPACE`（绝对路径）
2. 从当前目录逐级向上找，第一个含 `.claude` / `.git` / `AGENTS.md` / `financial_graph.local.json` 的目录
3. 都没有则用当前目录

所以**从项目根目录跑命令**最省事。跑之前不确定的话看 `fg doctor` 输出的 `workspace` 字段。

## 会话目录

```
<工作区>/financial-graph-sessions/<会话名>/
├── session.json          主题、中心问题、锚点、机制问题、质量档、Neo4j 库名
├── harvest/
│   ├── index.json        收割索引与挖掘状态（unmined / partial / mined）
│   └── h-0001.json       一次检索的完整落盘（含切层与单元格清点）
├── entities.jsonl        实体注册表
├── facts.jsonl           原子事实（含 quote 与 harvest_id）
├── dispositions.json     未用数据的处置理由
├── graph.json            编译产物（可重建）
├── ledger.jsonl          人在回路明文台账 + 工作日志
├── browser.grass         Neo4j Browser 配色
└── reports/
    ├── quality.json
    ├── depth.json
    └── harvest-usage.json
```

全是明文，随时可以用编辑器打开检查或手工修补。改完记得重跑 `fg compile`。

换位置：

```bash
fg session new "主题" --id my-session          # 指定会话名
export FG_SESSIONS_DIR=/data/kg-sessions      # 换会话根目录（可绝对路径）
```

多个会话共存时用 `--session <名称>` 或 `FG_SESSION` 指定；只有一个会话时可以省略。

## Neo4j 数据库

**一个会话一个专用库。** 默认按会话名生成 `fg-<slug>`。

库名限制（Neo4j 硬约束）：3–63 字符，只允许 ASCII 字母、数字、点、横线，
**不能有下划线**，不能以 `system` 开头，不能以点或横线结尾。中文主题会自动转成可用 slug。

```bash
fg session new "主题" --database my-graph      # 建会话时指定
fg neo4j load --database other-graph          # 单次覆盖
fg neo4j ensure-db                            # 建库 + 唯一约束 + 索引
```

企业版支持 `CREATE DATABASE`。社区版没有多库，`ensure-db` 会明确提示，改用：

```bash
fg session new "主题" --database neo4j
```

社区版下所有会话共用一个库，装载时的整库替换会清掉上一个会话的内容——需要保留就先 `fg export`。

> 目标实例上可能有很多其他项目的库。`fg doctor` 会把现有库全部列出来，**动手前确认
> `--database` 指的是你自己那个**。

## 质量档

| 指标 | `probe` 探路 | `standard` 标准 | `deep` 纵深 |
| --- | --- | --- | --- |
| 锚点已知属性数 | ≥12 | ≥24 | ≥40 |
| 有证据业务节点 | ≥30 | ≥120 | ≥300 |
| 可分析边占比 | ≥60% | ≥80% | ≥90% |
| 用到的语义层 | ≥3 | ≥6 | 8 |
| 信息交代率 | ≥50% | ≥75% | ≥85% |
| 独立实质路径 | 6跳×1 | 6跳×8 + 8跳×3 | 6跳×20 + 10跳×8 |

另有三项与档位无关，始终同一标准：数值可用率（有单位/币种 + 有期间）≥95%、
锚点属性覆盖 ≥5 个维度组、空泛关系为 0。

```bash
fg session set --profile deep
fg quality --profile probe      # 临时用别的档看一眼，不改会话设置
```

档位是**目标**，不是准入条件。`ok=false` 只意味着「还不能对外声称达标」，不阻止任何操作。

## LazySearch 通道

| 通道 | 命令 | 拿到什么 |
| --- | --- | --- |
| HTTP `/api/query` | `fg search "<问题>"` | 结论 + 完整 `history`（原始表、执行的 SQL、源库表名）**首选** |
| MCP | `fg search "<问题>" --channel mcp` | 只有结论 |
| MCP（宿主直调） | 宿主的 `query_financial_data` 工具 | 只有结论，**用完必须 `fg harvest add` 补落盘** |

超时不够时：

```bash
fg search "<深度问题>" --timeout 1200
export FG_LAZYSEARCH_TIMEOUT=1200
```

把宿主 MCP 的返回补落盘：

```bash
fg harvest add "刚才问的问题" --file reply.txt --channel mcp
```

## 无外部服务时

两个服务都不是硬依赖：

- **没有 Neo4j**：照常 `fg compile` / `fg quality` / `fg depth` / `fg export`，得到
  `graph.json` 与中文表头 CSV。只是不能 `fg neo4j *`。
- **没有 LazySearch**：把任何来源（网页、PDF 摘录、内部系统导出）的文本用
  `fg harvest add` 落盘，后面的挖掘、编译、质量、纵深全部照常。引文核验也照常生效。
