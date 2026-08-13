"""纵深分析：多跳路径、独立见证、跨层多样性，以及"是不是为了多跳而多跳"的判据。

只有业务实体之间的边才算跳。Document / Observation / Metric 这类元节点即使存在，
也不为跳数贡献任何长度 —— 否则很容易靠"公司→文档→观测→指标→公司"把 4 跳灌成 10 跳。

一条长路径要算真纵深，必须同时满足：
  1. 跨越足够多的语义层（机制在不同层之间真的传导了）；
  2. 关系类型有足够多样性（不是同一种关系重复接龙）；
  3. 每一跳都有事实支撑，且连续推断跳不超过 2；
  4. 至少还有一条边不重叠的独立见证路径（不是同一根主干换个尾巴）。
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque

from fgmodel import BUSINESS_KINDS, SEMANTIC_LAYERS

MAX_PATHS = 4000
MAX_STEPS = 400_000
SOFT_INFERENCE_RUN = 2


# --------------------------------------------------------------------------
# 图视图
# --------------------------------------------------------------------------
def business_view(graph: dict) -> dict:
    kinds = {n["id"]: n["kind"] for n in graph["nodes"]}
    captions = {n["id"]: n.get("caption") or n["id"] for n in graph["nodes"]}
    keep = {nid for nid, kind in kinds.items() if kind in BUSINESS_KINDS}
    out_adj, in_adj, undirected = defaultdict(list), defaultdict(list), defaultdict(set)
    edges = []
    for edge in graph["edges"]:
        if edge["from"] not in keep or edge["to"] not in keep:
            continue
        edges.append(edge)
        out_adj[edge["from"]].append(edge)
        in_adj[edge["to"]].append(edge)
        undirected[edge["from"]].add(edge["to"])
        undirected[edge["to"]].add(edge["from"])
    return {"nodes": keep, "kinds": kinds, "captions": captions, "edges": edges,
            "out": out_adj, "in": in_adj, "undirected": undirected}


def _traversal(view: dict, directed: bool):
    if directed:
        return lambda nid: [(e["to"], e) for e in view["out"].get(nid, [])]
    def both(nid):
        items = [(e["to"], e) for e in view["out"].get(nid, [])]
        items += [(e["from"], e) for e in view["in"].get(nid, [])]
        return items
    return both


# --------------------------------------------------------------------------
# 路径枚举
# --------------------------------------------------------------------------
def enumerate_paths(view: dict, start: str, targets=None, min_hops: int = 1,
                    max_hops: int = 12, directed: bool = False,
                    cap: int = MAX_PATHS) -> list:
    """DFS 枚举简单路径（不重复节点）。带上限，避免在稠密图上爆炸。"""
    if start not in view["nodes"]:
        return []
    step = _traversal(view, directed)
    target_set = set(targets) if targets else None
    found, steps = [], 0
    stack = [(start, [start], [])]
    while stack:
        node, nodes_seen, edges_seen = stack.pop()
        if len(edges_seen) >= min_hops and (target_set is None or node in target_set) \
                and node != start:
            found.append({"nodes": list(nodes_seen), "edges": list(edges_seen)})
            if len(found) >= cap:
                break
        if len(edges_seen) >= max_hops:
            continue
        for nxt, edge in step(node):
            steps += 1
            if steps > MAX_STEPS:
                stack = []
                break
            if nxt in nodes_seen:
                continue
            stack.append((nxt, nodes_seen + [nxt], edges_seen + [edge]))
    return found


# --------------------------------------------------------------------------
# 单条路径的实质性判定
# --------------------------------------------------------------------------
def describe_path(view: dict, path: dict) -> dict:
    edges = path["edges"]
    hops = len(edges)
    layers = [e["layer"] for e in edges if e["layer"] in SEMANTIC_LAYERS]
    relations = [e["relation"] for e in edges]
    unsupported = [e["id"] for e in edges if not e["fact_ids"]]
    no_mechanism = [e["id"] for e in edges if not str(e["mechanism"] or "").strip()]
    run, longest_run = 0, 0
    for edge in edges:
        if edge["epistemic"] in ("inference", "scenario", "estimate", "forecast"):
            run += 1
            longest_run = max(longest_run, run)
        else:
            run = 0
    distinct_layers = len(set(layers))
    distinct_relations = len(set(relations))
    needed_relations = -(-hops // 2)  # ceil(hops/2)
    reasons = []
    if hops >= 3 and distinct_layers < 3:
        reasons.append(f"只跨 {distinct_layers} 个语义层，机制没有真正在层间传导")
    if distinct_relations < needed_relations:
        reasons.append(f"{hops} 跳里只有 {distinct_relations} 种关系，像同一种关系重复接龙")
    if unsupported:
        reasons.append(f"{len(unsupported)} 跳没有事实支撑")
    if no_mechanism:
        reasons.append(f"{len(no_mechanism)} 跳没写机制")
    if longest_run > SOFT_INFERENCE_RUN:
        reasons.append(f"连续 {longest_run} 跳都是推断/预测，链条悬空")
    return {
        "hops": hops,
        "node_ids": path["nodes"],
        "captions": [view["captions"].get(n, n) for n in path["nodes"]],
        "steps": [{"from": e["from"], "to": e["to"], "relation": e["relation"],
                   "layer": e["layer"], "layer_zh": SEMANTIC_LAYERS.get(e["layer"], ""),
                   "mechanism": e["mechanism"], "epistemic": e["epistemic"],
                   "attrs": e["attrs"], "fact_ids": e["fact_ids"], "edge_id": e["id"]}
                  for e in edges],
        "distinct_layers": distinct_layers,
        "layers": sorted(set(layers)),
        "distinct_relations": distinct_relations,
        "unsupported_steps": unsupported,
        "steps_without_mechanism": no_mechanism,
        "longest_inference_run": longest_run,
        "substantive": not reasons,
        "weak_because": reasons,
        "edge_ids": [e["id"] for e in edges],
    }


def independent(described: list, limit: int = 25) -> list:
    """贪心挑边不重叠的路径族。共享同一主干的一堆尾巴只算一条。"""
    chosen, used = [], set()
    for item in sorted(described, key=lambda d: (-d["hops"], -d["distinct_layers"])):
        ids = set(item["edge_ids"])
        if ids & used:
            continue
        chosen.append(item)
        used |= ids
        if len(chosen) >= limit:
            break
    return chosen


# --------------------------------------------------------------------------
# 全局结构
# --------------------------------------------------------------------------
def _two_core(undirected: dict, nodes: set) -> set:
    degree = {n: len(undirected.get(n, set()) & nodes) for n in nodes}
    alive = set(nodes)
    queue = deque(n for n in alive if degree[n] < 2)
    while queue:
        node = queue.popleft()
        if node not in alive:
            continue
        alive.discard(node)
        for nb in undirected.get(node, set()):
            if nb in alive:
                degree[nb] -= 1
                if degree[nb] < 2:
                    queue.append(nb)
    return alive


def _bridges(undirected: dict, nodes: set) -> list:
    """迭代式 Tarjan 桥查找（避免深图递归爆栈）。"""
    disc, low, parent = {}, {}, {}
    bridges, timer = [], [0]
    for root in nodes:
        if root in disc:
            continue
        stack = [(root, iter(sorted(undirected.get(root, set()) & nodes)))]
        disc[root] = low[root] = timer[0]
        timer[0] += 1
        parent[root] = None
        while stack:
            node, it = stack[-1]
            advanced = False
            for nb in it:
                if nb not in disc:
                    parent[nb] = node
                    disc[nb] = low[nb] = timer[0]
                    timer[0] += 1
                    stack.append((nb, iter(sorted(undirected.get(nb, set()) & nodes))))
                    advanced = True
                    break
                if nb != parent.get(node):
                    low[node] = min(low[node], disc[nb])
            if not advanced:
                stack.pop()
                if stack:
                    up = stack[-1][0]
                    low[up] = min(low[up], low[node])
                    if low[node] > disc[up]:
                        bridges.append((up, node))
    return bridges


def _reach(view: dict, start: str, directed: bool) -> set:
    step = _traversal(view, directed)
    seen, queue = {start}, deque([start])
    while queue:
        node = queue.popleft()
        for nxt, _ in step(node):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def structure(graph: dict) -> dict:
    view = business_view(graph)
    nodes = view["nodes"]
    total = len(nodes)
    undirected = view["undirected"]
    degrees = {n: len(undirected.get(n, set()) & nodes) for n in nodes}
    isolated = [n for n, d in degrees.items() if d == 0]
    leaves = [n for n, d in degrees.items() if d == 1]
    core = _two_core(undirected, nodes)
    bridge_list = _bridges(undirected, nodes)
    anchors = [a for a in (graph.get("anchors") or []) if a in nodes]
    anchor_rows = []
    for anchor in anchors:
        undirected_reach = _reach(view, anchor, directed=False)
        directed_reach = _reach(view, anchor, directed=True)
        anchor_rows.append({
            "id": anchor, "caption": view["captions"].get(anchor, anchor),
            "degree": degrees.get(anchor, 0),
            "reach_undirected": len(undirected_reach) - 1,
            "reach_undirected_ratio": round((len(undirected_reach) - 1) / (total - 1), 4)
            if total > 1 else 0.0,
            "reach_directed": len(directed_reach) - 1,
        })
    hubs = sorted(degrees.items(), key=lambda kv: -kv[1])[:10]
    return {
        "business_nodes": total,
        "business_edges": len(view["edges"]),
        "isolated_nodes": isolated,
        "leaf_ratio": round(len(leaves) / total, 4) if total else 0.0,
        "two_core_ratio": round(len(core) / total, 4) if total else 0.0,
        "bridge_ratio": round(len(bridge_list) / len(view["edges"]), 4) if view["edges"] else 0.0,
        "kind_counts": dict(Counter(view["kinds"][n] for n in nodes)),
        "anchors": anchor_rows,
        "top_hubs": [{"id": n, "caption": view["captions"].get(n, n), "degree": d}
                     for n, d in hubs],
        "layers_used": sorted({e["layer"] for e in view["edges"] if e["layer"] in SEMANTIC_LAYERS}),
    }


# --------------------------------------------------------------------------
# 机制问题与自动深链
# --------------------------------------------------------------------------
def answer_case(graph: dict, case: dict) -> dict:
    view = business_view(graph)
    min_hops = int(case.get("min_hops") or 2)
    max_hops = int(case.get("max_hops") or max(min_hops + 4, 12))
    directed = bool(case.get("directed"))
    want = int(case.get("independent") or 2)
    raw = enumerate_paths(view, case.get("from"), [case.get("to")] if case.get("to") else None,
                          min_hops=min_hops, max_hops=max_hops, directed=directed)
    described = [describe_path(view, p) for p in raw]
    strong = [d for d in described if d["substantive"]]
    picked = independent(strong, limit=max(want, 5))
    required_layers = [l for l in (case.get("layers") or []) if l in SEMANTIC_LAYERS]
    covered = sorted({l for d in picked for l in d["layers"]})
    missing_layers = [l for l in required_layers if l not in covered]
    verdict = []
    if not described:
        verdict.append(f"从 {case.get('from')} 到 {case.get('to')} 找不到任何 ≥{min_hops} 跳的业务路径")
    elif not strong:
        verdict.append(f"找到 {len(described)} 条路径但没有一条算实质纵深："
                       + "；".join(described[0]["weak_because"]) if described else "")
    if len(picked) < want:
        verdict.append(f"独立见证只有 {len(picked)} 条，要求 {want} 条边不重叠的路径")
    if missing_layers:
        verdict.append("缺少要求覆盖的语义层：" +
                       "、".join(SEMANTIC_LAYERS[l] for l in missing_layers))
    return {
        "id": case.get("id"), "question": case.get("question", ""),
        "from": case.get("from"), "to": case.get("to"),
        "min_hops": min_hops, "want_independent": want,
        "paths_found": len(described),
        "substantive_found": len(strong),
        "independent_witnesses": len(picked),
        "max_hops_reached": max((d["hops"] for d in described), default=0),
        "max_substantive_hops": max((d["hops"] for d in strong), default=0),
        "layers_covered": covered,
        "missing_layers": missing_layers,
        "witnesses": picked[:max(want, 3)],
        "weak_samples": [d for d in described if not d["substantive"]][:3],
        "ok": not verdict,
        "gaps": [v for v in verdict if v],
    }


def deep_chains(graph: dict, min_hops: int = 6, limit: int = 20,
                from_ids: list | None = None) -> dict:
    """从锚点出发找最深的实质路径。用于回答"这张图到底有多深"。"""
    view = business_view(graph)
    starts = [s for s in (from_ids or graph.get("anchors") or []) if s in view["nodes"]]
    if not starts:
        degrees = {n: len(view["undirected"].get(n, set())) for n in view["nodes"]}
        starts = [n for n, _ in sorted(degrees.items(), key=lambda kv: -kv[1])[:3]]
    described = []
    for start in starts:
        for path in enumerate_paths(view, start, None, min_hops=min_hops,
                                    max_hops=max(min_hops + 6, 14), directed=False,
                                    cap=MAX_PATHS // max(len(starts), 1)):
            described.append(describe_path(view, path))
    strong = [d for d in described if d["substantive"]]
    picked = independent(strong, limit=limit)
    buckets = Counter()
    for item in picked:
        buckets[item["hops"]] += 1
    return {
        "starts": starts,
        "min_hops": min_hops,
        "paths_examined": len(described),
        "substantive": len(strong),
        "independent": len(picked),
        "deepest_hops": max((d["hops"] for d in strong), default=0),
        "hop_histogram": dict(sorted(buckets.items())),
        "independent_at_or_above": {
            str(k): sum(1 for d in picked if d["hops"] >= k)
            for k in (6, 8, 10, 12)
        },
        "witnesses": picked[:limit],
    }
