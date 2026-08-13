"""Neo4j HTTP 事务桥（仅标准库）。

约定：
  - 每个会话一个专用数据库（Neo4j 企业版可 CREATE DATABASE；社区版回退到 neo4j 库）。
  - 每个节点同时带技术标签 `FGNode` 与语义标签（Company / Stock / Event ...），
    唯一约束建在 FGNode.id 上，所以 MATCH 走索引。
  - 关系类型直接用**中文动作**（反引号转义），Browser 里一眼能读懂。
  - 节点属性用参数化 map 写入（`SET n = $props`），所以中文点分属性名不需要转义。
  - 装载 = 单事务内「整库替换 + 读回核对」，核对不上就整体回滚。
"""
from __future__ import annotations

import base64
import ipaddress
import json
import re
import urllib.error
import urllib.request
from urllib.parse import quote, urlparse

import fgconfig

COMMON_LABEL = "FGNode"
_DB_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]*")


class Neo4jError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# 连接
# --------------------------------------------------------------------------
def _base_url(cfg: dict) -> str:
    fgconfig.require(cfg, "neo4j_url")
    raw = str(cfg.get("neo4j_url") or "").rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise Neo4jError("neo4j_url 必须是 http(s) origin，形如 http://<主机>:7474")
    if parsed.username or parsed.password or parsed.path not in ("", "/"):
        raise Neo4jError("neo4j_url 里不要带用户名/密码/路径")
    if parsed.scheme == "http":
        host = parsed.hostname.lower()
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host in ("localhost", "localhost.localdomain")
        if not loopback and not cfg.get("neo4j_allow_http_auth"):
            raise Neo4jError(
                "不愿意把 Basic Auth 发到非回环的明文 HTTP。局域网内属正常场景，"
                "在工作区 financial_graph.local.json 里设 \"neo4j_allow_http_auth\": true。")
    return raw


def validate_db(name: str, allow_system: bool = False) -> str:
    if not isinstance(name, str) or not 3 <= len(name) <= 63:
        raise Neo4jError(f"数据库名长度必须 3-63：{name!r}")
    if not _DB_NAME.fullmatch(name) or name.endswith((".", "-")):
        raise Neo4jError(f"数据库名只能用 ASCII 字母数字点横线且不以点横线结尾：{name!r}")
    if name.lower().startswith("system") and not (allow_system and name.lower() == "system"):
        raise Neo4jError("system 开头的库名是保留的")
    return name


