# 快速开始

从零到第一张能查的 Neo4j 图。每步都给出预期输出，方便对照。

前置：已按 [INSTALL.md](INSTALL.md) 装好，`python $FG doctor` 全绿。

```bash
export FG="$HOME/.agents/skills/finkg/scripts/fg.py"     # macOS / Linux
```

```powershell
$env:PYTHONIOENCODING = "utf-8"
$env:FG = "$HOME\.agents\skills\finkg\scripts\fg.py"     # Windows
```

下文用 `fg` 代表 `python $FG`。**所有命令从你的工作区根目录跑**，会话产物会落在那里。

---

## 1. 建会话

```bash
fg session new "宁德时代动力电池产业链" \
  --center-question "锂价如何经采购成本与分部毛利传导到宁德时代的盈利与估值" \
  --anchor "E-catl=宁德时代:Company" \
  --anchor "E-catl-a=宁德时代A股:Stock" \
  --profile standard
```

```json
{
  "ok": true,
  "session_id": "ning-de-shi-dai-20260813",
  "path": ".../financial-graph-sessions/ning-de-shi-dai-20260813",
  "neo4j_database": "fg-ning-de-shi-dai-20260813"
}
```

锚点格式是 `ID=显示名:类型`。ID 用稳定可读的 slug，别用序号。
质量档 `probe`（探路）/ `standard`（标准）/ `deep`（纵深），不确定就 `standard`。

## 2. 声明机制问题

**纵深要服务于具体问题，否则就是为了多跳而多跳。** 先把「哪条链路必须能走通」写清楚：

```bash
fg session set --mechanism-question '{
  "id": "M1",
  "question": "锂价如何经采购成本与分部毛利传导到盈利与估值",
  "from": "E-lithium", "to": "E-catl-a",
  "min_hops": 5, "independent": 2,
  "layers": ["supply_operation", "financial_capital", "expectation_valuation"]
}'
```

`min_hops` 应该来自你对机制环节数的判断，不是想要的数字。锂价到股价确实要过采购成本、
分部毛利、合并利润、盈利预期这几道，所以 5 跳是自然的。

## 3. 检索并落盘

```bash
fg search "宁德时代2025年年报合并利润表全部科目与数值，不要摘要" --tag 财务
```

```json
{
  "ok": true,
  "harvest": {
    "id": "h-0001",
    "answer_chars": 4675,
    "data_chars": 30369,
    "data_cells": 684,
    "lazysearch_tools": ["<这次实际调用过的检索工具>"],
    "source_tables": ["<这次取数用到的库.表>"]
  }
}
```

注意 `answer_chars` 4,675 vs `data_chars` 30,369 —— **原始数据是结论的 6 倍**，而且识别出
684 个数据单元格待挖。这些都已落盘，不必进上下文。

写查询的关键：要求穷尽（「全部科目」「不要摘要」）、把口径写进问题、有歧义要求列全候选。
数据源选择与提问法细则在 `skills/finkg/references/LAZYSEARCH.md`。

## 4. 分层查看这次收割

```bash
fg harvest show h-0001 --part provenance      # 源库表 + 执行过的完整 SQL
fg harvest show h-0001 --part data --grep "营业|利润|费用"
fg harvest show h-0001 --part cells --unused-only --limit 20
```

`--part provenance` 里的 SQL 带**全部字段的中文别名**（一次利润表查询就有 82 个科目名），
这是节点该有哪些属性的现成清单。

## 5. 挖成实体与事实

用模板起手（`--output` 让工具自己写文件，Windows 上比 shell 重定向可靠）：

```bash
fg template entity --output entities.json
fg template fact   --output facts.json
fg template fact-edge --output edges.json
```

改成真实内容。核心要求：`quote` 必须能在这次收割的原文里**逐字找到**。

```json
{
  "subject": "E-catl", "predicate": "营业总收入",
  "object": { "kind": "number", "value": 423701834000.0, "unit": "元", "currency": "CNY" },
  "period": { "kind": "duration", "start": "2025-01-01", "end": "2025-12-31", "label": "2025年报" },
  "basis": { "consolidation": "合并报表", "source_table": "<从 provenance 抄来的库.表>" },
  "epistemic": "reported", "harvest_id": "h-0001",
  "quote": "一、营业总收入 | 4,237.02 | 423,701,834,000.00",
  "target": { "kind": "prop", "node": "E-catl", "key": "财务.利润表.2025年报.营业总收入" }
}
```

引文摘**整行**比摘单个数字划算：一次覆盖三个单元格，也更利于回溯。

```bash
fg harvest mine h-0001 --entities entities.json --facts facts.json --done
```

