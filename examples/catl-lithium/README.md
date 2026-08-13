# 样例：锂价传导到估值

一份**结构完整、可直接跑通**的最小数据集，用来验证安装是否正常、以及看懂事实与边该怎么写。

配合 [docs/WALKTHROUGH.md](../../docs/WALKTHROUGH.md) 阅读。

## 文件

| 文件 | 内容 |
| --- | --- |
| `entities.json` | 9 个实体：公司、证券、交易所、分部、商品、产业、政策 |
| `facts-props.json` | 9 条节点属性事实（财务、身份、报告口径） |
| `facts-edges.json` | 8 条边事实，构成一条 8 跳 6 层的传导链 |
| `mechanism-questions.json` | 2 个机制问题 |
| `harvest.txt` | 一份模拟的检索返回，供 `fg harvest add` 落盘 |

## 跑一遍

```bash
export FG="$HOME/.agents/skills/finkg/scripts/fg.py"     # 换成你的实际路径
cd <一个空目录>

python $FG session new "锂价传导样例" --id demo --profile probe \
  --center-question "锂价如何经采购成本与分部毛利传导到盈利与估值" \
  --anchor "E-catl=宁德时代:Company" --anchor "E-catl-a=宁德时代A股:Stock"

# 1) 先把"检索返回"落盘 —— 引文核验的比对面就来自这里
python $FG harvest add "宁德时代2025年年报合并利润表全部科目与数值" \
  --file <finkg仓库>/examples/catl-lithium/harvest.txt --channel manual

# 2) 挖成实体与事实
python $FG harvest mine h-0001 \
  --entities <…>/examples/catl-lithium/entities.json \
  --facts <…>/examples/catl-lithium/facts-props.json
python $FG fact add <…>/examples/catl-lithium/facts-edges.json --harvest h-0001

# 3) 机制问题
python $FG session set --mechanism-question "$(cat <…>/examples/catl-lithium/mechanism-questions.json | python -c 'import json,sys; print(json.dumps(json.load(sys.stdin)[0], ensure_ascii=False))')"

# 4) 编译并看
python $FG compile
python $FG node E-catl
python $FG depth --min-hops 5 --brief
python $FG usage
python $FG quality --brief
```

Windows 下把 `export` 换成 `$env:FG = "…"`，并先 `$env:PYTHONIOENCODING="utf-8"`。

## 预期结果

- `compile`：9 个节点、8 条边，无孤立节点
- `depth`：最深 7 跳（从锚点出发），跨 6 个语义层，1 条独立实质路径
- `quality`：证据没问题时 `ok=true`。样例节点少、是一条纯链（`two_core_ratio=0`）、
  信息利用率低——这些会以 `level: guide` 出现，告诉你下一步该问哪个扇区，
  **不会**把 `ok` 打成 false，也**不会**拦住装库。
- `usage`：会列出 `harvest.txt` 里还没用的科目

想看装库效果：

```bash
python $FG neo4j ensure-db
python $FG neo4j load
python $FG neo4j hop --from-id E-policy-density --to-id E-szse --min-hops 5
python $FG neo4j wipe --confirm --drop   # 用完清理
```

## 这份样例刻意展示的几件事

1. **引文必须能在落盘原文里找到。** 试着把某条事实的 `quote` 改一个数字，重跑
   `fg fact add`，会看到 `quote 在 h-0001 的返回里找不到`。
2. **属性用中文点分层级**，第一段是维度组，所以 `fg node` 能按组显示覆盖情况。
3. **每条边都是短中文短语 + 机制 + 量化属性。** 试着把某条边的 `mechanism` 删掉，
   `fg quality` 会把它列进 `missing_mechanism`，`fg depth` 会把经过它的路径判为非实质。
4. **推断边必须有依据。** `facts-edges.json` 里「预期传导」那条是 `inference`，
   带 `basis_fact_ids`（指向分部收入与毛利率两条事实）、`rule` 和失效条件。
   把 `basis_fact_ids` 清空会直接报错。注意它依赖 `facts-props.json` 按顺序先导入，
   这样自动分配的 ID 才是 `F00011` / `F00012`。
5. **纯链的结构风险。** 这 8 条边构成一条链，所以 `two_core_ratio=0`、`bridge_ratio=1.0`，
   质量报告会指出"任何一跳断掉整条链就断，没有任何交叉验证"。
