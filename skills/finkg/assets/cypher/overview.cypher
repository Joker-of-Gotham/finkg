// 图的整体形状 —— 先跑这一组，判断图是"实的"还是"大而空"
// 用法：fg neo4j query "<单条语句>"  或直接粘进 Neo4j Browser（先 :use <库名>）

// 1. 按类型看节点分布
MATCH (n:FGNode)
RETURN n.kind AS 类型, count(*) AS 数量
ORDER BY 数量 DESC;

// 2. 按关系类型看边分布（中文关系名直接是 type）
MATCH ()-[r]->()
RETURN type(r) AS 关系, count(*) AS 数量
ORDER BY 数量 DESC LIMIT 40;

// 3. 语义层覆盖 —— 缺哪一层，机制就缺哪个环节
MATCH ()-[r]->()
RETURN coalesce(r.语义层, '(未标注)') AS 语义层, count(*) AS 边数
ORDER BY 边数 DESC;

// 4. 属性最丰富的节点 —— 应该是锚点排在最前
MATCH (n:FGNode)
RETURN n.caption AS 节点, n.kind AS 类型,
       n._prop_count AS 属性数, n._prop_groups AS 维度组,
       size(n._fact_ids) AS 支撑事实数
ORDER BY 属性数 DESC LIMIT 25;

// 5. 只有名字的空壳节点 —— 要么补属性，要么删掉
MATCH (n:FGNode) WHERE coalesce(n._prop_count, 0) = 0
RETURN n.kind AS 类型, count(*) AS 数量, collect(n.caption)[..15] AS 样本
ORDER BY 数量 DESC;

// 6. 孤立节点 —— 对多跳没有任何贡献
MATCH (n:FGNode) WHERE NOT (n)--()
RETURN n.kind AS 类型, collect(n.caption) AS 孤立节点;

// 7. 度分布与枢纽 —— 若前几名度数远超其余，图是星形而非网络
MATCH (n:FGNode)
RETURN n.caption AS 节点, n.kind AS 类型,
       size([(n)--() | 1]) AS 度数
ORDER BY 度数 DESC LIMIT 20;