```json
{
  "ok": false,
  "facts_added": 17,
  "problems": [
    { "level": "error", "ref": "F00016",
      "issue": "inference 类事实必须写 basis_fact_ids，说明它是从哪些已接受事实推出来的" }
  ],
  "usage": { "data_cells": 684, "used": 142, "open": 542, "use_ratio": 0.2076 }
}
```

工具当场告诉你两件事：哪条事实不合格，以及**这次收割还有 542 个单元格没交代**。

真的不需要的数据写明理由：

```bash
fg harvest dispose h-0001 --scope "数据状态" --reason "库内版本标记，不是业务事实"
fg harvest dispose h-0001 --scope "已赚保费" --reason "保险业专用科目，本公司为空"
```

## 6. 编译并查看

```bash
fg compile
fg node E-catl
fg usage
```

`fg node` 给出这个节点的完整详表：每个属性的值、单位、期间、来源事实、原文引文，以及进出边。

## 7. 看还缺什么

```bash
fg brief
```

```json
{
  "counts": { "harvests": 1, "facts": 17, "nodes": 9, "edges": 8 },
  "information_use": { "total_data_cells": 684, "use_ratio": 0.2076, "open": 542 },
  "thin_nodes": [ { "id": "E-ganfeng", "prop_count": 0 } ],
  "next": [
    "542 个数据单元格既没用也没交代，最欠的是 h-0001(21%)",
    "3 个业务节点属性少于 5 个，先补锚点一跳邻居"
  ]
}
```

`fg brief` 是每做完一件事都该跑一次的命令。它是渐进式披露的入口：告诉你现在该干什么，
而不必把整个会话状态读进上下文。

## 8. 补纵深

```bash
fg depth --min-hops 6
```

看 `weak_because`——它给出的是**为什么这条路径不算真纵深**：

| weak_because | 补法 |
| --- | --- |
| 只跨 2 个语义层 | 缺的层没检索，去补政策/预期/风险扇区 |
| 8 跳里只有 3 种关系 | 只沿一个维度展开，给链上实体补持股/政策/竞争边 |
| 2 跳没有事实支撑 | 针对那两跳专门检索 |
| 连续 3 跳都是推断 | 中间插入一个有披露支撑的节点 |

都不是「再接一跳」能解决的。细则见 `skills/finkg/references/DEPTH.md`。

## 9. 质量与装库

```bash
fg quality
```

`ok=false` 时会逐条给出问题与修法，按 `skills/finkg/references/QUALITY.md` 的顺序修
（证据类 error → 机制问题 → 未挖收割 → 锚点属性 → 边机制 → 纵深 → 覆盖广度）。

```bash
fg neo4j ensure-db
fg neo4j load
```

装载在单事务里完成整库替换 + 读回核对，核对不上整体回滚。质量报告 `ok=false` 时会拒绝装
生产库；确实要装一个中间快照看看形状，加 `--force`。

## 10. 在库里验证多跳

```bash
fg neo4j hop --from-id E-lithium --to-id E-catl-a --min-hops 5 --max-hops 12 --limit 5
```

返回逐跳的中文关系链、语义层链和机制链。**把机制链连起来读一遍**——读出来是一段站得住的
因果叙述，这条纵深就是真的。

Neo4j Browser 可视化：

```bash
fg neo4j grass          # 生成 browser.grass
```

浏览器打开 Neo4j Browser → `:use fg-<你的库名>` → 左侧 `:style` 面板拖入 `browser.grass`
→ `MATCH (n:FGNode)-[r]->(m) RETURN n, r, m LIMIT 300`。

开箱可用的查询集在 `skills/finkg/assets/cypher/`。

## 11. 导出交付

```bash
fg export --output ./delivery/catl --include-harvest
```

产出中文表头的 `nodes.csv` / `relationships.csv`、`graph.json`、`facts.jsonl`、
`entities.jsonl`、明文 `ledger.jsonl`、`reports/`、`browser.grass`，以及（可选）全部原始收割。

---

## 一直贯穿全程的一件事

**每个关键判断都要跟用户对齐。** 消歧不唯一、来源数值打架、多个扩展方向优先级、关键边只能
靠推断、要推翻已对齐的范围——这些都该停下来问，而且要摆出**当前判断 + 证据样本 + 2~3 个真实
取舍 + 你的建议 + 为什么现在必须定**。

```bash
fg align --stage expansion --question "…" --option "A…" --option "B…" \
  --recommendation "…" --why-now "…"
fg answer "用户的决定" --effect "我据此改了什么"
```

台账是明文 JSONL，随时可读可改。没有 Gate、没有签名、没有令牌。细则见
`skills/finkg/references/HITL.md`。

## 下一步

- 完整实战：[WALKTHROUGH.md](WALKTHROUGH.md)
- 配置与端点：[CONFIGURATION.md](CONFIGURATION.md)
- 出问题了：[TROUBLESHOOTING.md](TROUBLESHOOTING.md)
