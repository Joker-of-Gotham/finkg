"""LazySearch 收割桥。

两条通道都能用，且**都必须落盘**：
  - HTTP `/api/query`：返回 final_answer + 完整 history（含每次工具调用的原始
    表格、执行过的 SQL、源库表名）。信息量通常是 final_answer 的 10-30 倍。
  - MCP `tools/call query_financial_data`：返回已综合好的 markdown，交互方便，
    但拿不到中间原始表。用 MCP 时把返回文本用 `harvest add` 落盘。

落盘之后本模块负责把一次返回**切开**成可分别取用的层（渐进式披露），并把里面的
每一个数据单元格清点出来（信息利用率核账）。
"""
from __future__ import annotations

import http.client
import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlparse

import fgconfig
import fgstore

CHANNEL_HTTP = "http"
CHANNEL_MCP = "mcp"
CHANNEL_MANUAL = "manual"

MCP_TOOL = "query_financial_data"

# 这些块是查询机制而非数据本体：留作出处线索，但不计入"应当被使用的数据"分母。
_METADATA_BLOCK = re.compile(r"<metadata>.*?</metadata>", re.S)
_SQL_BLOCK = re.compile(r"<executed_sql>.*?</executed_sql>", re.S)
_STORAGE_BLOCK = re.compile(r"<(file_storage|data_store)[^>]*>.*?</\1>", re.S)
_CSV_BLOCK = re.compile(r"```csv\s*\n(.*?)```", re.S)
_NUM = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


class LazyError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# 通道
# --------------------------------------------------------------------------
def _post_json(url: str, payload: dict, timeout: int) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        raise LazyError(f"LazySearch HTTP {exc.code}: {exc.read()[:300]!r}") from exc
    except OSError as exc:
        raise LazyError(f"连不上 LazySearch（{url}）：{exc}") from exc
    if not body:
        raise LazyError("LazySearch 返回空响应")
    return json.loads(body.decode("utf-8"))


def health(cfg: dict) -> dict:
    fgconfig.require(cfg, "lazysearch_url")
    url = f"{cfg['lazysearch_url']}/health"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - doctor 需要把任何失败原样报出
        raise LazyError(f"{url}: {type(exc).__name__} {exc}") from exc


def query_http(cfg: dict, question: str, timeout: int | None = None) -> dict:
    fgconfig.require(cfg, "lazysearch_url")
    url = f"{cfg['lazysearch_url']}/api/query"
    data = _post_json(url, {"query": question}, timeout or cfg["lazysearch_timeout"])
    if data.get("success") is False:
        raise LazyError(f"LazySearch 报错: {data.get('error')}")
    return data


def _mcp_post(cfg: dict, payload: dict, timeout: int, expect_reply: bool = True) -> dict:
    fgconfig.require(cfg, "lazysearch_url")
    parsed = urlparse(cfg["lazysearch_url"])
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise LazyError("lazysearch_url 必须是 http(s) origin，形如 http://<主机>:<端口>")
    cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = cfg["lazysearch_mcp_path"]
    for _ in range(3):  # 服务端会把 /mcp/ 307 到 /mcp
        conn = cls(parsed.hostname, port, timeout=timeout)
        conn.request("POST", path, body=json.dumps(payload).encode("utf-8"),
                     headers={"Content-Type": "application/json",
                              "Accept": "application/json, text/event-stream"})
        resp = conn.getresponse()
        body = resp.read()
        status, ctype = resp.status, (resp.getheader("Content-Type") or "")
        location = resp.getheader("Location")
        conn.close()
        if status in (301, 302, 307, 308) and location:
            path = urlparse(location).path or path
            continue
        break
    else:
        raise LazyError("MCP 重定向次数过多")
    if status >= 400:
        raise LazyError(f"MCP HTTP {status}: {body[:300]!r}")
    if not expect_reply or not body:
        return {}
    text = body.decode("utf-8", "replace")
    if "text/event-stream" in ctype.lower() or text.lstrip().startswith(("data:", "event:")):
        messages = []
        for event in text.replace("\r\n", "\n").split("\n\n"):
            chunk = "\n".join(ln[5:].lstrip() for ln in event.splitlines()
                              if ln.startswith("data:")).strip()
            if chunk and chunk != "[DONE]":
                messages.append(json.loads(chunk))
        if not messages:
            raise LazyError("MCP 返回空 SSE 流")
        return messages[-1]
    return json.loads(text)


def query_mcp(cfg: dict, question: str, timeout: int | None = None) -> str:
    timeout = timeout or cfg["lazysearch_timeout"]
    _mcp_post(cfg, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "finkg", "version": "1"}}}, 60)
    _mcp_post(cfg, {"jsonrpc": "2.0", "method": "notifications/initialized"}, 30,
              expect_reply=False)
    reply = _mcp_post(cfg, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": MCP_TOOL, "arguments": {"query": question}}}, timeout)
    if reply.get("error"):
        raise LazyError(f"MCP 错误: {reply['error']}")
    result = reply.get("result") or {}
    if result.get("isError"):
        raise LazyError(f"MCP 工具报错: {result.get('content')}")
    blocks = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
    return "\n".join(b for b in blocks if b)


def mcp_tools(cfg: dict) -> list:
    _mcp_post(cfg, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "finkg", "version": "1"}}}, 60)
    reply = _mcp_post(cfg, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, 60)
    return [{"name": t.get("name"), "description": (t.get("description") or "")[:400]}
            for t in ((reply.get("result") or {}).get("tools") or [])]


