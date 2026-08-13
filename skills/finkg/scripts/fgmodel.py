"""事实 / 实体 / 图编译 + 质量度量。

度量的是**内容质量**，不是格式合规：
  - 这条事实的数值能不能被人真正使用（有没有单位、币种、期间、口径）？
  - 这个节点作为公司/股票，属性是不是真的丰富到能支撑判断？
  - 这条边除了"有关"之外，有没有说清机制、带上可分析的量化属性？
  - 检索回来的数据单元格，有多少真的落进了图里？
缺字段只会让某条记录在报告里显示为"信息不完整"，不会因为"少一个 key"而报错。
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

# 业务实体类型：参与业务纵深计数。可扩展，未知类型只提示不拒绝。
BUSINESS_KINDS = {
    "Company", "Stock", "Bond", "Listing", "Exchange", "Institution", "Fund",
    "Person", "Product", "Technology", "Facility", "Commodity", "Industry",
    "Segment", "Customer", "Supplier", "Contract", "Project", "Region",
    "Regulator", "Policy", "Event", "Risk", "Index", "Currency", "Expectation",
}
# 非业务类型：可以存在，但不为"多跳纵深"贡献跳数（防止用文档/观测节点凑长度）。
META_KINDS = {"Document", "Source", "Harvest", "Observation", "Metric", "Note"}

SEMANTIC_LAYERS = {
    "policy_regulation": "政策与监管",
    "legal_ownership": "法律与所有权",
    "supply_operation": "供给与运营",
    "demand_market": "需求与终端市场",
    "firm_action": "公司行为与战略",
    "financial_capital": "财务与资本",
    "expectation_valuation": "预期与估值",
    "risk_feedback": "风险与二阶反馈",
}

EPISTEMIC = {"reported", "observed", "estimate", "forecast", "inference",
             "scenario", "disputed"}

# 这些词单独作关系名等于什么都没说，会让图不可分析。
VAGUE_RELATIONS = {
    "相关", "有关", "关联", "关系", "影响", "联系", "涉及", "对应", "相关性",
    "关联关系", "有关系", "有联系", "相互影响", "存在关系", "存在关联",
    "related", "affects", "linked", "associated", "connected", "relates_to",
}
# 关系类型是给 Browser 图例和路径阅读用的：必须是短短语或词，不是句子。
MIN_RELATION_LEN = 2
MAX_RELATION_LEN = 8
_HAN = re.compile(r"[\u4e00-\u9fff]")
_WS = re.compile(r"\s+")
_REL_PUNCT = re.compile(r"[。；，、？！：:;,.!?\s]")
_REL_SENTENCE_PREFIX = re.compile(r"^(向|由|经|以|对|将|把|被)")


def has_han(text) -> bool:
    return bool(_HAN.search(str(text or "")))


def flat(text) -> str:
    """去掉空白与千分位，用于宽容比对（内容差异仍然抓得住）。"""
    return _WS.sub("", str(text or "")).replace(",", "").replace("，", "")


def relation_problems(rel, ref: str = "") -> list:
    """关系名必须是 2–8 字中文短语。句子、空泛词、标点都会让 Browser 没法读。"""
    text = str(rel or "").strip()
    out = []
    if not text:
        out.append({"level": "error", "ref": ref, "issue": "边没有 relation"})
        return out
    if text in VAGUE_RELATIONS:
        out.append({"level": "error", "ref": ref,
                    "issue": f"关系「{text}」太空泛，读者无法据此做任何分析；"
                             "改成 2–8 字短语，如「持股」「长协供应」「准入约束」"})
        return out
    if _REL_PUNCT.search(text):
        out.append({"level": "error", "ref": ref,
                    "issue": f"关系「{text}」含标点或空白，应是连续短语"})
    n = len(text)
    if n < MIN_RELATION_LEN:
        out.append({"level": "error", "ref": ref,
                    "issue": f"关系「{text}」太短，至少两个字，如「持股」「挂牌」"})
    elif n > MAX_RELATION_LEN:
        out.append({"level": "error", "ref": ref,
                    "issue": f"关系「{text}」是 {n} 字的句子，Browser 图例读不动；"
                             "改成 2–8 字短语，对象/品类/限定写进 attrs"})
    elif n >= 6 and _REL_SENTENCE_PREFIX.match(text):
        out.append({"level": "warn", "ref": ref,
                    "issue": f"关系「{text}」像在写句子；主语是 from、宾语是 to，"
                             "关系名只要动作本身，如「供应」而不是「向客户供应某某」"})
    if not has_han(text):
        out.append({"level": "warn", "ref": ref,
                    "issue": f"关系「{text}」不是中文，用户看不懂"})
    return out


def relation_is_label(rel) -> bool:
    """能否当 Neo4j 关系类型：短、具体、无标点。"""
    return not any(p["level"] == "error" for p in relation_problems(rel))


def quote_found(quote: str, haystack: str) -> bool:
    """quote 是否真的出自这次收割。空白与千分位差异宽容，内容差异不宽容。"""
    if not quote:
        return False
    if quote in haystack:
        return True
    return flat(quote) in flat(haystack)


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------
def validate_entities(entities: list) -> dict:
    problems, seen = [], {}
    for pos, ent in enumerate(entities):
        ref = ent.get("id") or f"#{pos}"
        if not ent.get("id"):
            problems.append({"level": "error", "ref": ref, "issue": "缺 id，无法被事实引用"})
        elif ent["id"] in seen:
            problems.append({"level": "error", "ref": ref,
                             "issue": f"id 重复（与第 {seen[ent['id']]} 条冲突）"})
        else:
            seen[ent["id"]] = pos
        if not ent.get("name") and not ent.get("short"):
            problems.append({"level": "error", "ref": ref, "issue": "既没有 name 也没有 short，节点无法显示"})
        kind = ent.get("kind")
        if not kind:
            problems.append({"level": "error", "ref": ref, "issue": "缺 kind，无法判断它是公司还是证券还是事件"})
        elif kind not in BUSINESS_KINDS and kind not in META_KINDS:
            problems.append({"level": "note", "ref": ref,
                             "issue": f"kind「{kind}」不在推荐词表内；如果它是业务实体请确认拼写，"
                                      f"否则纵深统计不会把它算作业务跳"})
    return {"count": len(entities), "problems": problems}


def _object_problems(fact: dict, ref: str) -> list:
    obj = fact.get("object") or {}
    kind = obj.get("kind")
    out = []
    if kind == "number":
        if obj.get("value") is None:
            out.append({"level": "error", "ref": ref, "issue": "数值事实没有 value"})
        if not obj.get("unit") and not obj.get("currency") and not obj.get("percent"):
            out.append({"level": "error", "ref": ref,
                        "issue": "数值缺单位/币种/百分比标记，读到的人无法判断量级，等于不可用"})
        if not fact.get("period"):
            out.append({"level": "error", "ref": ref,
                        "issue": "数值缺 period（时点或区间），无法判断这是哪一期的数"})
    elif kind == "entity":
        if not obj.get("entity"):
            out.append({"level": "error", "ref": ref, "issue": "实体型对象没有 entity 指向"})
    elif kind == "text":
        if not str(obj.get("text") or "").strip():
            out.append({"level": "error", "ref": ref, "issue": "文本型对象为空"})
    elif kind is None:
        out.append({"level": "error", "ref": ref, "issue": "object 缺 kind（number/entity/text）"})
    return out


def _target_problems(fact: dict, ref: str, entity_ids: set) -> list:
    target = fact.get("target") or {}
    kind = target.get("kind")
    out = []
    if kind == "prop":
        if not target.get("key"):
            out.append({"level": "error", "ref": ref, "issue": "落到节点属性但没写 key"})
        node = target.get("node") or fact.get("subject")
        if node and node not in entity_ids:
            out.append({"level": "error", "ref": ref, "issue": f"属性宿主 {node} 不在实体表里"})
    elif kind == "edge":
        out.extend(relation_problems(target.get("relation"), ref))
        for side in ("from", "to"):
            node = target.get(side)
            if not node:
                out.append({"level": "error", "ref": ref, "issue": f"边缺 {side} 端点"})
            elif node not in entity_ids:
                out.append({"level": "error", "ref": ref, "issue": f"边端点 {node} 不在实体表里"})
        if not str(target.get("mechanism") or "").strip():
            out.append({"level": "warn", "ref": ref,
                        "issue": "边没有 mechanism（这条关系凭什么成立、如何传导），纵深分析会断在这里"})
        if not (target.get("attrs") or {}):
            out.append({"level": "warn", "ref": ref,
                        "issue": "边没有任何属性（金额/比例/数量/期间/生效时间），无法进一步深入分析"})
        layer = target.get("layer")
        if layer and layer not in SEMANTIC_LAYERS:
            out.append({"level": "note", "ref": ref,
                        "issue": f"layer「{layer}」不在八层词表内，跨层纵深统计会忽略它"})
        elif not layer:
            out.append({"level": "note", "ref": ref, "issue": "边没有 layer，跨层纵深统计会忽略它"})
    elif kind in ("observation", "context"):
        pass
    elif kind is None:
        out.append({"level": "error", "ref": ref,
                    "issue": "target 缺 kind：这条事实要变成节点属性(prop)、边(edge)还是仅作背景(context)？"})
    return out


def validate_facts(facts: list, entities: list, harvest_text: dict) -> dict:
    """harvest_text: {harvest_id: 可检索文本}。缺项则跳过 quote 核验并提示。"""
    entity_ids = {e["id"] for e in entities if e.get("id")}
    fact_ids = {f["id"] for f in facts if f.get("id")}
    problems, seen = [], {}
    unverified = 0
    for pos, fact in enumerate(facts):
        ref = fact.get("id") or f"#{pos}"
        if not fact.get("id"):
            problems.append({"level": "error", "ref": ref, "issue": "缺 id"})
        elif fact["id"] in seen:
            problems.append({"level": "error", "ref": ref, "issue": "id 重复"})
        else:
            seen[fact["id"]] = pos
        subject = fact.get("subject")
        if not subject:
            problems.append({"level": "error", "ref": ref, "issue": "缺 subject"})
        elif subject not in entity_ids:
            problems.append({"level": "error", "ref": ref, "issue": f"subject {subject} 不在实体表里"})
        if not str(fact.get("predicate") or "").strip():
            problems.append({"level": "error", "ref": ref, "issue": "缺 predicate（这条事实在说主体的什么）"})
        problems += _object_problems(fact, ref)
        problems += _target_problems(fact, ref, entity_ids)

        epistemic = fact.get("epistemic") or "reported"
        if epistemic not in EPISTEMIC:
            problems.append({"level": "warn", "ref": ref,
                             "issue": f"epistemic「{epistemic}」不在 {sorted(EPISTEMIC)} 内"})
        if epistemic in ("inference", "estimate", "forecast", "scenario"):
            basis = fact.get("basis_fact_ids") or []
            if not basis:
                problems.append({"level": "error", "ref": ref,
                                 "issue": f"{epistemic} 类事实必须写 basis_fact_ids，说明它是从哪些已接受事实推出来的"})
            for dep in basis:
                if dep not in fact_ids:
                    problems.append({"level": "error", "ref": ref, "issue": f"basis {dep} 不存在"})
                if dep == fact.get("id"):
                    problems.append({"level": "error", "ref": ref, "issue": "basis 指向自己"})
            if not str(fact.get("rule") or "").strip():
                problems.append({"level": "warn", "ref": ref, "issue": f"{epistemic} 没写 rule（推断规则）"})

        harvest_id = fact.get("harvest_id")
        if not harvest_id:
            problems.append({"level": "error", "ref": ref,
                             "issue": "缺 harvest_id：这条事实是从哪次检索里来的？"})
        elif harvest_id not in harvest_text:
            unverified += 1
            problems.append({"level": "warn", "ref": ref,
                             "issue": f"harvest {harvest_id} 未落盘，quote 无法核验"})
        else:
            quote = fact.get("quote") or ""
            if not quote:
                problems.append({"level": "error", "ref": ref,
                                 "issue": "缺 quote：必须摘出原始返回里的那一段文字，否则无法回溯"})
            elif not quote_found(quote, harvest_text[harvest_id]):
                problems.append({"level": "error", "ref": ref,
                                 "issue": f"quote 在 {harvest_id} 的返回里找不到；不要凭记忆改写原文"})
    problems += _cycle_problems(facts)
    return {"count": len(facts), "problems": problems, "quote_unverifiable": unverified}


def _cycle_problems(facts: list) -> list:
    graph = {f["id"]: [d for d in (f.get("basis_fact_ids") or [])]
             for f in facts if f.get("id")}
    state, out = {}, []
    def visit(node, trail):
        if state.get(node) == "done":
            return
        if state.get(node) == "open":
            out.append({"level": "error", "ref": node,
                        "issue": "推断依赖成环: " + " -> ".join(trail + [node])})
            return
        state[node] = "open"
        for dep in graph.get(node, []):
            if dep in graph:
                visit(dep, trail + [node])
        state[node] = "done"
    for node in graph:
        visit(node, [])
    return out


# --------------------------------------------------------------------------
# 编译
# --------------------------------------------------------------------------
def compile_graph(meta: dict, entities: list, facts: list) -> dict:
    by_id = {e["id"]: e for e in entities if e.get("id")}
    nodes, edges = {}, []
    prop_conflicts = []

    def ensure(node_id):
        if node_id in nodes:
            return nodes[node_id]
        ent = by_id.get(node_id) or {"id": node_id, "kind": "Unknown"}
        nodes[node_id] = {
            "id": node_id, "kind": ent.get("kind") or "Unknown",
            "caption": ent.get("short") or ent.get("name") or node_id,
            "name": ent.get("name") or ent.get("short") or node_id,
            "aliases": ent.get("aliases") or [],
            "ids": dict(ent.get("ids") or {}),
            "note": ent.get("note") or "",
            "props": {}, "prop_facts": {}, "prop_meta": {},
            "harvest_ids": [], "fact_ids": [],
        }
        return nodes[node_id]

    for ent in entities:
        if ent.get("id") and ent.get("anchor"):
            ensure(ent["id"])
    for anchor in meta.get("anchors") or []:
        if isinstance(anchor, dict) and anchor.get("id") in by_id:
            ensure(anchor["id"])

    for fact in facts:
        target = fact.get("target") or {}
        kind = target.get("kind")
        harvest_id = fact.get("harvest_id")
        if kind == "prop":
            node = ensure(target.get("node") or fact.get("subject"))
            key = target.get("key") or fact.get("predicate")
            value = _prop_value(fact)
            if key in node["props"] and node["props"][key] != value:
                prop_conflicts.append({"node": node["id"], "key": key,
                                       "values": [node["props"][key], value],
                                       "facts": node["prop_facts"].get(key, []) + [fact.get("id")]})
            node["props"][key] = value
            node["prop_facts"].setdefault(key, []).append(fact.get("id"))
            node["prop_meta"][key] = {
                "epistemic": fact.get("epistemic") or "reported",
                "period": _period_label(fact.get("period")),
                "unit": (fact.get("object") or {}).get("unit"),
                "currency": (fact.get("object") or {}).get("currency"),
                "basis": fact.get("basis") or {},
                "known_at": fact.get("known_at"),
                "harvest_id": harvest_id,
            }
            _touch(node, fact, harvest_id)
        elif kind == "edge":
            if not (target.get("from") and target.get("to")):
                continue
            src, dst = ensure(target["from"]), ensure(target["to"])
            edges.append({
                "id": f"R{len(edges) + 1:05d}",
                "from": target["from"], "to": target["to"],
                "relation": target.get("relation") or "",
                "layer": target.get("layer") or "",
                "mechanism": target.get("mechanism") or "",
                "attrs": dict(target.get("attrs") or {}),
                "epistemic": fact.get("epistemic") or "reported",
                "period": _period_label(fact.get("period")),
                "confidence": fact.get("confidence") or "",
                "fact_ids": [fact.get("id")],
                "harvest_ids": [harvest_id] if harvest_id else [],
                "quote": (fact.get("quote") or "")[:400],
            })
            _touch(src, fact, harvest_id)
            _touch(dst, fact, harvest_id)
        else:
            subject = fact.get("subject")
            if subject in by_id:
                _touch(ensure(subject), fact, harvest_id)

    edges = _merge_edges(edges)
    for node in nodes.values():
        node["prop_count"] = len(node["props"])
        node["prop_groups"] = sorted({k.split(".")[0] for k in node["props"]})
    return {"schema": "finkg/1", "topic": meta.get("topic", ""),
            "as_of": meta.get("as_of", ""), "center_question": meta.get("center_question", ""),
            "anchors": [a.get("id") if isinstance(a, dict) else a
                        for a in (meta.get("anchors") or [])],
            "nodes": list(nodes.values()), "edges": edges,
            "prop_conflicts": prop_conflicts}


def _touch(node: dict, fact: dict, harvest_id) -> None:
    if fact.get("id") and fact["id"] not in node["fact_ids"]:
        node["fact_ids"].append(fact["id"])
    if harvest_id and harvest_id not in node["harvest_ids"]:
        node["harvest_ids"].append(harvest_id)


def _prop_value(fact: dict):
    obj = fact.get("object") or {}
    if obj.get("kind") == "number":
        return obj.get("value")
    if obj.get("kind") == "entity":
        return obj.get("entity")
    return obj.get("text")


def _period_label(period) -> str:
    if not period:
        return ""
    if isinstance(period, str):
        return period
    if period.get("label"):
        return str(period["label"])
    if period.get("kind") == "instant":
        return str(period.get("at") or "")
    start, end = period.get("start"), period.get("end")
    return f"{start}~{end}" if start or end else ""


def _merge_edges(edges: list) -> list:
    merged = {}
    for edge in edges:
        key = (edge["from"], edge["to"], edge["relation"], edge["period"])
        if key in merged:
            keep = merged[key]
            keep["fact_ids"] += [f for f in edge["fact_ids"] if f not in keep["fact_ids"]]
            keep["harvest_ids"] += [h for h in edge["harvest_ids"] if h not in keep["harvest_ids"]]
            keep["attrs"].update(edge["attrs"])
            keep["mechanism"] = keep["mechanism"] or edge["mechanism"]
            keep["layer"] = keep["layer"] or edge["layer"]
        else:
            merged[key] = edge
    out = list(merged.values())
    for pos, edge in enumerate(out, 1):
        edge["id"] = f"R{pos:05d}"
    return out


# --------------------------------------------------------------------------
# 质量度量
# --------------------------------------------------------------------------
def node_richness(graph: dict, anchors: list) -> dict:
    rows = []
    for node in graph["nodes"]:
        rows.append({
            "id": node["id"], "kind": node["kind"], "caption": node["caption"],
            "prop_count": node["prop_count"],
            "prop_groups": node["prop_groups"],
            "group_count": len(node["prop_groups"]),
            "identifier_count": len(node["ids"]),
            "fact_count": len(node["fact_ids"]),
            "is_anchor": node["id"] in anchors,
        })
    rows.sort(key=lambda r: (-r["prop_count"], r["id"]))
    business = [r for r in rows if r["kind"] in BUSINESS_KINDS]
    empty = [r for r in business if r["prop_count"] == 0]
    return {
        "nodes": rows,
        "anchor_rows": [r for r in rows if r["is_anchor"]],
        "business_node_count": len(business),
        "name_only_nodes": [r["id"] for r in empty],
        "median_prop_count": _median([r["prop_count"] for r in business]),
        "kind_counts": dict(Counter(r["kind"] for r in rows)),
    }


def edge_quality(graph: dict) -> dict:
    total = len(graph["edges"])
    vague, sentence, no_mech, no_attr, no_fact, non_han, layered = (
        [], [], [], [], [], [], Counter())
    attr_counts = []
    for edge in graph["edges"]:
        rel = (edge.get("relation") or "").strip()
        problems = relation_problems(rel, edge.get("id") or "")
        if any(p["level"] == "error" and "空泛" in p["issue"] for p in problems):
            vague.append(edge["id"])
        if any(p["level"] == "error" and ("句子" in p["issue"] or "标点" in p["issue"])
               for p in problems):
            sentence.append(edge["id"])
        if not has_han(rel):
            non_han.append(edge["id"])
        if not (edge.get("mechanism") or "").strip():
            no_mech.append(edge["id"])
        attr_counts.append(len(edge.get("attrs") or {}))
        if not edge.get("attrs"):
            no_attr.append(edge["id"])
        if not edge.get("fact_ids"):
            no_fact.append(edge["id"])
        if edge.get("layer") in SEMANTIC_LAYERS:
            layered[edge["layer"]] += 1
    analyzable = [e for e in graph["edges"]
                  if (e.get("mechanism") or "").strip() and e.get("attrs") and e.get("fact_ids")
                  and relation_is_label(e.get("relation"))]
    return {
        "edge_count": total,
        "analyzable_count": len(analyzable),
        "analyzable_ratio": _ratio(len(analyzable), total),
        "vague_relations": vague,
        "sentence_relations": sentence,
        "non_chinese_relations": non_han,
        "missing_mechanism": no_mech,
        "missing_attrs": no_attr,
        "missing_evidence": no_fact,
        "mean_attr_count": round(sum(attr_counts) / total, 2) if total else 0.0,
        "layer_counts": {SEMANTIC_LAYERS[k]: v for k, v in layered.items()},
        "layers_used": len(layered),
        "relation_vocabulary": len({e["relation"] for e in graph["edges"]}),
    }


def load_blockers(report: dict | None) -> list:
    """装库只拦证据完整性（引文找不到、端点悬空），不拦节点数/边数/跳数。"""
    if not report:
        return []
    return [f for f in report.get("findings", [])
            if f.get("level") == "error" and f.get("area") == "证据"]


def fact_quality(facts: list) -> dict:
    numeric = [f for f in facts if (f.get("object") or {}).get("kind") == "number"]
    complete = [f for f in numeric
                if ((f.get("object") or {}).get("unit") or (f.get("object") or {}).get("currency")
                    or (f.get("object") or {}).get("percent")) and f.get("period")]
    with_quote = [f for f in facts if str(f.get("quote") or "").strip()]
    by_epistemic = Counter(f.get("epistemic") or "reported" for f in facts)
    return {
        "fact_count": len(facts),
        "numeric_count": len(numeric),
        "numeric_usable_ratio": _ratio(len(complete), len(numeric)),
        "quoted_ratio": _ratio(len(with_quote), len(facts)),
        "by_epistemic": dict(by_epistemic),
        "distinct_harvests": len({f.get("harvest_id") for f in facts if f.get("harvest_id")}),
    }


def value_conflicts(facts: list) -> list:
    buckets = defaultdict(list)
    for fact in facts:
        obj = fact.get("object") or {}
        if obj.get("kind") != "number":
            continue
        key = (fact.get("subject"), fact.get("predicate"), _period_label(fact.get("period")),
               obj.get("unit"), obj.get("currency"))
        buckets[key].append(fact)
    out = []
    for key, group in buckets.items():
        values = {(f.get("object") or {}).get("value") for f in group}
        if len(values) > 1:
            out.append({"subject": key[0], "predicate": key[1], "period": key[2],
                        "values": sorted(str(v) for v in values),
                        "facts": [f.get("id") for f in group],
                        "harvests": sorted({f.get("harvest_id") for f in group if f.get("harvest_id")})})
    return out


def _ratio(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def _median(values: list):
    if not values:
        return 0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return round((ordered[mid - 1] + ordered[mid]) / 2, 1)
