# 实战：锂价如何传导到宁德时代的估值

一个完整的会话，从提问到能在 Neo4j 里走通 8 跳。示例数据在 [`examples/catl-lithium/`](../examples/catl-lithium/)，
可以直接拿去跑。

下文 `fg` = `python $FG`（见 [QUICKSTART.md](QUICKSTART.md)）。

---

## 第 0 步：先跟人对齐，别急着检索

用户说「帮我看看锂价对宁德时代的影响」。这句话里有三个未定项，都不该由 agent 替他定：

1. **要回答的到底是哪个问题？** 成本影响？毛利影响？股价影响？三者需要的图不一样。
2. **锚点是公司还是证券？** 财务口径挂公司，估值口径挂证券。混起来会让所有数据口径失真。
3. **用途是什么？** 研究、尽调还是持续监控——决定要不要多来源交叉验证、要不要覆盖历史多期。

这时候应该停下来问，并且摆出取舍：

> 「锂价影响」有三条不同的链路，需要的图差别很大：
> **(A) 只看成本**：锂价 → 采购成本 → 营业成本 → 毛利率。3~4 跳，一天能做完，
> 但回答不了"股价为什么没跟着动"。
> **(B) 看到盈利**：加上分部收入、费用、归母净利润。5~6 跳，需要挖分部数据。
> **(C) 看到估值**：再加一致预期、EPS 预期、股价。7~9 跳，需要分析师预期数据，
> 而且最后两跳只能是推断——预期到股价没有披露级证据。
>
> 我建议 **C**，因为你问的是"影响"，而市场层面的影响最终体现在估值上；
> 但要接受最后两跳标记为 `inference` 并写明失效条件。
> 现在必须定，因为它决定我要不要去检索分析师预期数据（多约 8 次检索）。

用户选了 C。记账：

```bash
fg session new "宁德时代锂价传导" \
  --center-question "锂价如何经采购成本与分部毛利传导到宁德时代的盈利与估值" \
  --anchor "E-catl=宁德时代:Company" \
  --anchor "E-catl-a=宁德时代A股:Stock" \
  --profile standard --purpose "研究"

fg align --stage scope \
  --question "锂价影响看到哪一层：成本 / 盈利 / 估值" \
  --option "A 只看成本，3~4 跳" --option "B 看到盈利，5~6 跳" --option "C 看到估值，7~9 跳" \
  --recommendation "C。你问的是影响，市场层面的影响体现在估值上" \
  --why-now "决定要不要检索分析师预期数据（多约 8 次检索）"

fg answer "选 C，看到估值" --effect "机制问题 M1 终点定为 E-catl-a；新增预期与估值扇区检索计划"
```

## 第 1 步：把机制问题写下来

**这是防止"为了多跳而多跳"的关键一步。** 先想清楚机制有几个环节，再定 `min_hops`：

```
锂价 → 赣锋（锂盐产出）→ 宁德（采购成本）→ 分部（收入毛利）→ 合并利润 → 一致预期 → 股价
        ①                  ②                 ③                ④           ⑤          ⑥
```

六个环节，所以 `min_hops: 5`（跳数 = 节点数 - 1，取保守值）。

```bash
fg session set --mechanism-question '{
  "id": "M1",
  "question": "锂价如何经采购成本与分部毛利传导到盈利与估值",
  "from": "E-lithium", "to": "E-catl-a",
  "min_hops": 5, "independent": 2,
  "layers": ["supply_operation", "financial_capital", "expectation_valuation"]
}'

fg session set --mechanism-question '{
  "id": "M2",
  "question": "能量密度准入政策如何经技术路线改变碳酸锂需求结构",
  "from": "E-policy-density", "to": "E-lithium",
  "min_hops": 3, "independent": 2,
  "layers": ["policy_regulation", "demand_market", "supply_operation"]
}'
```

M2 是为链条补上「起因」——只有 M1 的话，图会从锂价凭空开始，读者会问"锂价为什么变"。

## 第 2 步：按扇区铺开检索

十个扇区（见 `references/LAZYSEARCH.md`），每个至少一次。**宁可多问**——落盘成本很低，
漏掉一整类信息的代价很高。