# --------------------------------------------------------------------------
# 切层：把一次返回拆成可分别取用的部分
# --------------------------------------------------------------------------
def _tool_calls(turn: dict) -> list:
    out = []
    for call in turn.get("tool_calls") or []:
        fn = call.get("function") or {}
        out.append({"name": fn.get("name"), "arguments": fn.get("arguments"),
                    "id": call.get("id")})
    return out


def partition(payload: dict, question: str, channel: str) -> dict:
    """把 LazySearch 返回切成 answer / data / plan / prompt 四层。

    - prompt：LazySearch 自己的 knowledge card 与系统指令，不是我们要的数据，排除。
    - plan：assistant 的工具调用（含 SQL 与参数），是出处线索。
    - data：role == "tool" 的原始返回，真正的数据富矿。
    - answer：final_answer，已经被综合过的结论。
    """
    turns, plan, data_blocks = [], [], []
    for index, turn in enumerate(payload.get("history") or []):
        if not isinstance(turn, dict):
            continue
        role = turn.get("role")
        content = turn.get("content")
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        calls = _tool_calls(turn)
        kind = {"tool": "data", "assistant": "plan", "user": "prompt"}.get(role, "other")
        if index == 0 and role == "user":
            kind = "prompt"
        turns.append({"index": index, "role": role, "kind": kind, "chars": len(text or ""),
                      "tool_call_id": turn.get("tool_call_id"), "tool_calls": calls})
        if kind == "data":
            data_blocks.append({"index": index, "tool_call_id": turn.get("tool_call_id"),
                                "text": text or ""})
        elif kind == "plan":
            plan.append({"index": index, "text": text or "", "tool_calls": calls})
    answer = payload.get("final_answer") or ""
    if channel in (CHANNEL_MCP, CHANNEL_MANUAL) and not payload.get("history"):
        answer = payload.get("final_answer") or payload.get("text") or ""
    return {"question": question, "channel": channel, "answer": answer,
            "turns": turns, "plan": plan, "data_blocks": data_blocks,
            "tool_briefs": payload.get("tool_briefs") or []}


def provenance(parts: dict) -> dict:
    """抽出出处线索：LazySearch 用了哪些内部工具、哪些源库表、执行了什么 SQL。"""
    tools, tables, sqls, caches, search_types = [], [], [], [], []
    for step in parts["plan"]:
        for call in step["tool_calls"]:
            if call.get("name"):
                tools.append(call["name"])
    for block in parts["data_blocks"]:
        text = block["text"]
        tables += re.findall(r"source_table:\s*([A-Za-z0-9_.]+)", text)
        tables += re.findall(r"\bFROM\s+([A-Za-z0-9_]+\.[A-Za-z0-9_]+)", text)
        tables += re.findall(r"\bJOIN\s+([A-Za-z0-9_]+\.[A-Za-z0-9_]+)", text)
        search_types += re.findall(r"search_type:\s*(.+)", text)
        caches += re.findall(r"pl\.read_csv\('([^']+)'\)", text)
        for match in _SQL_BLOCK.finditer(text):
            body = re.sub(r"</?executed_sql>|```sql|```", "", match.group(0)).strip()
            if body:
                sqls.append(body)
    for brief in parts["tool_briefs"]:
        raw = brief.get("brief") or ""
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if obj.get("source_table"):
            tables.append(obj["source_table"])
        if obj.get("search_type"):
            search_types.append(str(obj["search_type"]))

    def uniq(seq):
        seen, out = set(), []
        for item in seq:
            key = str(item).strip()
            if key and key not in seen:
                seen.add(key)
                out.append(key)
        return out

    return {"lazysearch_tools": uniq(tools), "source_tables": uniq(tables),
            "search_types": uniq(search_types), "executed_sql": sqls,
            "server_cache_csv": uniq(caches)}


# --------------------------------------------------------------------------
# 数据单元格清点：信息利用率核账的分母
# --------------------------------------------------------------------------
def _norm_num(text: str) -> str:
    return str(text).replace(",", "").replace(" ", "").replace("，", "").strip()


def _strip_mechanics(text: str) -> str:
    for pattern in (_METADATA_BLOCK, _SQL_BLOCK, _STORAGE_BLOCK):
        text = pattern.sub(" ", text)
    return text


def _md_tables(text: str) -> list:
    """抓 markdown 表：返回 [{header:[...], rows:[[...]]}]。"""
    tables, current = [], None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("|") and line.endswith("|") and line.count("|") >= 2:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cells):
                continue  # 分隔行
            if current is None:
                current = {"header": cells, "rows": []}
            else:
                current["rows"].append(cells)
        else:
            if current and current["rows"]:
                tables.append(current)
            current = None
    if current and current["rows"]:
        tables.append(current)
    return tables


