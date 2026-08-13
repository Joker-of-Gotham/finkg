# 排错

按症状查。先跑 `fg doctor`，它能定位大部分环境问题。

## 安装与发现

| 症状 | 原因 | 处理 |
| --- | --- | --- |
| 宿主列表里没有 `finkg` | 目录名不是 `finkg`，或 `SKILL.md` 不在该目录根下 | `python install.py --list` 看实际路径；确认 `<路径>/finkg/SKILL.md` 存在 |
| 装了但 Codex 不加载 | `description` 超过 500 字符或换行了 | `python install.py --check` |
| Claude Code 看不到，Cursor 看得到 | 只装了 `.agents/skills/`，Claude 读 `.claude/skills/` | `python install.py --agent claude` |
| 改了源码但宿主里还是旧的 | 用复制方式安装的 | `python install.py --all --force` 重新同步 |
| `符号链接失败` | Windows 未开开发者模式 | 去掉 `--link`，让它自动回退复制；或开启开发者模式 |
| 装完仍不生效 | 宿主需要重启会话 | 重开一个会话 |
| `python: command not found` | 只有 `python3` | 用 `python3 install.py`；`install.sh` 会自动探测 |

## LazySearch

| 症状 | 原因 | 处理 |
| --- | --- | --- |
| `连不上 LazySearch` | 端点不对或不在同一网络 | `curl <lazysearch_url>/health` 应返回 `{"status":"healthy"}` |
| MCP 返回 307 空响应 | 路径写成 `/mcp/` 带尾斜杠 | 用 `/mcp`；工具已自动跟随重定向，手工调用时注意 |
| 检索超时 | 深度检索可能跑几分钟 | `fg search "…" --timeout 1200` 或 `FG_LAZYSEARCH_TIMEOUT=1200` |
| `data_cells` 很少、`data_chars` 接近 0 | 走了 MCP 通道，只有结论 | 用默认 HTTP 通道；宿主 MCP 的返回要 `fg harvest add` 补落盘 |
| 返回内容很空泛 | 查询没要求穷尽 | 加「全部科目」「全部行」「不要摘要」，把口径写进问题 |
| 拿不到想要的表 | 没点名工具或库表 | 先 `fg env` 看你这套环境已知的表与工具，在查询里直接点名；档案是空的就先做一轮探索，见 `references/LAZYSEARCH.md` |

## Neo4j

| 症状 | 原因 | 处理 |
| --- | --- | --- |
| `Neo4j 认证失败（HTTP 401）` | 密码没读到或不对 | `fg doctor` 看 `neo4j_password` 是不是 `<set>`；密码只从工作区 `financial_graph.local.json` 或 `FG_NEO4J_PASSWORD` 读 |
| `不愿意把 Basic Auth 发到非回环的明文 HTTP` | 未开 LAN HTTP | 设 `"neo4j_allow_http_auth": true` |
| `数据库名只能用 ASCII 字母数字点横线` | 库名带下划线或中文 | 换名，或让工具自动生成 |
| `UnsupportedAdministrationCommand` | 社区版没有多库 | `fg session new … --database neo4j` |
| `装载已回滚（库内容未变）：节点数不符` | 有边的端点指向不存在的实体 | `fg validate` 看悬空端点，`fg compile` 后再装 |
| `Tried to execute Administration command after executing Read query` | 一个事务里混了管理命令与读查询 | 分开跑（工具内部已分开，手写 Cypher 时注意） |
| 装完 Browser 里一个节点都没有 | 没切库 | `:use fg-<库名>` |
| Browser 里节点显示 ID 不显示中文名 | caption 已在库里，通常是查询没返回节点本体 | 用 `MATCH (n:FGNode) RETURN n` 返回节点本体而不是属性；再上传 `browser.grass` |
| 不小心装到了别人的库 | `--database` 指错 | `fg doctor` 列出所有库；`fg neo4j wipe --confirm` 只清当前会话库 |

## 事实与引文

