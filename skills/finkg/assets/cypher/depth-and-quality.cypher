// 纵深与边质量 —— 在库里直接检验"是不是为了多跳而多跳"

// ============ 边质量 ============

// 1. 缺机制的边 —— 纵深实际断在这些位置
MATCH ()-[r]->()
WHERE r.机制 IS NULL OR trim(r.机制) = ""
RETURN type(r) AS 关系, count(*) AS 数量
ORDER BY 数量 DESC;

// 2. 属性过少的边 —— 只知道"有关系"，无法比较强弱与时点
MATCH (a:FGNode)-[r]->(b:FGNode)
WITH a, r, b, size([k IN keys(r) WHERE NOT k STARTS WITH "_"
                    AND NOT k IN ["id","relation","机制","语义层","认识状态","期间","置信度","原文"]]) AS 业务属性数
WHERE 业务属性数 <= 1
RETURN a.caption AS 起点, type(r) AS 关系, b.caption AS 终点, 业务属性数
ORDER BY 业务属性数 LIMIT 30;

// 3. 空泛关系名（应为 0）
MATCH ()-[r]->()
WHERE type(r) IN ["相关","有关","关联","关系","影响","联系","涉及","对应"]
RETURN type(r) AS 空泛关系, count(*) AS 数量;

// 4. 推断类边占比 —— 过高说明图靠猜撑起来
MATCH ()-[r]->()
RETURN coalesce(r.认识状态, '(未标注)') AS 认识状态, count(*) AS 边数
ORDER BY 边数 DESC;

// ============ 多跳路径 ============

// 5. 指定两端的深链，带逐跳机制。可变长度上下界必须是字面量
MATCH p = (a:FGNode {id: "E-lithium"})-[*5..12]-(b:FGNode {id: "E-catl-a"})
RETURN [n IN nodes(p) | n.caption]        AS 路径节点,
       [r IN relationships(p) | type(r)]   AS 关系链,
       [r IN relationships(p) | r.语义层]  AS 语义层链,
       [r IN relationships(p) | r.机制]    AS 机制链,
       length(p)                           AS 跳数
ORDER BY 跳数 DESC LIMIT 5;

// 6. 只保留跨 ≥3 个语义层的路径 —— 过滤掉同层接龙
MATCH p = (a:FGNode {id: "E-lithium"})-[*5..12]-(b:FGNode {id: "E-catl-a"})
WITH p, [r IN relationships(p) | r.语义层] AS 层链
UNWIND 层链 AS 层
WITH p, 层链, count(DISTINCT 层) AS 跨层数
WHERE 跨层数 >= 3
RETURN [n IN nodes(p) | n.caption] AS 路径节点, 层链, 跨层数, length(p) AS 跳数
ORDER BY 跳数 DESC, 跨层数 DESC LIMIT 10;

// 7. 只保留关系类型足够多样的路径 —— 过滤掉"同一种关系重复接龙"
MATCH p = (a:FGNode {id: "E-lithium"})-[*5..12]-(b:FGNode {id: "E-catl-a"})
WITH p, [r IN relationships(p) | type(r)] AS 关系链, length(p) AS 跳数
UNWIND 关系链 AS 关系
WITH p, 关系链, 跳数, count(DISTINCT 关系) AS 关系种数
WHERE 关系种数 >= toInteger(ceil(跳数 / 2.0))
RETURN [n IN nodes(p) | n.caption] AS 路径节点, 关系链, 关系种数, 跳数
ORDER BY 跳数 DESC LIMIT 10;

// 8. 每一跳都有事实支撑的路径
MATCH p = (a:FGNode {id: "E-lithium"})-[*5..12]-(b:FGNode {id: "E-catl-a"})
WHERE all(r IN relationships(p) WHERE size(coalesce(r._fact_ids, [])) > 0
                                 AND trim(coalesce(r.机制, "")) <> "")
RETURN [n IN nodes(p) | n.caption] AS 路径节点,
       [r IN relationships(p) | type(r)] AS 关系链, length(p) AS 跳数
ORDER BY 跳数 DESC LIMIT 10;

// 9. 从锚点出发的距离分布 —— 走不到的部分是游离资料
MATCH (a:FGNode {id: "E-catl"})
MATCH p = shortestPath((a)-[*1..12]-(b:FGNode))
WHERE a <> b
RETURN length(p) AS 距离, count(b) AS 节点数
ORDER BY 距离;

// 10. 锚点走不到的节点
MATCH (b:FGNode)
WHERE NOT EXISTS { MATCH (a:FGNode {id: "E-catl"})-[*1..12]-(b) } AND b.id <> "E-catl"
RETURN b.kind AS 类型, collect(b.caption)[..20] AS 不可达节点;

// 边不重叠的独立见证在 Cypher 里不好表达，用 fg depth 算：
//   fg depth --min-hops 6        → independent_at_or_above / weak_because
