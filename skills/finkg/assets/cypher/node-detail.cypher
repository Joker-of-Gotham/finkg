// 节点详表 —— 把某个公司/股票节点当成一份可读的档案来查
// 把 "E-catl" 换成你的节点 id

// 1. 全部业务属性（按维度组排序，_ 开头的系统属性排除）
MATCH (n:FGNode {id: "E-catl"})
UNWIND [k IN keys(n) WHERE NOT k STARTS WITH "_"] AS 属性
RETURN 属性, n[属性] AS 值
ORDER BY 属性;

// 2. 只看财务维度
MATCH (n:FGNode {id: "E-catl"})
UNWIND [k IN keys(n) WHERE k STARTS WITH "财务."] AS 科目
RETURN 科目, n[科目] AS 数值
ORDER BY 科目;

// 3. 维度组覆盖情况
MATCH (n:FGNode {id: "E-catl"})
RETURN n.caption AS 节点, n._prop_count AS 属性数, n._prop_groups AS 已覆盖维度组;

// 4. 同一指标的多期对比（属性名里带报告期，所以可以横向拉出来）
MATCH (n:FGNode {id: "E-catl"})
UNWIND [k IN keys(n) WHERE k CONTAINS "营业总收入"] AS 属性
RETURN 属性, n[属性] AS 数值
ORDER BY 属性;

// 5. 这个节点的进出边 —— 关系名连起来读，应该像一段业务描述
MATCH (n:FGNode {id: "E-catl"})-[r]->(m:FGNode)
RETURN "出" AS 方向, type(r) AS 关系, m.caption AS 对端,
       r.语义层 AS 语义层, r.机制 AS 机制, r.期间 AS 期间
UNION
MATCH (n:FGNode {id: "E-catl"})<-[r]-(m:FGNode)
RETURN "入" AS 方向, type(r) AS 关系, m.caption AS 对端,
       r.语义层 AS 语义层, r.机制 AS 机制, r.期间 AS 期间;

// 6. 一跳邻居的实心程度 —— 邻居都是空壳说明扩图只连了名字
MATCH (n:FGNode {id: "E-catl"})--(m:FGNode)
RETURN m.caption AS 邻居, m.kind AS 类型, m._prop_count AS 属性数
ORDER BY 属性数 DESC;

// 属性的单位/币种/期间/口径/来源收割存在 n._prop_meta（JSON 字符串）里。
// 不想手工解析就直接跑：fg node E-catl —— 它给结构化版本并附原文引文。