| 症状 | 原因 | 处理 |
| --- | --- | --- |
| `quote 在 h-xxxx 的返回里找不到` | 引文是改写的、来自别处，或摘了带 `**`/`&nbsp;` 的渲染文本 | `fg harvest show <id> --part data --grep "关键词"` 摘真正的原文 |
| `harvest h-xxxx 未落盘` | 事实的 `harvest_id` 指向不存在的收割 | 检查 ID 拼写；或者这次检索还没落盘 |
| `数值缺单位/币种/百分比标记` | `object` 里没写 `unit`/`currency`/`percent` | 补上。万元和元差一万倍 |
| `数值缺 period` | 没写时点或区间 | 补 `period`，区分 `instant` 与 `duration` |
| `inference 类事实必须写 basis_fact_ids` | 推断没说依据 | 写依据事实 ID + `rule`；没有依据就不该断言 |
| `推断依赖成环` | A 依据 B、B 依据 A | 打断环，找到真正的地基事实 |
| `id 重复` | 手工分配 ID 撞了 | 留空让工具自动分配 |

## 图的形状

| 症状 | 原因 | 处理 |
| --- | --- | --- |
| 图看起来大但没法回答问题 | 大量只有名字的节点 | `fg quality` 看 `name_only_nodes`，补属性或删掉 |
| 每个节点只有名称和一两个数 | 只用了结论，没挖 `history` 里的原始表 | `fg harvest show <id> --part cells --unused-only` |
| 边很多但没法深入分析 | 关系空泛、无机制、无量化属性 | 看 `missing_mechanism` / `missing_attrs`，见 `references/EDGE_SEMANTICS.md` |
| 有 10 跳但读起来不像机制 | 跨层不足或同一种关系接龙 | `fg depth` 看 `weak_because` |
| 一根长链，断一跳全断 | `two_core_ratio` 接近 0、`bridge_ratio` 接近 1 | 为同一结论找第二条边不重叠的独立通路 |
| 数字对不上、口径打架 | 报告期 vs 时点、合并 vs 母公司、复权口径混用 | `fg quality` 的 `conflicts`，用 `fg align` 交给用户判 |
| 信息利用率上不去 | 挖掘停在「看过就算」 | `fg usage` 找最差的几次收割，逐格挖或逐格 `dispose` |
| `节点属性只覆盖 2 个维度组` | 只查了一个方向 | 按 `references/NODE_PROFILE.md` 的八组补检索 |

## Windows 专项

| 症状 | 原因 | 处理 |
| --- | --- | --- |
| 中文输出乱码 | 控制台编码 | `$env:PYTHONIOENCODING="utf-8"` |
| `不是合法 JSON：Invalid control character` | PowerShell 的 `>` 按控制台宽度折断了长字符串 | **不要用 `>` 或 `Out-File`**。用 `fg template <名称> --output <文件>`，或任意命令加 `--out-file <文件>`；要喂给工具的 JSON 直接用编辑器/文件写入工具生成 |
| `不是 UTF-8 也不带可识别的 BOM` | 文件是奇怪编码 | 用 UTF-8 重存；工具已能自动识别 UTF-8/16/32 BOM |
| `unrecognized arguments: --out-file` | 老版本 | 更新到 1.0.0+；现在全局选项放子命令前后都可以 |
| 符号链接建不了 | 未开开发者模式 | 让 `install.py` 自动回退复制即可 |

## 会话

| 症状 | 原因 | 处理 |
| --- | --- | --- |
| `有多个会话（…）。用 --session 指定` | 会话根目录下有多个会话 | 加 `--session <名称>` 或设 `FG_SESSION` |
| `还没有任何会话` | 工作区判定跑偏了 | `fg doctor` 看 `workspace` 与 `sessions_root` 字段；从项目根目录跑，或设 `FG_WORKSPACE` |
| `还没有 graph.json` | 没编译过 | `fg compile` |
| `会话已存在` | 同名 | 换 `--id`，或直接继续用它 |
| 手工改了 facts.jsonl 但报告没变 | 报告是读模型 | 重跑 `fg compile`，再跑 `fg quality` |

## 还是不行

1. `fg doctor --out-file doctor.json` 收集环境信息
2. `python install.py --check` 确认 skill 本身合规
3. `python tests/test_finkg.py` 确认离线逻辑正常
4. 带上这三份输出开 [issue](https://github.com/Joker-of-Gotham/finkg/issues)。
   **注意去掉 `doctor.json` 里的主机地址等敏感信息**（密码不会出现在里面，只显示 `<set>`）。