```bash
# 身份与证券
fg search "解析宁德时代的证券实体，A股/港股候选都列出并说明区别" --tag 身份
# 财务报表（多期）
fg search "宁德时代2023/2024/2025年合并利润表全部科目与数值，不要摘要" --tag 财务
fg search "宁德时代2025年合并资产负债表全部科目与数值" --tag 财务
# 业务构成 ← 传导链的关键一环
fg search "宁德时代最新主营业务构成及对应营收、成本、毛利率、占比、同比增长率" --tag 业务
# 供应与采购
fg search "宁德时代主要供应商与碳酸锂采购情况、长协与定价方式" --tag 供应
fg search "赣锋锂业2025年锂盐板块收入、成本、毛利率与主要客户" --tag 供应
# 上游要素
fg search "电池级碳酸锂2024-2026月度价格、产量、开工率、库存" --tag 上游
# 所有权
fg search "宁德时代最新报告期前十大股东、持股数量、持股比例、股东性质" --tag 所有权
# 行情与估值
fg search "宁德时代A股最近60个交易日日线与市值、PE(TTM)、PB" --tag 行情
# 政策与事件
fg search "动力电池能量密度准入政策变化与宁德时代相关公告" --tag 政策
# 预期与风险
fg search "宁德时代分析师盈利预测与一致预期EPS；年报披露的主要风险因素" --tag 预期
```

跑完看一眼总量：

```bash
fg harvest list
```

```json
{ "count": 11, "unmined": 11, "total_data_cells": 4820 }
```

**4,820 个数据单元格待挖。** 这个数字就是后面「信息用尽」的分母。

## 第 3 步：先挖一次，把样本摆给人看

**不要一口气挖完 11 次收割。** 先挖一次，把 20 条左右的事实样本给用户看，确认切分粒度、
单位口径、属性命名对不对。错了改一次比错 400 条再改便宜得多。

```bash
fg harvest show h-0002 --part provenance     # 拿到 82 个中文科目名
fg harvest show h-0002 --part data --grep "营业|利润|费用"
```

写 `facts.json`，然后：

```bash
fg harvest mine h-0002 --entities entities.json --facts facts.json
fg node E-catl
```

把 `fg node` 的输出摆给用户，问：

> 利润表已经挖了 41 个科目进 `财务.利润表.2025年报.*`。三个口径问题请你定：
> **(1)** 数值我按原始单位「元」存（`423701834000.0`），也可以按「亿元」存（`4237.02`）。
> 存元的好处是与原文一致、不引入舍入；坏处是人读起来要数位数。我建议存元，
> 显示时再换算。
> **(2)** 「数据状态=3（已调整/最终）」和「数据状态=1（原始）」两个版本都在返回里。
> 我只取了 3。如果你要看重述影响，我把 1 也挖进来，两条事实用 `basis.data_status` 区分。
> **(3)** 「其中：研发费用」在利润表里出现两次（费用明细里 221.47 亿，另一处为空）。
> 我只取了有值那条，空的那条准备 `dispose` 掉，理由写「同一科目的重复列，本期无值」。
>
> 现在定，因为剩下 10 次收割要按同样规则挖。

## 第 4 步：批量挖掘，边挖边核账

按对齐后的规则挖完剩下的：

```bash
fg harvest mine h-0001 --facts facts-h1.json --done
fg harvest mine h-0003 --facts facts-h3.json --done
# …
fg usage
```

```json
{
  "total_data_cells": 4820, "used": 3912, "disposed": 604, "open": 304,
  "use_ratio": 0.8116, "accounted_ratio": 0.9369,
  "worst": [ { "harvest": "h-0009", "accounted_ratio": 0.61,
               "open_samples": [ { "column": "换手率(%)", "value": "0.83", … } ] } ]
}
```

`worst` 直接指出哪次收割挖得最差。行情类收割常常最差——60 个交易日 × 8 个字段 = 480 个
单元格，但通常只需要少数几个时点。这时候**批量 dispose 是正当的**，只要理由写清楚：

```bash
fg harvest dispose h-0009 --scope "换手率" \
  --reason "本图不做流动性分析，只保留最新收盘价与市值；日频换手率不入图"
```

## 第 5 步：编译、看形状、补纵深

```bash
fg compile
fg depth --min-hops 5 --case M1
```

第一次跑通常是这样：

```json
{
  "id": "M1", "paths_found": 3, "substantive_found": 0,
  "ok": false,
  "gaps": ["找到 3 条路径但没有一条算实质纵深"],
  "weak_samples": [{
    "hops": 6, "distinct_layers": 2, "distinct_relations": 3,
    "weak_because": [
      "只跨 2 个语义层，机制没有真正在层间传导",
      "2 跳没有事实支撑"
    ]
  }]
}
```

**读 `weak_because`，不要急着接新边。** 这里说的是两件具体的事：

1. 只跨 2 层 → 缺 `expectation_valuation`。因为分析师预期那次检索（h-0011）还没挖成边。
2. 2 跳没证据 → 是「分部 → 合并利润」和「合并利润 → 一致预期」两跳，当时是靠常识连的。