def _request(cfg: dict, url: str, payload=None, method="POST", timeout=180) -> dict:
    base = _base_url(cfg)
    if not url.startswith(base + "/"):
        raise Neo4jError("拒绝把凭据发到别的 origin")
    fgconfig.require(cfg, "neo4j_user")
    password = fgconfig.require_password(cfg)
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    token = base64.b64encode(f"{cfg['neo4j_user']}:{password}".encode()).decode()
    req.add_header("Authorization", "Basic " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        return json.loads(data.decode("utf-8")) if data else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        if exc.code in (401, 403):
            raise Neo4jError(f"Neo4j 认证失败（HTTP {exc.code}）：检查 neo4j_user / neo4j_password") from exc
        raise Neo4jError(f"Neo4j HTTP {exc.code}: {detail}") from exc
    except OSError as exc:
        raise Neo4jError(f"连不上 Neo4j（{base}）：{exc}") from exc


def _tx_url(cfg: dict, database: str, suffix: str = "") -> str:
    validate_db(database, allow_system=True)
    return f"{_base_url(cfg)}/db/{quote(database, safe='')}/tx{suffix}"


def _check(out: dict) -> dict:
    if out.get("errors"):
        first = out["errors"][0]
        raise Neo4jError(f"{first.get('code')}: {first.get('message')}")
    return out


def run(cfg: dict, database: str, cypher: str, params: dict | None = None,
        timeout: int = 180) -> dict:
    """自动提交单条语句，返回 {columns, rows}。"""
    out = _check(_request(cfg, _tx_url(cfg, database, "/commit"),
                          {"statements": [{"statement": cypher, "parameters": params or {}}]},
                          timeout=timeout))
    result = (out.get("results") or [{}])[0]
    return {"columns": result.get("columns", []),
            "rows": [entry.get("row", []) for entry in result.get("data", [])]}


def run_many(cfg: dict, database: str, statements: list, timeout: int = 600) -> list:
    out = _check(_request(cfg, _tx_url(cfg, database, "/commit"),
                          {"statements": statements}, timeout=timeout))
    return out.get("results", [])


class Transaction:
    """显式事务：装载失败必须能整体回滚。"""

    def __init__(self, cfg: dict, database: str, timeout: int = 900):
        self.cfg, self.database, self.timeout = cfg, database, timeout
        self.tx_url = self.commit_url = None
        self.closed = False

    def __enter__(self):
        out = _check(_request(self.cfg, _tx_url(self.cfg, self.database),
                              {"statements": []}, timeout=self.timeout))
        commit_ref = out.get("commit")
        if not isinstance(commit_ref, str):
            raise Neo4jError("Neo4j 没有返回 commit URL")
        path = urlparse(commit_ref).path
        expected = f"/db/{quote(self.database, safe='')}/tx/"
        if not path.startswith(expected) or not path.endswith("/commit"):
            raise Neo4jError(f"Neo4j 返回了异常的事务 URL: {path}")
        self.commit_url = _base_url(self.cfg) + path
        self.tx_url = self.commit_url[: -len("/commit")]
        return self

    def __exit__(self, exc_type, exc, tb):
        if not self.closed:
            self.rollback()
        return False

    def run(self, statements: list) -> list:
        if self.closed or not self.tx_url:
            raise Neo4jError("事务已关闭")
        out = _check(_request(self.cfg, self.tx_url, {"statements": statements},
                             timeout=self.timeout))
        return out.get("results", [])

    def one(self, cypher: str, params: dict | None = None):
        results = self.run([{"statement": cypher, "parameters": params or {}}])
        rows = [e.get("row", []) for e in (results[0].get("data") or [])]
        return rows

    def commit(self) -> None:
        _check(_request(self.cfg, self.commit_url, {"statements": []}, timeout=self.timeout))
        self.closed = True

    def rollback(self) -> None:
        if self.closed or not self.tx_url:
            return
        try:
            _request(self.cfg, self.tx_url, method="DELETE", timeout=60)
        except Neo4jError:
            pass
        self.closed = True


# --------------------------------------------------------------------------
# 自检与建库
# --------------------------------------------------------------------------
def info(cfg: dict) -> dict:
    """服务端版本信息。**刻意不回主机地址** —— 这个结果会进 doctor 输出，
    而 doctor 输出应当可以直接贴进 issue。"""
    res = run(cfg, "neo4j", "CALL dbms.components() YIELD name, versions, edition "
                            "RETURN name, versions[0], edition", timeout=30)
    rows = res["rows"]
    name, version, edition = rows[0] if rows else ("?", "?", "?")
    return {"product": name, "version": version, "edition": edition}


def databases(cfg: dict) -> list:
    res = run(cfg, "system", "SHOW DATABASES YIELD name, currentStatus "
                             "RETURN name, currentStatus", timeout=30)
    return [{"name": r[0], "status": r[1]} for r in res["rows"]]


def ensure_database(cfg: dict, database: str) -> dict:
    validate_db(database)
    created, note = False, ""
    try:
        run(cfg, "system", f"CREATE DATABASE `{_esc(database)}` IF NOT EXISTS WAIT 60 SECONDS",
            timeout=120)
        created = True
    except Neo4jError as exc:
        note = str(exc)
        if "UnsupportedAdministrationCommand" in note or "not support" in note.lower():
            note = ("这个 Neo4j 不支持多数据库（社区版）。请把 neo4j_database 设成 neo4j，"
                    "或换企业版。")
            return {"database": database, "created": False, "constraints": [], "note": note}
        if "already exists" not in note.lower():
            raise
    statements = [
        {"statement": f"CREATE CONSTRAINT fg_node_id IF NOT EXISTS "
                      f"FOR (n:{COMMON_LABEL}) REQUIRE n.id IS UNIQUE"},
        {"statement": f"CREATE INDEX fg_node_caption IF NOT EXISTS "
                      f"FOR (n:{COMMON_LABEL}) ON (n.caption)"},
        {"statement": f"CREATE INDEX fg_node_kind IF NOT EXISTS "
                      f"FOR (n:{COMMON_LABEL}) ON (n.kind)"},
    ]
    run_many(cfg, database, statements, timeout=180)
    return {"database": database, "created": created,
            "constraints": ["fg_node_id", "fg_node_caption", "fg_node_kind"], "note": note}


# --------------------------------------------------------------------------
# 装载
# --------------------------------------------------------------------------
def _esc(identifier: str) -> str:
    return str(identifier).replace("`", "")


def _scalar(value):
    """Neo4j 属性只接受原始类型或同质数组；其他一律 JSON 字符串化。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        items = list(value)
        if all(isinstance(i, (str, int, float, bool)) for i in items):
            kinds = {type(i) for i in items}
            if len(kinds) > 1:
                return [str(i) for i in items]
            return items
        return json.dumps(items, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False)


def node_rows(graph: dict) -> dict:
    """按语义标签分组的节点写入行。"""
    grouped = {}
    for node in graph["nodes"]:
        props = {"id": node["id"], "kind": node["kind"], "caption": node["caption"],
                 "name": node.get("name") or node["caption"],
                 "_topic": graph.get("topic", ""),
                 "_prop_count": node.get("prop_count", len(node.get("props") or {})),
                 "_prop_groups": node.get("prop_groups") or [],
                 "_fact_ids": node.get("fact_ids") or [],
                 "_harvest_ids": node.get("harvest_ids") or [],
                 "_prop_sources": json.dumps(node.get("prop_facts") or {}, ensure_ascii=False),
                 "_prop_meta": json.dumps(node.get("prop_meta") or {}, ensure_ascii=False)}
        if node.get("aliases"):
            props["aliases"] = _scalar(node["aliases"])
        if node.get("note"):
            props["note"] = node["note"]
        for key, value in (node.get("ids") or {}).items():
            props[f"标识.{key}"] = _scalar(value)
        for key, value in (node.get("props") or {}).items():
            if value is None or value == "":
                continue
            props[key] = _scalar(value)
        label = _esc(node["kind"]) or "Unknown"
        grouped.setdefault(label, []).append({"id": node["id"], "props": props})
    return grouped


def edge_rows(graph: dict) -> dict:
    grouped = {}
    for edge in graph["edges"]:
        attrs = {"id": edge["id"], "relation": edge["relation"],
                 "机制": edge.get("mechanism") or "", "语义层": edge.get("layer") or "",
                 "认识状态": edge.get("epistemic") or "", "期间": edge.get("period") or "",
                 "置信度": edge.get("confidence") or "",
                 "_topic": graph.get("topic", ""),
                 "_fact_ids": edge.get("fact_ids") or [],
                 "_harvest_ids": edge.get("harvest_ids") or [],
                 "原文": (edge.get("quote") or "")[:400]}
        for key, value in (edge.get("attrs") or {}).items():
            if value is None or value == "":
                continue
            attrs[key] = _scalar(value)
        rel_type = _esc(edge["relation"]) or "关联"
        grouped.setdefault(rel_type, []).append(
            {"id": edge["id"], "from": edge["from"], "to": edge["to"], "attrs": attrs})
    return grouped


def load(cfg: dict, database: str, graph: dict, batch: int = 400,
         replace: bool = True) -> dict:
    validate_db(database)
    nodes_by_label, edges_by_type = node_rows(graph), edge_rows(graph)
    want_nodes = sum(len(v) for v in nodes_by_label.values())
    want_edges = sum(len(v) for v in edges_by_type.values())
    with Transaction(cfg, database) as tx:
        if replace:
            tx.one(f"MATCH (n:{COMMON_LABEL}) DETACH DELETE n")
        for label, rows in nodes_by_label.items():
            cypher = (f"UNWIND $rows AS row "
                      f"MERGE (n:{COMMON_LABEL} {{id: row.id}}) "
                      f"SET n = row.props SET n:`{label}`")
            for start in range(0, len(rows), batch):
                tx.one(cypher, {"rows": rows[start:start + batch]})
        for rel_type, rows in edges_by_type.items():
            cypher = (f"UNWIND $rows AS row "
                      f"MATCH (a:{COMMON_LABEL} {{id: row.from}}), "
                      f"(b:{COMMON_LABEL} {{id: row.to}}) "
                      f"MERGE (a)-[r:`{rel_type}` {{id: row.id}}]->(b) SET r = row.attrs")
            for start in range(0, len(rows), batch):
                tx.one(cypher, {"rows": rows[start:start + batch]})

        got_nodes = tx.one(f"MATCH (n:{COMMON_LABEL}) RETURN count(n)")[0][0]
        got_edges = tx.one("MATCH ()-[r]->() RETURN count(r)")[0][0]
        got_labels = {r[0]: r[1] for r in tx.one(
            f"MATCH (n:{COMMON_LABEL}) RETURN n.kind, count(*)")}
        orphan = tx.one(f"MATCH (n:{COMMON_LABEL}) WHERE NOT (n)--() "
                        f"RETURN count(n)")[0][0]
        prop_sample = tx.one(
            f"MATCH (n:{COMMON_LABEL}) RETURN n.id, n._prop_count "
            f"ORDER BY n._prop_count DESC LIMIT 3")
        mismatch = []
        if got_nodes != want_nodes:
            mismatch.append(f"节点数不符：期望 {want_nodes}，库里 {got_nodes}")
        if got_edges != want_edges:
            mismatch.append(f"关系数不符：期望 {want_edges}，库里 {got_edges}")
        for label, rows in nodes_by_label.items():
            expect = len(rows)
            actual = got_labels.get(label, 0)
            if actual != expect:
                mismatch.append(f"{label} 节点数不符：期望 {expect}，库里 {actual}")
        if mismatch:
            tx.rollback()
            raise Neo4jError("装载已回滚（库内容未变）：" + "；".join(mismatch))
        tx.commit()
    return {"database": database, "nodes": want_nodes, "edges": want_edges,
            "labels": {k: len(v) for k, v in nodes_by_label.items()},
            "relation_types": {k: len(v) for k, v in edges_by_type.items()},
            "isolated_in_db": orphan,
            "richest_nodes": [{"id": r[0], "prop_count": r[1]} for r in prop_sample],
            "verified": True}


def snapshot(cfg: dict, database: str) -> dict:
    nodes_res = run(cfg, database,
                    f"MATCH (n:{COMMON_LABEL}) "
                    f"RETURN count(n), sum(coalesce(n._prop_count, 0))")
    nodes, props = nodes_res["rows"][0] if nodes_res["rows"] else (0, 0)
    edge_res = run(cfg, database,
                   "MATCH ()-[r]->() RETURN count(r), count(DISTINCT type(r))")
    edges, types = edge_res["rows"][0] if edge_res["rows"] else (0, 0)
    kinds = run(cfg, database,
                f"MATCH (n:{COMMON_LABEL}) RETURN n.kind AS kind, count(*) AS c "
                f"ORDER BY c DESC")
    rels = run(cfg, database,
               "MATCH ()-[r]->() RETURN type(r) AS rel, count(*) AS c ORDER BY c DESC LIMIT 25")
    isolated = run(cfg, database,
                   f"MATCH (n:{COMMON_LABEL}) WHERE NOT (n)--() RETURN count(n)")
    return {"nodes": nodes, "props": props, "edges": edges, "relation_types": types,
            "isolated": isolated["rows"][0][0] if isolated["rows"] else 0,
            "by_kind": {r[0]: r[1] for r in kinds["rows"]},
            "top_relations": {r[0]: r[1] for r in rels["rows"]}}


def hop(cfg: dict, database: str, from_id: str, to_id: str | None,
        min_hops: int = 2, max_hops: int = 8, limit: int = 10,
        directed: bool = False) -> dict:
    min_hops, max_hops = max(1, int(min_hops)), max(1, int(max_hops))
    arrow = "->" if directed else "-"
    tail = f"(b:{COMMON_LABEL} {{id: $to}})" if to_id else f"(b:{COMMON_LABEL})"
    cypher = (
        f"MATCH p = (a:{COMMON_LABEL} {{id: $from}})-[*{min_hops}..{max_hops}]{arrow}{tail} "
        f"WHERE a <> b "
        f"RETURN [n IN nodes(p) | n.caption] AS 路径节点, "
        f"[r IN relationships(p) | type(r)] AS 关系链, "
        f"[r IN relationships(p) | r.语义层] AS 语义层链, "
        f"[r IN relationships(p) | r.机制] AS 机制链, "
        f"length(p) AS 跳数 "
        f"ORDER BY 跳数 DESC LIMIT $limit")
    res = run(cfg, database, cypher,
              {"from": from_id, "to": to_id, "limit": int(limit)}, timeout=300)
    return {"columns": res["columns"], "rows": res["rows"]}


def wipe(cfg: dict, database: str, drop: bool = False) -> dict:
    validate_db(database)
    if drop:
        run(cfg, "system", f"DROP DATABASE `{_esc(database)}` IF EXISTS", timeout=120)
        return {"database": database, "dropped": True}
    run(cfg, database, f"MATCH (n:{COMMON_LABEL}) DETACH DELETE n", timeout=300)
    return {"database": database, "dropped": False, "cleared": True}


# --------------------------------------------------------------------------
# Browser 样式
# --------------------------------------------------------------------------
_PALETTE = ["#C990C0", "#F79767", "#57C7E3", "#F16667", "#D9C8AE", "#8DCC93",
            "#ECB5C9", "#4C8EDA", "#FFC454", "#DA7194", "#569480", "#848484"]


def grass(graph: dict) -> str:
    kinds, rels = [], []
    for node in graph["nodes"]:
        if node["kind"] not in kinds:
            kinds.append(node["kind"])
    for edge in graph["edges"]:
        if edge["relation"] not in rels:
            rels.append(edge["relation"])
    lines = ["node {", "  diameter: 50px;", "  color: #A5ABB6;",
             "  border-color: #9AA1AC;", "  text-color-internal: #FFFFFF;",
             "  caption: '{caption}';", "  font-size: 10px;", "}", "",
             "relationship {", "  color: #A5ABB6;", "  shaft-width: 1px;",
             "  font-size: 8px;", "  padding: 3px;", "  text-color-external: #000000;",
             "  caption: '<type>';", "}", ""]
    for pos, kind in enumerate(kinds):
        lines += [f"node.{_esc(kind)} {{", f"  color: {_PALETTE[pos % len(_PALETTE)]};",
                  "  border-color: #9AA1AC;", "  text-color-internal: #FFFFFF;",
                  "  caption: '{caption}';", "  diameter: 65px;", "}", ""]
    for pos, rel in enumerate(rels):
        lines += [f"relationship.{_esc(rel)} {{",
                  f"  color: {_PALETTE[pos % len(_PALETTE)]};",
                  "  shaft-width: 2px;", "  caption: '<type>';", "}", ""]
    return "\n".join(lines)