def _csv_tables(text: str) -> list:
    tables = []
    for match in _CSV_BLOCK.finditer(text):
        lines = [ln for ln in match.group(1).splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        header = [c.strip() for c in lines[0].split(",")]
        rows = [[c.strip() for c in ln.split(",")] for ln in lines[1:]]
        tables.append({"header": header, "rows": rows})
    return tables


_ENTITIES = {"&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"',
             "&#39;": "'", "&emsp;": " ", "&ensp;": " "}


def clean_cell(text: str) -> str:
    """去掉 HTML 实体与 markdown 强调，让单元格取值能跟事实里的引文对得上。"""
    value = str(text or "")
    for entity, replacement in _ENTITIES.items():
        value = value.replace(entity, replacement)
    value = value.replace("**", "").replace("`", "")
    return value.strip()


def _cells_from_tables(tables: list, origin: str) -> list:
    cells = []
    blank = {"", "-", "—", "None", "null", "nan", "NaN", "N/A"}
    for tno, table in enumerate(tables):
        header = [clean_cell(h) for h in table["header"]]
        index_col = 0 if header and header[0].lower() in ("row_idx", "序号", "#", "") else -1
        for rno, row in enumerate(table["rows"]):
            cleaned = [clean_cell(c) for c in row]
            context = " | ".join(cleaned)[:240]
            for cno, value in enumerate(cleaned):
                if cno == index_col or value in blank:
                    continue
                column = header[cno] if cno < len(header) else f"col{cno}"
                cells.append({"origin": origin, "table": tno, "row": rno,
                              "column": column, "value": value, "context": context})
    return cells


def data_cells(parts: dict) -> list:
    """一次收割里"本应被使用"的数据单元格全集。

    只数真正的数据面：answer 与 tool 返回里的表格单元格 + 正文中的数值。
    LazySearch 自己的 prompt、SQL 语句、缓存路径都不计入。
    """
    cells = []
    answer = parts.get("answer") or ""
    cells += _cells_from_tables(_md_tables(answer), "answer")
    used_lines = {ln.strip() for ln in answer.splitlines() if ln.strip().startswith("|")}
    for line in answer.splitlines():
        if line.strip() in used_lines:
            continue
        for token in _NUM.findall(line):
            if len(_norm_num(token)) >= 2:
                cells.append({"origin": "answer", "table": -1, "row": -1,
                              "column": "正文", "value": token, "context": line.strip()[:240]})
    for block in parts.get("data_blocks") or []:
        text = _strip_mechanics(block["text"])
        origin = f"data[{block['index']}]"
        cells += _cells_from_tables(_csv_tables(text), origin)
        cells += _cells_from_tables(_md_tables(text), origin)
    # 去重：同一个 (column, value, context) 只算一次
    seen, out = set(), []
    for cell in cells:
        key = (cell["column"], _norm_num(cell["value"]), cell["context"])
        if key in seen:
            continue
        seen.add(key)
        out.append(cell)
    return out


def searchable_text(record: dict) -> str:
    """quote 校验的比对面：answer + 全部 tool 原始返回 + plan 文本。"""
    parts = record["parts"]
    chunks = [parts.get("answer") or ""]
    chunks += [b["text"] for b in parts.get("data_blocks") or []]
    chunks += [p["text"] for p in parts.get("plan") or []]
    chunks += [json.dumps(parts.get("tool_briefs") or [], ensure_ascii=False)]
    return "\n".join(chunks)


# --------------------------------------------------------------------------
# 组装 harvest 记录
# --------------------------------------------------------------------------
def build_record(harvest_id: str, question: str, channel: str, payload: dict,
                 tags: list | None = None, note: str = "") -> dict:
    parts = partition(payload, question, channel)
    cells = data_cells(parts)
    record = {
        "schema": fgstore.SCHEMA, "id": harvest_id, "question": question,
        "channel": channel, "collected_at": fgstore.now(),
        "tags": tags or [], "note": note,
        "server": {"version": payload.get("version"), "intent": payload.get("intent"),
                   "model": payload.get("lazy_model_name"),
                   "steps": payload.get("steps_count"),
                   "tokens": payload.get("total_tokens_used")},
        "parts": parts,
        "provenance": provenance(parts),
        "data_cell_count": len(cells),
        "raw": payload,
    }
    return record


def summarize(record: dict) -> dict:
    parts = record["parts"]
    data_chars = sum(b["chars"] for b in parts["turns"] if b["kind"] == "data")
    return {
        "id": record["id"], "question": record["question"],
        "channel": record["channel"], "collected_at": record["collected_at"],
        "tags": record.get("tags") or [],
        "state": record.get("state", "unmined"),
        "answer_chars": len(parts.get("answer") or ""),
        "data_chars": data_chars,
        "data_cells": record["data_cell_count"],
        "lazysearch_tools": record["provenance"]["lazysearch_tools"],
        "source_tables": record["provenance"]["source_tables"],
    }