对应处理：

```bash
# 补第 1 项：把预期数据挖成边
fg harvest show h-0011 --part data --grep "一致预期|EPS|目标价"
fg harvest mine h-0011 --facts facts-expectation.json --done

# 补第 2 项：给那两跳找证据，或者诚实降级为 inference 并写依据
```

降级为推断时必须写 `basis_fact_ids` 和 `rule`，而且要在 `mechanism` 里写失效条件：

```json
{
  "epistemic": "inference",
  "basis_fact_ids": ["F00142", "F00151"],
  "rule": "分部收入与毛利率变化 → 合并营业利润 → 卖方盈利预测调整 → 一致预期EPS",
  "target": {
    "relation": "经分部毛利变化改变市场盈利预期",
    "layer": "expectation_valuation",
    "mechanism": "分部毛利率是卖方模型的核心输入，变化会先反映为预测调整再反映为股价；若公司同期发布指引覆盖该变化，此传导被指引替代",
    "attrs": { "传导时滞": "1-2个季度", "观察指标": "一致预期EPS", "失效条件": "公司发布覆盖性业绩指引" }
  }
}
```

再跑：

```bash
fg compile && fg depth --min-hops 5 --case M1
```

```json
{
  "id": "M1", "paths_found": 9, "substantive_found": 4,
  "independent_witnesses": 2, "max_substantive_hops": 8,
  "layers_covered": ["supply_operation","financial_capital","expectation_valuation",
                     "demand_market","legal_ownership","policy_regulation"],
  "ok": true
}
```

## 第 6 步：质量与结构

```bash
fg quality
```

典型的剩余问题与处理顺序（详见 `references/QUALITY.md`）：

| finding | 处理 |
| --- | --- |
| `2-core 只占 18%、桥边率 82%` | 图基本是链。给锂价→毛利这条结论找第二条通路：经「原材料成本占比」这个分部属性走另一条边 |
| `7 个业务节点只有名字没有任何属性` | 外围供应商。要么补工商与财务，要么删掉——空壳节点拉低可分析率且对纵深无贡献 |
| `conflicts: 营业总收入 2025年报 有两个值` | 一个来自东财表、一个来自中心财务库。**不要静默取一个**，`fg align` 摆给用户判口径 |

## 第 7 步：装库并验证

```bash
fg neo4j ensure-db
fg neo4j load
fg neo4j hop --from-id E-lithium --to-id E-catl-a --min-hops 5 --max-hops 12 --limit 3
```

```
电池级碳酸锂 ─构成锂盐板块主要产出品种──→ 赣锋锂业       [供给与运营]
赣锋锂业     ─向客户长协供应电池级碳酸锂→ 宁德时代       [供给与运营]
宁德时代     ─由动力电池分部贡献主营收入→ 动力电池分部   [财务与资本]
动力电池分部 ─经分部毛利变化改变盈利预期→ 宁德时代A股    [预期与估值]
                                          跳数 4 · 跨 3 层
```

**把机制链连起来读一遍**：碳酸锂是赣锋锂盐板块的主要产出，价格决定其对下游报价能力 →
长协指数联动使锂价经采购成本进入宁德时代营业成本 → 分部收入与毛利率是合并利润的主要来源 →
分部毛利率是卖方模型核心输入，变化先反映为预测调整再反映为股价。

这是一段站得住的因果叙述，所以这条纵深是真的。如果读起来跳跃或某跳机制是空的，
那就是还要补的地方。

再看加上政策起因之后的完整链条：

```bash
fg neo4j hop --from-id E-policy-density --to-id E-catl-a --min-hops 6
```

## 第 8 步：交付

```bash
fg export --output ./delivery/catl-lithium --include-harvest
```

交付包里的 `ledger.jsonl` 是这次会话的完整决策记录——对方能看到每个口径判断是怎么定的、
谁定的、据此改了什么。这比一份没有过程的图有用得多。

---

## 回头看：哪几步最容易被跳过

| 容易跳过的 | 跳过的代价 |
| --- | --- |
| 第 0 步跟人对齐范围 | 做完才发现回答的不是用户要的问题 |
| 第 1 步写机制问题 | 纵深变成盲目扩图，最后有跳数没机制 |
| 第 3 步先挖一次给人看样本 | 口径错了要重做几百条事实 |
| 补链条的「起因」（M2） | 图从中段开始，读者第一个问题就答不上 |
| `dispose` 写理由 | 信息利用率上不去，且分不清"没用"和"用不上" |
| 读 `weak_because` 再补 | 盲目接边，跳数涨了但仍判非实质 |
