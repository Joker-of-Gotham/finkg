#!/usr/bin/env python3
"""finkg 工具箱。零第三方依赖，Python 3.10+。

把 `<skill-dir>/scripts/fg.py` 记成 $FG，然后从工作区根目录执行：

    python $FG doctor
    python $FG session new "宁德时代动力电池产业链" --anchor E-catl=宁德时代:Company
    python $FG search "宁德时代2025年合并利润表全部科目"
    python $FG harvest show h-0001 --part cells --unused-only
    python $FG harvest mine h-0001 --done   # 读取会话 drafts/ 下的实体与事实草稿
    python $FG compile && python $FG quality
    python $FG neo4j ensure-db && python $FG neo4j load

所有子命令输出 JSON，便于 Agent 直接解析；要落盘用 `--out-file`，不要用 shell 重定向。
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fgconfig  # noqa: E402
import fgdepth  # noqa: E402
import fglazy  # noqa: E402
import fgmodel  # noqa: E402
import fgneo4j  # noqa: E402
import fgstore  # noqa: E402

SKILL_ROOT = Path(__file__).resolve().parents[1]
# 档位只是发掘方向感，用来生成「下一步去问什么」，不是装库门槛。
PROFILES = {
    "probe": {"label": "探路", "anchor_props": 12, "business_nodes": 30,
              "analyzable_ratio": 0.60, "layers": 3, "usage": 0.50,
              "independent": {"6": 1}},
    "standard": {"label": "标准", "anchor_props": 24, "business_nodes": 120,
                 "analyzable_ratio": 0.80, "layers": 6, "usage": 0.75,
                 "independent": {"6": 8, "8": 3}},
    "deep": {"label": "纵深", "anchor_props": 40, "business_nodes": 300,
             "analyzable_ratio": 0.90, "layers": 8, "usage": 0.85,
             "independent": {"6": 20, "10": 8}},
}

# 十个检索扇区：brief/quality 用它们指出「下一步该问什么」，不设数量门槛。
SECTORS = [
    {"id": "1", "name": "身份与证券", "tags": ("身份", "证券", "s1"),
     "keywords": ("证券实体", "ISIN", "上市地", "股本", "资产类别"),
     "ask": "解析 {name} 的证券实体，A股/港股/美股候选都列出并说明区别"},
    {"id": "2", "name": "财务报表", "tags": ("财报", "利润表", "资产负债表", "s2"),
     "keywords": ("利润表", "资产负债表", "现金流量表", "全部科目"),
     "ask": "{name} 最近三个报告期合并利润表、资产负债表、现金流量表全部科目与数值，不要摘要"},
    {"id": "3", "name": "财务指标", "tags": ("指标", "s3"),
     "keywords": ("成长能力", "盈利能力", "偿债", "营运能力", "每股收益"),
     "ask": "{name} 最近 3 个报告期财务指标（成长/盈利/偿债/营运）全部字段"},
    {"id": "4", "name": "业务构成", "tags": ("分部", "主营", "s4"),
     "keywords": ("主营业务构成", "分部", "毛利率", "产品收入"),
     "ask": "{name} 最新主营业务构成及对应营收、成本、毛利率、占比、同比"},
    {"id": "5", "name": "所有权与治理", "tags": ("股东", "治理", "s5"),
     "keywords": ("十大股东", "持股比例", "实控人", "董监高"),
     "ask": "{name} 最新报告期前十大股东、持股数量、持股比例、股东性质与实控人"},
    {"id": "6", "name": "供应与产能", "tags": ("供应", "产能", "客户", "s6"),
     "keywords": ("供应商", "主要客户", "产能利用率", "长协", "在建工程"),
     "ask": "{name} 主要客户与供应商、产能与产能利用率、在建工程与长协"},
    {"id": "7", "name": "行情与估值", "tags": ("行情", "估值", "s7"),
     "keywords": ("日线", "市值", "PE(TTM)", "换手"),
     "ask": "{name} 最近 60 个交易日日线与市值、PE(TTM)、PB"},
    {"id": "8", "name": "上游要素", "tags": ("上游", "商品", "s8"),
     "keywords": ("开工率", "库存", "进出口"),
     "ask": "与 {name} 相关的关键原料价格、产量、开工率、库存时序"},
    {"id": "9", "name": "政策与事件", "tags": ("政策", "事件", "s9"),
     "keywords": ("监管政策", "准入", "处罚", "诉讼"),
     "ask": "{name} 所属行业最近的监管政策变化与公司公告、处罚、诉讼"},
    {"id": "10", "name": "预期与风险", "tags": ("预期", "风险", "s10"),
     "keywords": ("分析师", "一致预期", "风险因素"),
     "ask": "{name} 的分析师盈利预测与一致预期；年报披露的主要风险因素"},
]

STRAY_ROOT_NAMES = {
    "entities.json", "facts.json", "facts-edges.json", "facts-props.json",
    "graph.json", "quality.json", "doctor.json", "nodes.csv", "relationships.csv",
    "harvest.txt", "reply.txt",
}

TEMPLATE_DEFAULT_NAME = {
    "entity": "entities.json",
    "fact": "facts.json",
    "fact-edge": "facts-edges.json",
    "mechanism-question": "mechanism-questions.json",
}


_OUT_FILE: Path | None = None


def out(payload, code: int = 0) -> int:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if _OUT_FILE is not None:
        _OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        _OUT_FILE.write_text(text + "\n", encoding="utf-8")
        print(json.dumps({"ok": code == 0, "written": str(_OUT_FILE),
                          "bytes": len(text.encode("utf-8"))}, ensure_ascii=False))
    else:
        print(text)
    return code


def fail(message: str, **extra) -> int:
    return out({"ok": False, "error": message, **extra}, 1)


def _cfg(args) -> dict:
    return fgconfig.load({"neo4j_url": getattr(args, "neo4j_url", None),
                          "neo4j_database": getattr(args, "database", None),
                          "lazysearch_url": getattr(args, "lazysearch_url", None)})


def _session(args) -> tuple[dict, fgstore.Session]:
    cfg = _cfg(args)
    return cfg, fgstore.Session.resolve(cfg["_sessions_root"], getattr(args, "session", None))


def _database(cfg: dict, session: fgstore.Session, args) -> str:
    return (getattr(args, "database", None) or cfg.get("neo4j_database")
            or session.database())


def _anchor_name(meta: dict) -> str:
    anchors = meta.get("anchors") or []
    if not anchors:
        return meta.get("topic") or "该公司"
    first = anchors[0]
    if isinstance(first, dict):
        return first.get("name") or first.get("id") or "该公司"
    return str(first)


def _sector_coverage(session: fgstore.Session, meta: dict) -> tuple[list, list]:
    harvests = session.harvests()
    tags = set()
    blob = []
    for harvest in harvests:
        tags.update(str(t) for t in (harvest.get("tags") or []))
        blob.append(harvest.get("question") or "")
    text = " ".join(blob)
    name = _anchor_name(meta)
    covered, missing = [], []
    for sector in SECTORS:
        hit = any(tag in tags for tag in sector["tags"]) or any(
            key in text for key in sector["keywords"])
        row = {"id": sector["id"], "name": sector["name"],
               "next_query": sector["ask"].format(name=name)}
        (covered if hit else missing).append(row)
    return covered, missing


def _stray_workspace_files(cfg: dict) -> list:
    root = Path(cfg["_workspace"])
    if not root.is_dir():
        return []
    return [name for name in sorted(STRAY_ROOT_NAMES) if (root / name).is_file()]


# ==========================================================================
# doctor
# ==========================================================================
def cmd_doctor(args) -> int:
    cfg = _cfg(args)
    configured = fgconfig.configured(cfg)
    report = {
        "workspace": cfg["_workspace"],
        "local_config": cfg["_local_config"],
        "sessions_root": cfg["_sessions_root"],
        "environment_file": cfg["_env_file"],
        "configured": configured,
        "config": fgconfig.redacted(cfg),
        "checks": {},
    }

    def probe(name, fn, needs):
        missing = [k for k in needs if not cfg.get(k)]
        if missing:
            report["checks"][name] = {"ok": False, "reason": "unconfigured",
                                      "missing": missing}
            return
        try:
            report["checks"][name] = {"ok": True, "reason": "ok", "detail": fn()}
        except Exception as exc:  # noqa: BLE001
            report["checks"][name] = {"ok": False, "reason": "unreachable",
                                      "detail": str(exc)}

    # 默认输出刻意不含主机、账号与其他项目的库名，这样 doctor 结果可以直接贴进 issue。
    # 本机排错要看细节就加 --verbose。
    def databases():
        names = [d["name"] for d in fgneo4j.databases(cfg)]
        if args.verbose:
            return {"count": len(names), "names": names}
        return {"count": len(names),
                "note": "库名未显示（可能含其他项目）；本机排错用 --verbose"}

    probe("lazysearch_health", lambda: fglazy.health(cfg), ["lazysearch_url"])
    probe("lazysearch_mcp", lambda: [t["name"] for t in fglazy.mcp_tools(cfg)],
          ["lazysearch_url"])
    probe("neo4j", lambda: fgneo4j.info(cfg), ["neo4j_url", "neo4j_user", "neo4j_password"])
    probe("neo4j_databases", databases, ["neo4j_url", "neo4j_user", "neo4j_password"])

    report["sessions"] = fgstore.Session.list_all(cfg["_sessions_root"])
    report["environment_file_exists"] = Path(cfg["_env_file"]).exists()
    required = ("lazysearch_health", "neo4j")
    ok = all(report["checks"][k]["ok"] for k in required)
    report["ok"] = ok
    if not all(configured.values()):
        report["next"] = fgconfig.setup_hint(cfg).splitlines()
    elif not ok:
        report["next"] = ["端点已配置但连不上：确认服务在跑、地址端口正确、本机能访问到该网络"]
    elif not report["environment_file_exists"]:
        report["next"] = [f"建个环境档案记录你这套环境有哪些数据表与检索工具："
                          f"fg.py env --init"]
    return out(report, 0 if ok else 1)


def cmd_setup(args) -> int:
    """生成/更新工作区的 financial_graph.local.json。所有部署信息只落在这里。"""
    cfg = _cfg(args)
    path = Path(cfg["_local_config"])
    existing = fgstore.read_json(path, {}) or {}
    existing.pop("_说明", None)

    fields = [
        ("lazysearch_url", args.lazysearch_url, "LazySearch 地址（形如 http://主机:端口）"),
        ("neo4j_url", args.neo4j_url, "Neo4j HTTP 地址（形如 http://主机:7474）"),
        ("neo4j_user", args.neo4j_user, "Neo4j 用户名"),
        ("neo4j_password", args.neo4j_password, "Neo4j 密码"),
    ]
    interactive = not any(value for _, value, _ in fields) and not args.non_interactive
    payload = dict(existing)
    for key, value, prompt in fields:
        if value:
            payload[key] = value
        elif interactive:
            current = existing.get(key)
            shown = "<已配置>" if (current and key in fgconfig.SECRET_KEYS) else (current or "")
            suffix = f"（回车保留 {shown}）" if current else ""
            try:
                entered = input(f"{prompt}{suffix}: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return fail("已取消")
            if entered:
                payload[key] = entered
    if args.allow_http_auth is not None:
        payload["neo4j_allow_http_auth"] = args.allow_http_auth
    elif "neo4j_allow_http_auth" not in payload:
        payload["neo4j_allow_http_auth"] = True

    missing = [k for k in fgconfig.DEPLOYMENT_KEYS if not payload.get(k)]
    payload = {"_说明": "本机私有配置，不要提交。地址、账号、密码只应存在于这里或 FG_* 环境变量。",
               **payload}
    fgstore.write_json(path, payload)

    gitignore = Path(cfg["_workspace"]) / ".gitignore"
    ignored = gitignore.exists() and fgconfig.LOCAL_FILE in gitignore.read_text(encoding="utf-8")
    return out({
        "ok": not missing, "written": str(path),
        "configured": {k: bool(payload.get(k)) for k in fgconfig.DEPLOYMENT_KEYS},
        "still_missing": missing,
        "gitignored": ignored,
        "warning": None if ignored else
                   f"⚠ {gitignore} 里没有 {fgconfig.LOCAL_FILE}，这个文件含密码，务必加进 .gitignore",
        "next": ["python <skill>/scripts/fg.py doctor"] if not missing else
                [f"补上 {'、'.join(missing)} 后再跑 doctor"],
    }, 0 if not missing else 1)


def cmd_env(args) -> int:
    """环境档案：记录你这套环境有哪些数据表与检索工具。本地文件，不入库。"""
    cfg = _cfg(args)
    path = Path(cfg["_env_file"])
    template = SKILL_ROOT / "assets" / "templates" / "environment.md"
    if args.init:
        if path.exists() and not args.force:
            return fail(f"{path} 已存在（加 --force 覆盖）")
        path.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
        return out({"ok": True, "created": str(path),
                    "hint": "用检索探索你的环境，把发现记进这个文件；它是本地的，不会进仓库。"})
    if not path.exists():
        return out({"ok": False, "path": str(path), "exists": False,
                    "hint": "还没有环境档案。跑 `fg.py env --init` 创建，"
                            "然后把你这套环境有哪些库表与检索工具记进去。"}, 1)
    text = path.read_text(encoding="utf-8")
    if args.grep:
        pattern = re.compile(args.grep, re.I)
        text = "\n".join(ln for ln in text.splitlines() if pattern.search(ln))
    return out({"ok": True, "path": str(path), "content": text})


# ==========================================================================
# session
# ==========================================================================
def _parse_anchor(spec: str) -> dict:
    """`E-catl=宁德时代:Company` -> {id, name, kind}"""
    ident, _, rest = spec.partition("=")
    name, _, kind = rest.partition(":")
    return {"id": ident.strip(), "name": (name or ident).strip(),
            "kind": (kind or "Company").strip()}


def cmd_session_new(args) -> int:
    cfg = _cfg(args)
    session_id = args.id or f"{fgstore.slug(args.topic)}-{fgstore.now()[:10].replace('-', '')}"
    meta = {
        "topic": args.topic,
        "center_question": args.center_question or "",
        "as_of": args.as_of or fgstore.now()[:10],
        "profile": args.profile,
        "anchors": [_parse_anchor(a) for a in (args.anchor or [])],
        "scope": {"include": args.include or [], "exclude": args.exclude or [],
                  "markets": args.market or [], "period": args.period or ""},
        "purpose": args.purpose or "",
        "mechanism_questions": [],
        "neo4j_database": args.database or fgstore.neo4j_db_name(session_id),
    }
    try:
        session = fgstore.Session.create(cfg["_sessions_root"], session_id, meta)
    except fgstore.StoreError as exc:
        return fail(str(exc))
    session.log("session_created", topic=args.topic, profile=args.profile)
    return out({"ok": True, "session_id": session_id, "path": str(session.root),
                "drafts": str(session.drafts_dir),
                "neo4j_database": meta["neo4j_database"], "meta": session.meta(),
                "next": ["把中心问题与机制问题和用户对齐：fg.py align --stage scope ...",
                         "按十个扇区检索，每次加 --tag（如 --tag 财报）",
                         f"草稿只写进 {session.drafts_dir}，不要写到工作区根目录"]})


def cmd_session_list(args) -> int:
    cfg = _cfg(args)
    return out({"sessions_root": cfg["_sessions_root"],
                "sessions": fgstore.Session.list_all(cfg["_sessions_root"])})


def cmd_session_show(args) -> int:
    _, session = _session(args)
    return out({"meta": session.meta(), "path": str(session.root),
                "harvests": len(session.harvests()), "facts": len(session.facts()),
                "entities": len(session.entities()), "ledger": len(session.ledger())})


def cmd_session_set(args) -> int:
    _, session = _session(args)
    changes = {}
    if args.center_question is not None:
        changes["center_question"] = args.center_question
    if args.profile:
        changes["profile"] = args.profile
    if args.as_of:
        changes["as_of"] = args.as_of
    if args.anchor:
        changes["anchors"] = [_parse_anchor(a) for a in args.anchor]
    if args.mechanism_question:
        meta = session.meta()
        cases = list(meta.get("mechanism_questions") or [])
        for spec in args.mechanism_question:
            try:
                case = json.loads(spec) if spec.strip().startswith("{") else None
            except json.JSONDecodeError as exc:
                return fail(f"--mechanism-question 不是合法 JSON: {exc}")
            if case is None:
                return fail("--mechanism-question 需要 JSON，如 "
                            '{"id":"M1","question":"...","from":"E-a","to":"E-b",'
                            '"min_hops":6,"independent":2,"layers":["policy_regulation"]}')
            cases = [c for c in cases if c.get("id") != case.get("id")] + [case]
        changes["mechanism_questions"] = cases
    if not changes:
        return fail("没有要改的东西")
    meta = session.update_meta(**changes)
    session.log("session_updated", changes=sorted(changes))
    return out({"ok": True, "meta": meta})


# ==========================================================================
# search / harvest
# ==========================================================================
def _store_harvest(session, question, channel, payload, tags, note) -> dict:
    harvest_id = session.next_harvest_id()
    record = fglazy.build_record(harvest_id, question, channel, payload, tags, note)
    record["state"] = "unmined"
    summary = fglazy.summarize(record)
    session.save_harvest(record, summary)
    return summary


def cmd_search(args) -> int:
    cfg, session = _session(args)
    try:
        if args.channel == fglazy.CHANNEL_MCP:
            text = fglazy.query_mcp(cfg, args.query, args.timeout)
            payload = {"final_answer": text, "history": []}
        else:
            payload = fglazy.query_http(cfg, args.query, args.timeout)
    except fglazy.LazyError as exc:
        return fail(str(exc))
    summary = _store_harvest(session, args.query, args.channel, payload,
                             args.tag or [], args.note or "")
    session.log("harvest", harvest_id=summary["id"], question=args.query,
                channel=args.channel, data_cells=summary["data_cells"])
    preview = (payload.get("final_answer") or "")[: args.preview]
    drafts = session.ensure_layout().drafts_dir
    return out({"ok": True, "harvest": summary, "answer_preview": preview,
                "next": [f"fg.py harvest show {summary['id']} --part data  # 看原始表",
                         f"fg.py harvest show {summary['id']} --part cells # 看待挖掘单元格",
                         f"把实体/事实草稿写到 {drafts}，不要写到工作区根目录",
                         f"fg.py harvest mine {summary['id']} --done  # 读取 drafts/ 下 fact*.json"]})


def cmd_harvest_add(args) -> int:
    """把 MCP 或人工获得的返回文本落盘（同样进入信息利用率核账）。"""
    _, session = _session(args)
    if args.file:
        raw = fgstore.read_text(Path(args.file))
    else:
        raw = sys.stdin.read()
    if not raw.strip():
        return fail("没有内容可落盘（用 --file 或从 stdin 传入）")
    payload = None
    if raw.lstrip().startswith("{"):
        try:
            candidate = json.loads(raw)
            if isinstance(candidate, dict) and ("final_answer" in candidate or "history" in candidate):
                payload = candidate
        except json.JSONDecodeError:
            payload = None
    if payload is None:
        payload = {"final_answer": raw, "history": []}
    summary = _store_harvest(session, args.query, args.channel, payload,
                             args.tag or [], args.note or "")
    session.log("harvest", harvest_id=summary["id"], question=args.query,
                channel=args.channel, data_cells=summary["data_cells"])
    return out({"ok": True, "harvest": summary})


def cmd_harvest_list(args) -> int:
    _, session = _session(args)
    rows = session.harvests()
    if args.state:
        rows = [r for r in rows if r.get("state") == args.state]
    if args.tag:
        rows = [r for r in rows if set(args.tag) & set(r.get("tags") or [])]
    return out({"count": len(rows),
                "unmined": sum(1 for r in session.harvests() if r.get("state") == "unmined"),
                "total_data_cells": sum(r.get("data_cells", 0) for r in session.harvests()),
                "harvests": rows})


def cmd_harvest_show(args) -> int:
    _, session = _session(args)
    try:
        record = session.harvest(args.id)
    except fgstore.StoreError as exc:
        return fail(str(exc))
    parts, part = record["parts"], args.part

    def clip(text: str) -> str:
        text = text or ""
        if args.grep:
            pattern = re.compile(args.grep, re.I)
            keep = [ln for ln in text.splitlines() if pattern.search(ln)]
            text = "\n".join(keep)
        return text if args.max_chars <= 0 else text[: args.max_chars]

    if part == "meta":
        return out({k: v for k, v in record.items() if k not in ("parts", "raw")})
    if part == "answer":
        return out({"id": record["id"], "question": record["question"],
                    "answer": clip(parts.get("answer"))})
    if part == "plan":
        return out({"id": record["id"], "plan": [
            {"index": p["index"], "tool_calls": p["tool_calls"], "text": clip(p["text"])}
            for p in parts["plan"]]})
    if part == "provenance":
        return out({"id": record["id"], "provenance": record["provenance"],
                    "turns": parts["turns"]})
    if part == "data":
        blocks = parts["data_blocks"]
        if args.block is not None:
            blocks = [b for b in blocks if b["index"] == args.block]
        return out({"id": record["id"], "blocks": [
            {"index": b["index"], "tool_call_id": b["tool_call_id"],
             "chars": len(b["text"]), "text": clip(b["text"])} for b in blocks]})
    if part == "cells":
        cells = fglazy.data_cells(parts)
        used = _used_cell_values(session, record["id"])
        rows = []
        for cell in cells:
            rows.append({**cell, "used": _cell_used(cell, used)})
        if args.unused_only:
            rows = [r for r in rows if not r["used"]]
        if args.grep:
            pattern = re.compile(args.grep, re.I)
            rows = [r for r in rows if pattern.search(r["context"]) or pattern.search(r["column"])]
        limit = args.limit or 200
        return out({"id": record["id"], "total_cells": len(cells),
                    "used_cells": sum(1 for c in cells if _cell_used(c, used)),
                    "showing": min(limit, len(rows)), "cells": rows[:limit]})
    return out({"id": record["id"], "question": record["question"],
                "server": record["server"], "provenance": record["provenance"],
                "answer": clip(parts.get("answer")),
                "data_blocks": [{"index": b["index"], "chars": len(b["text"])}
                                for b in parts["data_blocks"]],
                "data_cell_count": record["data_cell_count"]})


def cmd_harvest_mine(args) -> int:
    """提交一次收割的挖掘成果：实体 + 事实 + 未用数据的处置理由。"""
    _, session = _session(args)
    session.ensure_layout()
    try:
        session.harvest(args.id)
    except fgstore.StoreError as exc:
        return fail(str(exc))
    entity_files = ([args.entities] if args.entities
                    else ([str(session.drafts_dir / "entities.json")]
                          if (session.drafts_dir / "entities.json").exists() else []))
    fact_files = ([args.facts] if args.facts
                  else [str(p) for p in sorted(session.drafts_dir.glob("fact*.json"))])
    added_entities = 0
    for path in entity_files:
        added_entities += _ingest_entities(session, path)
    added_facts, problems = 0, []
    for path in fact_files:
        count, extra = _ingest_facts(session, path, default_harvest=args.id)
        added_facts += count
        problems.extend(extra)
    state = "mined" if args.done else "partial"
    session.set_harvest_state(args.id, state=state)
    session.log("harvest_mined", harvest_id=args.id, facts=added_facts,
                entities=added_entities, state=state)
    usage = _usage_for(session, [args.id])
    return out({"ok": not any(p["level"] == "error" for p in problems),
                "harvest": args.id, "state": state,
                "entities_added": added_entities, "facts_added": added_facts,
                "problems": problems, "usage": usage["harvests"][0] if usage["harvests"] else {},
                "hint": f"fg.py harvest show {args.id} --part cells --unused-only "
                        f"# 看还有哪些数据没落进图里"}, 0 if not problems else 1)


def cmd_harvest_dispose(args) -> int:
    _, session = _session(args)
    data = session.dispositions()
    entries = data.setdefault("unused", {}).setdefault(args.id, [])
    entries.append({"scope": args.scope, "reason": args.reason, "at": fgstore.now()})
    session.save_dispositions(data)
    session.log("harvest_disposed", harvest_id=args.id, scope=args.scope, reason=args.reason)
    return out({"ok": True, "harvest": args.id, "dispositions": entries})


# ---- 信息利用率 -----------------------------------------------------------
def _used_cell_values(session, harvest_id: str) -> str:
    chunks = []
    for fact in session.facts():
        if fact.get("harvest_id") != harvest_id:
            continue
        chunks.append(str(fact.get("quote") or ""))
        obj = fact.get("object") or {}
        for key in ("value", "text", "entity"):
            if obj.get(key) is not None:
                chunks.append(str(obj[key]))
        target = fact.get("target") or {}
        for value in (target.get("attrs") or {}).values():
            chunks.append(str(value))
    return fgmodel.flat("\n".join(chunks))


def _cell_used(cell: dict, used_flat: str) -> bool:
    token = fgmodel.flat(cell["value"])
    if not token or len(token) < 2:
        return True  # 太短的片段不构成「信息」，不计入未用
    return token in used_flat


def _disposed(cell: dict, entries: list) -> bool:
    for entry in entries:
        scope = str(entry.get("scope") or "")
        if not scope or scope == "*":
            return True
        if scope in cell["column"] or scope in cell["context"]:
            return True
    return False


def _usage_for(session, harvest_ids=None) -> dict:
    dispositions = session.dispositions().get("unused", {})
    rows, total, used_total, disposed_total = [], 0, 0, 0
    for summary in session.harvests():
        hid = summary["id"]
        if harvest_ids and hid not in harvest_ids:
            continue
        record = session.harvest(hid)
        cells = fglazy.data_cells(record["parts"])
        used_flat = _used_cell_values(session, hid)
        entries = dispositions.get(hid, [])
        used, disposed, open_cells = [], [], []
        for cell in cells:
            if _cell_used(cell, used_flat):
                used.append(cell)
            elif _disposed(cell, entries):
                disposed.append(cell)
            else:
                open_cells.append(cell)
        total += len(cells)
        used_total += len(used)
        disposed_total += len(disposed)
        rows.append({
            "harvest": hid, "question": summary["question"], "state": summary.get("state"),
            "data_cells": len(cells), "used": len(used), "disposed": len(disposed),
            "open": len(open_cells),
            "use_ratio": round(len(used) / len(cells), 4) if cells else 1.0,
            "accounted_ratio": round((len(used) + len(disposed)) / len(cells), 4)
            if cells else 1.0,
            "open_samples": [{"column": c["column"], "value": c["value"],
                              "context": c["context"][:160]} for c in open_cells[:12]],
        })
    return {"harvests": rows, "total_data_cells": total, "used": used_total,
            "disposed": disposed_total, "open": total - used_total - disposed_total,
            "use_ratio": round(used_total / total, 4) if total else 0.0,
            "accounted_ratio": round((used_total + disposed_total) / total, 4)
            if total else 0.0}


def cmd_usage(args) -> int:
    _, session = _session(args)
    report = _usage_for(session, args.id or None)
    report["worst"] = sorted(report["harvests"], key=lambda r: r["accounted_ratio"])[:8]
    fgstore.write_json(session.reports_dir / "harvest-usage.json", report)
    return out(report)


# ==========================================================================
# entity / fact / compile
# ==========================================================================
def _ingest_entities(session, path) -> int:
    records = fgstore.load_records(session.resolve_input(path))
    existing = {e["id"]: e for e in session.entities() if e.get("id")}
    for rec in records:
        if rec.get("id"):
            existing[rec["id"]] = {**existing.get(rec["id"], {}), **rec}
    fgstore.rewrite_jsonl(session.entities_path, list(existing.values()))
    return len(records)


def _ingest_facts(session, path, default_harvest=None) -> tuple[int, list]:
    records = fgstore.load_records(session.resolve_input(path))
    existing = session.facts()
    used_ids = {f.get("id") for f in existing}
    counter = len(existing)
    fresh = []
    for rec in records:
        if default_harvest and not rec.get("harvest_id"):
            rec["harvest_id"] = default_harvest
        if not rec.get("id"):
            counter += 1
            candidate = f"F{counter:05d}"
            while candidate in used_ids:
                counter += 1
                candidate = f"F{counter:05d}"
            rec["id"] = candidate
        used_ids.add(rec["id"])
        rec.setdefault("added_at", fgstore.now())
        fresh.append(rec)
    merged = {f["id"]: f for f in existing}
    for rec in fresh:
        merged[rec["id"]] = rec
    ordered = list(merged.values())
    fgstore.rewrite_jsonl(session.facts_path, ordered)
    harvest_text = {h["id"]: fglazy.searchable_text(session.harvest(h["id"]))
                    for h in session.harvests()}
    report = fgmodel.validate_facts(ordered, session.entities(), harvest_text)
    fresh_ids = {r["id"] for r in fresh}
    problems = [p for p in report["problems"] if p["ref"] in fresh_ids]
    return len(fresh), problems


def cmd_entity_add(args) -> int:
    _, session = _session(args)
    try:
        count = _ingest_entities(session, args.file)
    except fgstore.StoreError as exc:
        return fail(str(exc))
    report = fgmodel.validate_entities(session.entities())
    session.log("entities_added", count=count)
    return out({"ok": not any(p["level"] == "error" for p in report["problems"]),
                "added": count, "total": report["count"],
                "problems": report["problems"]})


def cmd_fact_add(args) -> int:
    _, session = _session(args)
    try:
        count, problems = _ingest_facts(session, args.file, args.harvest)
    except fgstore.StoreError as exc:
        return fail(str(exc))
    session.log("facts_added", count=count,
                errors=sum(1 for p in problems if p["level"] == "error"))
    return out({"ok": not any(p["level"] == "error" for p in problems),
                "added": count, "total": len(session.facts()), "problems": problems},
               0 if not any(p["level"] == "error" for p in problems) else 1)


def cmd_validate(args) -> int:
    _, session = _session(args)
    entities, facts = session.entities(), session.facts()
    harvest_text = {h["id"]: fglazy.searchable_text(session.harvest(h["id"]))
                    for h in session.harvests()}
    ent = fgmodel.validate_entities(entities)
    fac = fgmodel.validate_facts(facts, entities, harvest_text)
    problems = ent["problems"] + fac["problems"]
    errors = [p for p in problems if p["level"] == "error"]
    return out({"ok": not errors, "entities": ent["count"], "facts": fac["count"],
                "errors": len(errors),
                "warnings": sum(1 for p in problems if p["level"] == "warn"),
                "notes": sum(1 for p in problems if p["level"] == "note"),
                "problems": problems[: args.limit]}, 0 if not errors else 1)


def cmd_compile(args) -> int:
    _, session = _session(args)
    meta, entities, facts = session.meta(), session.entities(), session.facts()
    graph = fgmodel.compile_graph(meta, entities, facts)
    fgstore.write_json(session.graph_path, graph)
    struct = fgdepth.structure(graph)
    session.log("compiled", nodes=len(graph["nodes"]), edges=len(graph["edges"]))
    return out({"ok": True, "graph": str(session.graph_path),
                "nodes": len(graph["nodes"]), "edges": len(graph["edges"]),
                "business_nodes": struct["business_nodes"],
                "business_edges": struct["business_edges"],
                "kind_counts": struct["kind_counts"],
                "prop_conflicts": graph["prop_conflicts"][:10],
                "isolated_nodes": struct["isolated_nodes"][:20]})


def _graph(session) -> dict:
    graph = fgstore.read_json(session.graph_path)
    if graph is None:
        raise fgstore.StoreError("还没有 graph.json，先跑 `fg.py compile`")
    return graph


def cmd_node(args) -> int:
    _, session = _session(args)
    graph = _graph(session)
    node = next((n for n in graph["nodes"] if n["id"] == args.id), None)
    if node is None:
        matches = [n["id"] for n in graph["nodes"] if args.id in n["id"]
                   or args.id in (n.get("caption") or "")]
        return fail(f"没有节点 {args.id}", candidates=matches[:15])
    facts = {f["id"]: f for f in session.facts()}
    props = []
    for key in sorted(node["props"]):
        meta = node["prop_meta"].get(key, {})
        props.append({"key": key, "value": node["props"][key],
                      "unit": meta.get("unit"), "currency": meta.get("currency"),
                      "period": meta.get("period"), "epistemic": meta.get("epistemic"),
                      "harvest": meta.get("harvest_id"),
                      "facts": node["prop_facts"].get(key, []),
                      "quote": (facts.get((node["prop_facts"].get(key) or [""])[0], {})
                                .get("quote") or "")[:200]})
    out_edges = [e for e in graph["edges"] if e["from"] == args.id]
    in_edges = [e for e in graph["edges"] if e["to"] == args.id]
    return out({"id": node["id"], "kind": node["kind"], "caption": node["caption"],
                "name": node["name"], "ids": node["ids"], "aliases": node["aliases"],
                "prop_count": node["prop_count"], "prop_groups": node["prop_groups"],
                "props": props,
                "out_edges": [{"to": e["to"], "relation": e["relation"],
                               "layer": e["layer"], "mechanism": e["mechanism"],
                               "attrs": e["attrs"]} for e in out_edges],
                "in_edges": [{"from": e["from"], "relation": e["relation"],
                              "layer": e["layer"], "mechanism": e["mechanism"],
                              "attrs": e["attrs"]} for e in in_edges]})


# ==========================================================================
# depth / quality
# ==========================================================================
def cmd_depth(args) -> int:
    _, session = _session(args)
    graph = _graph(session)
    meta = session.meta()
    cases = meta.get("mechanism_questions") or []
    if args.case:
        cases = [c for c in cases if c.get("id") in args.case]
    report = {"structure": fgdepth.structure(graph),
              "chains": fgdepth.deep_chains(graph, args.min_hops, args.limit,
                                            args.from_id or None),
              "cases": [fgdepth.answer_case(graph, c) for c in cases]}
    report["ok"] = all(c["ok"] for c in report["cases"]) if report["cases"] else None
    fgstore.write_json(session.reports_dir / "depth.json", report)
    if args.brief:
        chains = report["chains"]
        report = {"ok": report["ok"], "structure": report["structure"],
                  "deepest_hops": chains["deepest_hops"],
                  "independent_at_or_above": chains["independent_at_or_above"],
                  "hop_histogram": chains["hop_histogram"],
                  "cases": [{k: v for k, v in c.items()
                             if k not in ("witnesses", "weak_samples")}
                            for c in report["cases"]]}
    return out(report)


def cmd_quality(args) -> int:
    _, session = _session(args)
    meta = session.meta()
    graph = _graph(session)
    entities, facts = session.entities(), session.facts()
    harvest_text = {h["id"]: fglazy.searchable_text(session.harvest(h["id"]))
                    for h in session.harvests()}
    profile_name = args.profile or meta.get("profile") or "standard"
    profile = PROFILES.get(profile_name, PROFILES["standard"])
    anchors = [a.get("id") if isinstance(a, dict) else a for a in (meta.get("anchors") or [])]

    ent = fgmodel.validate_entities(entities)
    fac = fgmodel.validate_facts(facts, entities, harvest_text)
    richness = fgmodel.node_richness(graph, anchors)
    edges = fgmodel.edge_quality(graph)
    facts_q = fgmodel.fact_quality(facts)
    conflicts = fgmodel.value_conflicts(facts)
    struct = fgdepth.structure(graph)
    chains = fgdepth.deep_chains(graph, 6, 30)
    cases = [fgdepth.answer_case(graph, c) for c in (meta.get("mechanism_questions") or [])]
    usage = _usage_for(session)

    findings = []
    covered, missing_sectors = _sector_coverage(session, meta)

    def note(level, area, issue, fix):
        findings.append({"level": level, "area": area, "issue": issue, "fix": fix})

    for problem in ent["problems"] + fac["problems"]:
        if problem["level"] == "error":
            note("error", "证据", f"{problem['ref']}: {problem['issue']}",
                 "改这条记录后重跑 fg.py compile")
    bad_names = list(edges.get("vague_relations") or []) + list(edges.get("sentence_relations") or [])
    if bad_names:
        note("error", "边命名",
             f"{len(bad_names)} 条关系名空泛或写成了句子：{bad_names[:8]}",
             "改成 2–8 字短语（持股、长协供应、准入约束），对象/品类/限定写进 attrs")
    if facts_q["numeric_usable_ratio"] < 0.95 and facts_q["numeric_count"]:
        note("error", "数值可用性",
             f"只有 {facts_q['numeric_usable_ratio']:.0%} 的数值事实同时具备"
             f"单位/币种与期间",
             "补 unit / currency / period；缺口径的数值等于不能用")

    if not anchors:
        note("guide", "范围", "会话还没有锚点，图不知道围绕谁展开",
             "先和用户对齐，再 fg.py session set --anchor E-xxx=名称:Company")
    for row in richness["anchor_rows"]:
        if row["prop_count"] < 8:
            note("guide", "节点深度",
                 f"锚点 {row['caption']} 只有 {row['prop_count']} 个有证据的属性",
                 "fg.py harvest show <id> --part cells --unused-only；一次利润表应产出几十条属性")
        if row["group_count"] < 5:
            note("guide", "节点深度",
                 f"锚点 {row['caption']} 属性只覆盖 {row['group_count']} 个维度组"
                 f"（{'、'.join(row['prop_groups'])}）",
                 "按 references/NODE_PROFILE.md 补所有权、治理、风险等还没问的组")
    if richness["name_only_nodes"]:
        note("warn", "节点深度",
             f"{len(richness['name_only_nodes'])} 个业务节点只有名字没有任何属性："
             f"{richness['name_only_nodes'][:8]}",
             "要么补属性，要么删掉——只有名字的节点让图看起来大而实际空")
    if missing_sectors:
        first = missing_sectors[0]
        note("guide", "下一步发掘",
             f"还没覆盖的检索扇区：{'、'.join(s['name'] for s in missing_sectors)}",
             f"下一步：fg.py search \"{first['next_query']}\" --tag {first['name']}")
    if edges["missing_mechanism"] or edges["missing_attrs"]:
        note("guide", "边可分析性",
             f"缺机制 {len(edges['missing_mechanism'])} 条，缺属性 {len(edges['missing_attrs'])} 条",
             "写不出机制说明这一跳还没研究透，回去检索，不要先连上再说")
    if edges["layers_used"] < 6:
        note("guide", "机制层次",
             f"只用到 {edges['layers_used']} 个语义层（{ '、'.join(edges['layer_counts']) }）",
             "最常缺政策/预期/二阶反馈，对应扇区 9 和 10，见 references/LAZYSEARCH.md")
    if conflicts:
        note("warn", "证据冲突",
             f"{len(conflicts)} 组同主体同指标同期间但数值不同",
             "用 fg.py align 把口径分歧摆给用户判断，不要静默取一个")
    if struct["isolated_nodes"]:
        note("warn", "结构",
             f"{len(struct['isolated_nodes'])} 个业务节点没有任何边：{struct['isolated_nodes'][:8]}",
             "孤立节点对多跳没有贡献，接上关系或删除")
    if struct["leaf_ratio"] > 0.45:
        note("warn", "结构",
             f"叶节点占 {struct['leaf_ratio']:.0%}，图是「一根主干 + 一圈卫星」的形状",
             "补交叉边，让 2-core 提上来，见 references/DEPTH.md")
    if struct["business_edges"] >= 5 and struct["two_core_ratio"] < 0.30:
        note("warn", "结构",
             f"2-core 只占 {struct['two_core_ratio']:.0%}、桥边率 {struct['bridge_ratio']:.0%}："
             f"图基本是树/链，任何一跳断掉整条链就断，没有任何交叉验证",
             "同一结论要找第二条独立通路，见 references/DEPTH.md 的「独立见证」")
    for anchor in struct["anchors"]:
        if anchor["reach_undirected_ratio"] < 0.85:
            note("warn", "结构",
                 f"从锚点 {anchor['caption']} 只能走到 "
                 f"{anchor['reach_undirected_ratio']:.0%} 的业务节点",
                 "剩下的部分与锚点无路可通，等于游离资料")
    if chains["deepest_hops"] and chains["deepest_hops"] < 4:
        note("guide", "纵深",
             f"目前最深实质路径 {chains['deepest_hops']} 跳",
             "不要为跳数接龙。看 fg.py depth 的 weak_because：缺哪一层就去搜哪一扇区")
    for case in cases:
        if not case["ok"]:
            note("guide", "机制问题",
                 f"{case['id']}「{case['question']}」还未走通：" + "；".join(case["gaps"]),
                 "这是用户点名要能走通的链路，优先补它缺的那几跳，而不是扩大节点数")
    if usage.get("open"):
        note("guide", "信息利用",
             f"检索回来 {usage['total_data_cells']} 个数据单元格，"
             f"只有 {usage['use_ratio']:.0%} 进了图、"
             f"{usage['accounted_ratio']:.0%} 被交代过；"
             f"{usage['open']} 个既没用也没说明为什么不用",
             "fg.py harvest show <id> --part cells --unused-only 逐个处理；"
             "确实不需要的用 fg.py harvest dispose 写明理由")
    unmined = [h["id"] for h in session.harvests() if h.get("state") == "unmined"]
    if unmined:
        note("guide", "信息利用",
             f"{len(unmined)} 次收割还标着 unmined：{unmined[:8]}",
             "fg.py harvest show <id> --part data，草稿写进 drafts/，然后 fg.py harvest mine <id> --done")

    errors = [f for f in findings if f["level"] == "error"]
    report = {
        "ok": not errors, "profile": profile_name, "profile_label": profile["label"],
        "summary": {
            "facts": facts_q, "nodes": {
                "business_nodes": richness["business_node_count"],
                "median_prop_count": richness["median_prop_count"],
                "kind_counts": richness["kind_counts"],
                "anchors": richness["anchor_rows"]},
            "edges": edges,
            "structure": {k: v for k, v in struct.items() if k != "top_hubs"},
            "depth": {"deepest_hops": chains["deepest_hops"],
                      "independent_at_or_above": chains["independent_at_or_above"],
                      "hop_histogram": chains["hop_histogram"]},
            "information_use": {k: v for k, v in usage.items() if k != "harvests"},
            "sectors": {"covered": [s["name"] for s in covered],
                        "missing": [s["name"] for s in missing_sectors]},
        },
        "mechanism_cases": [{k: v for k, v in c.items()
                             if k not in ("witnesses", "weak_samples")} for c in cases],
        "conflicts": conflicts[:20],
        "findings": findings,
        "next": [f["fix"] for f in findings if f["level"] in ("error", "guide")][:6],
        "orientation": {
            "profile": profile_name,
            "label": profile["label"],
            "hint": (
                f"「{profile['label']}」只是发掘方向，不是门槛。"
                f"扇区铺开、锚点做成详表、机制链路走通之后，"
                f"图往往会自然长到大约 {profile['business_nodes']} 个有证据节点、"
                f"{profile['layers']} 个语义层；不要为这些数字接龙或造空壳。"
            ),
        },
        "note": "ok=false 只表示还有证据缺陷或边名写成了句子，不能当结论用。"
                "节点数/边数/跳数不是门槛；看 level=guide 的条目，按它给出的下一步去检索。",
    }
    fgstore.write_json(session.reports_dir / "quality.json", report)
    if args.brief:
        report = {k: v for k, v in report.items() if k != "conflicts"}
    return out(report, 0 if not errors else 1)


# ==========================================================================
# brief（渐进式披露的热上下文）
# ==========================================================================
def cmd_brief(args) -> int:
    cfg, session = _session(args)
    session.ensure_layout()
    meta = session.meta()
    harvests = session.harvests()
    facts = session.facts()
    graph = fgstore.read_json(session.graph_path)
    usage = _usage_for(session) if harvests else {"total_data_cells": 0, "use_ratio": 0,
                                                 "accounted_ratio": 0, "open": 0,
                                                 "harvests": []}
    covered, missing_sectors = _sector_coverage(session, meta)
    stray = _stray_workspace_files(cfg)
    drafts = sorted(p.name for p in session.drafts_dir.glob("*.json")) if session.drafts_dir.exists() else []
    todo = []
    unmined = [h["id"] for h in harvests if h.get("state") == "unmined"]
    if stray:
        todo.append(f"工作区根目录堆了 {', '.join(stray)}，请移到 {session.drafts_dir} 再 mine，不要写在根目录")
    if not meta.get("center_question"):
        todo.append("中心问题还是空的——先和用户对齐要回答什么（fg.py align --stage scope）")
    if not meta.get("anchors"):
        todo.append("还没有锚点——先确定这张图围绕哪家公司/哪只证券展开")
    if not meta.get("mechanism_questions"):
        todo.append("还没有机制问题——纵深要服务于具体问题，否则就是为了多跳而多跳"
                    "（fg.py session set --mechanism-question '{...}'）")
    if missing_sectors:
        first = missing_sectors[0]
        todo.append(f"扇区「{first['name']}」还没检索（还缺 {len(missing_sectors)} 个扇区）。"
                    f"下一步：fg.py search \"{first['next_query']}\" --tag {first['name']}")
    if unmined:
        todo.append(f"{len(unmined)} 次收割还没挖：{unmined[:6]}。"
                    f"草稿写进 {session.drafts_dir}，然后 fg.py harvest mine <id> --done")
    if usage.get("open"):
        worst = sorted(usage["harvests"], key=lambda r: r["accounted_ratio"])[:3]
        todo.append(f"{usage['open']} 个数据单元格既没用也没交代，最欠的是 "
                    + "、".join(f"{r['harvest']}({r['accounted_ratio']:.0%})" for r in worst))
    if graph is None:
        todo.append("还没 compile 过")
    thin = []
    if graph:
        anchors = [a.get("id") if isinstance(a, dict) else a for a in (meta.get("anchors") or [])]
        richness = fgmodel.node_richness(graph, anchors)
        thin = [{"id": r["id"], "caption": r["caption"], "kind": r["kind"],
                 "prop_count": r["prop_count"], "group_count": r["group_count"]}
                for r in richness["nodes"] if r["kind"] in fgmodel.BUSINESS_KINDS
                and r["prop_count"] < 5][:12]
        if thin:
            todo.append(f"{len(thin)} 个业务节点属性少于 5 个，先补锚点一跳邻居")
    open_asks = [e for e in session.ledger()
                 if e.get("kind") == "align" and not e.get("answer")]
    if open_asks:
        todo.append(f"有 {len(open_asks)} 个对齐点还没等到用户答复："
                    + "；".join(e.get("question", "")[:40] for e in open_asks[:3]))
    return out({
        "session": meta.get("session_id"), "topic": meta.get("topic"),
        "as_of": meta.get("as_of"), "profile": meta.get("profile"),
        "center_question": meta.get("center_question"),
        "anchors": meta.get("anchors"),
        "mechanism_questions": meta.get("mechanism_questions"),
        "session_path": str(session.root),
        "drafts_path": str(session.drafts_dir),
        "drafts": drafts,
        "stray_workspace_files": stray,
        "sectors": {"covered": [s["name"] for s in covered],
                    "missing": missing_sectors},
        "counts": {"harvests": len(harvests), "facts": len(facts),
                   "entities": len(session.entities()),
                   "nodes": len(graph["nodes"]) if graph else 0,
                   "edges": len(graph["edges"]) if graph else 0},
        "information_use": {k: v for k, v in usage.items() if k != "harvests"},
        "unmined_harvests": unmined,
        "thin_nodes": thin,
        "open_alignments": [{"stage": e.get("stage"), "question": e.get("question")}
                            for e in open_asks],
        "recent_ledger": session.ledger()[-6:],
        "next": todo or ["跑 fg.py quality：有 error 先修证据；有 guide 就按它的下一步去检索"],
    })


# ==========================================================================
# 人在回路（明文台账，无签名无阻断）
# ==========================================================================
def cmd_align(args) -> int:
    """记录一个对齐点。问题**在对话里**用 AskQuestion 问用户，这里只留下可读记录。"""
    _, session = _session(args)
    entry = session.log("align", stage=args.stage, question=args.question,
                        options=args.option or [], recommendation=args.recommendation or "",
                        why_now=args.why_now or "", refs=args.ref or [],
                        answer=args.answer or "", effect=args.effect or "")
    pending = not args.answer
    return out({"ok": True, "recorded": entry,
                "reminder": "现在在对话里把这个问题连同取舍、证据样本和你的建议摆给用户；"
                            "拿到答复后用 fg.py answer 回填。" if pending
                            else "已连同答复一起记下。"})


def cmd_answer(args) -> int:
    _, session = _session(args)
    entries = session.ledger()
    target = None
    for entry in reversed(entries):
        if entry.get("kind") != "align":
            continue
        if args.question and args.question not in (entry.get("question") or ""):
            continue
        if entry.get("answer"):
            continue
        target = entry
        break
    if target is None:
        return fail("找不到待答复的对齐点；先跑 fg.py align")
    updated = []
    for entry in entries:
        if entry is target:
            entry = {**entry, "answer": args.answer, "effect": args.effect or "",
                     "answered_at": fgstore.now(), "actor": args.actor or "user"}
        updated.append(entry)
    fgstore.rewrite_jsonl(session.ledger_path, updated)
    return out({"ok": True, "question": target.get("question"), "answer": args.answer,
                "effect": args.effect or "",
                "reminder": "把这个答复真正落到工件上（改 facts / entities / "
                            "mechanism_questions / profile），然后 compile + quality。"})


def cmd_ledger(args) -> int:
    _, session = _session(args)
    entries = session.ledger()
    if args.kind:
        entries = [e for e in entries if e.get("kind") in args.kind]
    return out({"count": len(entries), "entries": entries[-args.limit:]})


# ==========================================================================
# neo4j
# ==========================================================================
def cmd_neo4j_ensure(args) -> int:
    cfg, session = _session(args)
    database = _database(cfg, session, args)
    try:
        result = fgneo4j.ensure_database(cfg, database)
    except (fgneo4j.Neo4jError, fgconfig.ConfigError) as exc:
        return fail(str(exc))
    session.update_meta(neo4j_database=database)
    return out({"ok": True, **result, "browser": f"{cfg['neo4j_url']}/browser/",
                "browser_hint": f":use {database}"})


def cmd_neo4j_load(args) -> int:
    cfg, session = _session(args)
    database = _database(cfg, session, args)
    try:
        graph = _graph(session)
    except fgstore.StoreError as exc:
        return fail(str(exc))
    if not args.force:
        report = fgstore.read_json(session.reports_dir / "quality.json")
        blockers = fgmodel.load_blockers(report)
        if blockers:
            return fail("质量报告里还有证据类错误（引文找不到、端点悬空等），"
                        "装进去的图不可回溯。先修这些，或明确用 --force。",
                        errors=[b["issue"] for b in blockers[:10]])
    if args.dry_run:
        nodes, edges = fgneo4j.node_rows(graph), fgneo4j.edge_rows(graph)
        flat_nodes = [row for rows in nodes.values() for row in rows]
        flat_edges = [row for rows in edges.values() for row in rows]
        return out({"ok": True, "dry_run": True, "database": database,
                    "labels": {k: len(v) for k, v in nodes.items()},
                    "relation_types": {k: len(v) for k, v in edges.items()},
                    "sample_node": flat_nodes[0] if flat_nodes else None,
                    "sample_edge": flat_edges[0] if flat_edges else None})
    try:
        result = fgneo4j.load(cfg, database, graph, replace=not args.append)
    except (fgneo4j.Neo4jError, fgconfig.ConfigError) as exc:
        return fail(str(exc))
    grass_path = session.root / "browser.grass"
    grass_path.write_text(fgneo4j.grass(graph), encoding="utf-8")
    session.update_meta(neo4j_database=database)
    session.log("neo4j_loaded", database=database, nodes=result["nodes"],
                edges=result["edges"])
    return out({"ok": True, **result, "grass": str(grass_path),
                "browser": f"{cfg['neo4j_url']}/browser/",
                "next": [f":use {database}",
                         "MATCH (n:FGNode) RETURN n LIMIT 100",
                         f"上传 {grass_path.name} 到 Browser 的 :style 面板"]})


def cmd_neo4j_snapshot(args) -> int:
    cfg, session = _session(args)
    database = _database(cfg, session, args)
    try:
        return out({"ok": True, "database": database,
                    **fgneo4j.snapshot(cfg, database)})
    except (fgneo4j.Neo4jError, fgconfig.ConfigError) as exc:
        return fail(str(exc))


def cmd_neo4j_query(args) -> int:
    cfg, session = _session(args)
    database = _database(cfg, session, args)
    lowered = args.cypher.lower()
    if not args.write and re.search(r"\b(create|merge|delete|set|remove|drop)\b", lowered):
        return fail("这看起来是写操作。读查询直接跑；确实要写请加 --write。")
    try:
        result = fgneo4j.run(cfg, database, args.cypher, timeout=args.timeout)
    except (fgneo4j.Neo4jError, fgconfig.ConfigError) as exc:
        return fail(str(exc))
    return out({"ok": True, "database": database, **result})


def cmd_neo4j_hop(args) -> int:
    cfg, session = _session(args)
    database = _database(cfg, session, args)
    try:
        result = fgneo4j.hop(cfg, database, args.from_id, args.to_id, args.min_hops,
                             args.max_hops, args.limit, args.directed)
    except (fgneo4j.Neo4jError, fgconfig.ConfigError) as exc:
        return fail(str(exc))
    return out({"ok": True, "database": database, **result})


def cmd_neo4j_grass(args) -> int:
    _, session = _session(args)
    graph = _graph(session)
    path = Path(args.output) if args.output else session.root / "browser.grass"
    path.write_text(fgneo4j.grass(graph), encoding="utf-8")
    return out({"ok": True, "path": str(path),
                "hint": "Neo4j Browser 左侧 :style 面板 → Drop a GraSS file here"})


def cmd_neo4j_wipe(args) -> int:
    cfg, session = _session(args)
    database = _database(cfg, session, args)
    if not args.confirm:
        return fail(f"这会清空 {database}。确认请加 --confirm。")
    try:
        return out({"ok": True, **fgneo4j.wipe(cfg, database, args.drop)})
    except (fgneo4j.Neo4jError, fgconfig.ConfigError) as exc:
        return fail(str(exc))


# ==========================================================================
# export / template
# ==========================================================================
def _write_csv(path: Path, header: list, rows: list) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


def cmd_export(args) -> int:
    _, session = _session(args)
    graph = _graph(session)
    target = Path(args.output).resolve()
    target.mkdir(parents=True, exist_ok=True)
    meta = session.meta()

    prop_keys = sorted({k for n in graph["nodes"] for k in n["props"]})
    _write_csv(target / "nodes.csv",
               ["编号", "类型", "显示名", "全称", "属性数", "维度组", "标识", "事实"]
               + prop_keys,
               [[n["id"], n["kind"], n["caption"], n["name"], n["prop_count"],
                 "|".join(n["prop_groups"]),
                 "|".join(f"{k}={v}" for k, v in n["ids"].items()),
                 "|".join(n["fact_ids"])]
                + [n["props"].get(k, "") for k in prop_keys]
                for n in graph["nodes"]])
    attr_keys = sorted({k for e in graph["edges"] for k in e["attrs"]})
    _write_csv(target / "relationships.csv",
               ["编号", "起点", "关系", "终点", "机制", "语义层", "认识状态", "期间",
                "置信度", "事实", "原文"] + attr_keys,
               [[e["id"], e["from"], e["relation"], e["to"], e["mechanism"], e["layer"],
                 e["epistemic"], e["period"], e["confidence"], "|".join(e["fact_ids"]),
                 e["quote"]] + [e["attrs"].get(k, "") for k in attr_keys]
                for e in graph["edges"]])
    fgstore.write_json(target / "graph.json", graph)
    fgstore.write_json(target / "session.json", meta)
    shutil.copy2(session.facts_path, target / "facts.jsonl")
    shutil.copy2(session.entities_path, target / "entities.jsonl")
    shutil.copy2(session.ledger_path, target / "ledger.jsonl")
    if session.dispositions_path.exists():
        shutil.copy2(session.dispositions_path, target / "dispositions.json")
    (target / "browser.grass").write_text(fgneo4j.grass(graph), encoding="utf-8")
    if session.reports_dir.exists():
        shutil.copytree(session.reports_dir, target / "reports", dirs_exist_ok=True)
    if args.include_harvest and session.harvest_dir.exists():
        shutil.copytree(session.harvest_dir, target / "harvest", dirs_exist_ok=True)
    readme = [
        f"# {meta.get('topic')}", "",
        f"- 中心问题：{meta.get('center_question') or '(未填)'}",
        f"- 数据时点：{meta.get('as_of')}", f"- 质量档：{meta.get('profile')}",
        f"- 节点 {len(graph['nodes'])} / 关系 {len(graph['edges'])}",
        f"- Neo4j 库：{meta.get('neo4j_database')}", "",
        "## 文件", "",
        "| 文件 | 说明 |", "| --- | --- |",
        "| graph.json | 编译后的图，节点属性与边属性都在这里 |",
        "| nodes.csv / relationships.csv | 中文表头，Excel 可直接看 |",
        "| facts.jsonl | 每条原子事实，含 quote 与 harvest_id，可回溯到检索原文 |",
        "| entities.jsonl | 实体注册表 |",
        "| ledger.jsonl | 与用户商量过的每个对齐点及其答复 |",
        "| reports/ | 质量、纵深、信息利用率报告 |",
        "| browser.grass | Neo4j Browser 配色，拖进 :style 面板 |",
    ]
    if args.include_harvest:
        readme.append("| harvest/ | 每次 LazySearch 检索的完整原始返回 |")
    (target / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")
    session.log("exported", output=str(target))
    return out({"ok": True, "output": str(target),
                "files": sorted(p.name for p in target.iterdir())})


def cmd_template(args) -> int:
    root = SKILL_ROOT / "assets" / "templates"
    if not args.name:
        return out({"templates": sorted(p.stem for p in root.glob("*.json"))})
    path = root / f"{args.name}.json"
    if not path.exists():
        return fail(f"没有模板 {args.name}",
                    available=sorted(p.stem for p in root.glob("*.json")))
    payload = json.loads(fgstore.read_text(path))
    default_name = TEMPLATE_DEFAULT_NAME.get(args.name, f"{args.name}.json")
    try:
        _, session = _session(args)
        session.ensure_layout()
        target = session.resolve_output(args.output, default_name)
        fgstore.write_json(target, payload)
        return out({"ok": True, "template": args.name, "written": str(target),
                    "hint": "改完后 fg.py harvest mine <id> --done 会读取 drafts/ 下的草稿；"
                            "不要把 entities.json / facts.json 写到工作区根目录"})
    except fgstore.StoreError:
        if args.output:
            target = fgstore.write_json(Path(args.output), payload)
            return out({"ok": True, "template": args.name, "written": str(target)})
        return out(payload)


# ==========================================================================
# argparse
# ==========================================================================
def _add_global_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session", help="会话 id（默认唯一会话或 $FG_SESSION）")
    parser.add_argument("--database", help="Neo4j 数据库名（默认取会话里的）")
    parser.add_argument("--neo4j-url", dest="neo4j_url")
    parser.add_argument("--lazysearch-url", dest="lazysearch_url")
    parser.add_argument("--out-file", help="把 JSON 输出直接写成 UTF-8 文件。"
                                          "Windows PowerShell 的 `>` 会按控制台宽度折断长字符串，"
                                          "要落盘就用这个")


def _spread_global_options(parser: argparse.ArgumentParser) -> None:
    """把全局选项复制到每个叶子子命令，这样 `fg quality --out-file x` 和
    `fg --out-file x quality` 都能用 —— 别让参数位置成为使用障碍。"""
    for action in parser._actions:  # noqa: SLF001 - argparse 没有公开的遍历接口
        if not isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            continue
        for child in dict.fromkeys(action.choices.values()):
            if any(isinstance(a, argparse._SubParsersAction) for a in child._actions):  # noqa: SLF001
                _spread_global_options(child)
            else:
                _add_global_options(child)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fg.py", description="金融图谱工具箱")
    _add_global_options(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="自检配置 / LazySearch / Neo4j / 会话")
    doctor.add_argument("--verbose", action="store_true",
                        help="显示库名等本机细节（默认输出不含主机/账号/库名，可直接贴进 issue）")
    doctor.set_defaults(func=cmd_doctor)

    # --lazysearch-url / --neo4j-url 来自全局选项（同名同 dest），这里不再重复定义
    setup = sub.add_parser("setup", help="生成工作区本地配置（地址/账号/密码只存在这里）")
    setup.add_argument("--neo4j-user", dest="neo4j_user")
    setup.add_argument("--neo4j-password", dest="neo4j_password",
                       help="留空则交互输入，避免密码进 shell 历史")
    setup.add_argument("--allow-http-auth", dest="allow_http_auth",
                       action=argparse.BooleanOptionalAction, default=None,
                       help="局域网明文 HTTP 连 Neo4j 时需要（默认开）")
    setup.add_argument("--non-interactive", action="store_true")
    setup.set_defaults(func=cmd_setup)

    env = sub.add_parser("env", help="环境档案：本机有哪些数据表与检索工具（本地文件，不入库）")
    env.add_argument("--init", action="store_true", help="从模板创建")
    env.add_argument("--force", action="store_true")
    env.add_argument("--grep")
    env.set_defaults(func=cmd_env)

    ses = sub.add_parser("session", help="会话管理").add_subparsers(dest="sub", required=True)
    new = ses.add_parser("new", help="新建会话")
    new.add_argument("topic")
    new.add_argument("--id")
    new.add_argument("--center-question")
    new.add_argument("--as-of")
    new.add_argument("--profile", choices=sorted(PROFILES), default="standard")
    new.add_argument("--anchor", action="append", metavar="ID=名称:类型")
    new.add_argument("--include", action="append")
    new.add_argument("--exclude", action="append")
    new.add_argument("--market", action="append")
    new.add_argument("--period")
    new.add_argument("--purpose")
    new.set_defaults(func=cmd_session_new)
    ses.add_parser("list").set_defaults(func=cmd_session_list)
    ses.add_parser("show").set_defaults(func=cmd_session_show)
    sset = ses.add_parser("set", help="改中心问题/锚点/机制问题/质量档")
    sset.add_argument("--center-question")
    sset.add_argument("--profile", choices=sorted(PROFILES))
    sset.add_argument("--as-of")
    sset.add_argument("--anchor", action="append", metavar="ID=名称:类型")
    sset.add_argument("--mechanism-question", action="append", metavar="JSON")
    sset.set_defaults(func=cmd_session_set)

    sub.add_parser("brief", help="当前热上下文：缺什么、下一步做什么").set_defaults(func=cmd_brief)

    search = sub.add_parser("search", help="调 LazySearch 并落盘")
    search.add_argument("query")
    search.add_argument("--channel", choices=[fglazy.CHANNEL_HTTP, fglazy.CHANNEL_MCP],
                        default=fglazy.CHANNEL_HTTP)
    search.add_argument("--tag", action="append")
    search.add_argument("--note")
    search.add_argument("--timeout", type=int)
    search.add_argument("--preview", type=int, default=1200)
    search.set_defaults(func=cmd_search)

    harvest = sub.add_parser("harvest", help="收割仓库").add_subparsers(dest="sub", required=True)
    hadd = harvest.add_parser("add", help="把 MCP/人工获得的返回落盘")
    hadd.add_argument("query")
    hadd.add_argument("--file")
    hadd.add_argument("--channel", default=fglazy.CHANNEL_MCP)
    hadd.add_argument("--tag", action="append")
    hadd.add_argument("--note")
    hadd.set_defaults(func=cmd_harvest_add)
    hlist = harvest.add_parser("list")
    hlist.add_argument("--state", choices=["unmined", "partial", "mined"])
    hlist.add_argument("--tag", action="append")
    hlist.set_defaults(func=cmd_harvest_list)
    hshow = harvest.add_parser("show", help="分层取用一次收割（渐进式披露）")
    hshow.add_argument("id")
    hshow.add_argument("--part", default="summary",
                       choices=["summary", "meta", "answer", "data", "plan",
                                "provenance", "cells"])
    hshow.add_argument("--block", type=int, help="只看 data 里的某一块")
    hshow.add_argument("--grep")
    hshow.add_argument("--max-chars", type=int, default=6000)
    hshow.add_argument("--limit", type=int)
    hshow.add_argument("--unused-only", action="store_true")
    hshow.set_defaults(func=cmd_harvest_show)
    hmine = harvest.add_parser("mine", help="提交挖掘成果")
    hmine.add_argument("id")
    hmine.add_argument("--facts")
    hmine.add_argument("--entities")
    hmine.add_argument("--done", action="store_true", help="这次收割已挖尽")
    hmine.set_defaults(func=cmd_harvest_mine)
    hdis = harvest.add_parser("dispose", help="说明某部分数据为什么不用")
    hdis.add_argument("id")
    hdis.add_argument("--scope", required=True, help="列名或上下文片段，* 表示整次")
    hdis.add_argument("--reason", required=True)
    hdis.set_defaults(func=cmd_harvest_dispose)

    usage = sub.add_parser("usage", help="信息利用率：检索回来的东西有多少真的用了")
    usage.add_argument("--id", action="append")
    usage.set_defaults(func=cmd_usage)

    ent = sub.add_parser("entity", help="实体").add_subparsers(dest="sub", required=True)
    eadd = ent.add_parser("add")
    eadd.add_argument("file")
    eadd.set_defaults(func=cmd_entity_add)

    fact = sub.add_parser("fact", help="事实").add_subparsers(dest="sub", required=True)
    fadd = fact.add_parser("add")
    fadd.add_argument("file")
    fadd.add_argument("--harvest", help="默认 harvest_id")
    fadd.set_defaults(func=cmd_fact_add)

    val = sub.add_parser("validate", help="校验事实与实体的内容质量")
    val.add_argument("--limit", type=int, default=80)
    val.set_defaults(func=cmd_validate)

    sub.add_parser("compile", help="由事实编译 graph.json").set_defaults(func=cmd_compile)

    node = sub.add_parser("node", help="看一个节点的完整详表")
    node.add_argument("id")
    node.set_defaults(func=cmd_node)

    depth = sub.add_parser("depth", help="纵深报告")
    depth.add_argument("--min-hops", type=int, default=6)
    depth.add_argument("--limit", type=int, default=20)
    depth.add_argument("--case", action="append")
    depth.add_argument("--from-id", action="append")
    depth.add_argument("--brief", action="store_true")
    depth.set_defaults(func=cmd_depth)

    qual = sub.add_parser("quality", help="质量报告（只判内容，不判格式）")
    qual.add_argument("--profile", choices=sorted(PROFILES))
    qual.add_argument("--brief", action="store_true")
    qual.set_defaults(func=cmd_quality)

    align = sub.add_parser("align", help="记录一个对齐点（问题请在对话里问用户）")
    align.add_argument("--stage", required=True)
    align.add_argument("--question", required=True)
    align.add_argument("--option", action="append")
    align.add_argument("--recommendation")
    align.add_argument("--why-now")
    align.add_argument("--ref", action="append")
    align.add_argument("--answer")
    align.add_argument("--effect")
    align.set_defaults(func=cmd_align)

    ans = sub.add_parser("answer", help="回填用户对某个对齐点的答复")
    ans.add_argument("answer")
    ans.add_argument("--question", help="匹配哪个对齐点（子串）")
    ans.add_argument("--effect")
    ans.add_argument("--actor")
    ans.set_defaults(func=cmd_answer)

    led = sub.add_parser("ledger", help="看人在回路台账")
    led.add_argument("--kind", action="append")
    led.add_argument("--limit", type=int, default=40)
    led.set_defaults(func=cmd_ledger)

    neo = sub.add_parser("neo4j", help="Neo4j 操作").add_subparsers(dest="sub", required=True)
    neo.add_parser("ensure-db", help="建库+约束+索引").set_defaults(func=cmd_neo4j_ensure)
    nload = neo.add_parser("load", help="整库替换装载并读回核对")
    nload.add_argument("--dry-run", action="store_true")
    nload.add_argument("--append", action="store_true", help="不清空已有内容")
    nload.add_argument("--force", action="store_true", help="证据类错误也装")
    nload.set_defaults(func=cmd_neo4j_load)
    neo.add_parser("snapshot", help="库里现在有什么").set_defaults(func=cmd_neo4j_snapshot)
    nq = neo.add_parser("query", help="跑 Cypher")
    nq.add_argument("cypher")
    nq.add_argument("--write", action="store_true")
    nq.add_argument("--timeout", type=int, default=180)
    nq.set_defaults(func=cmd_neo4j_query)
    nh = neo.add_parser("hop", help="在库里验证多跳路径")
    nh.add_argument("--from-id", dest="from_id", required=True)
    nh.add_argument("--to-id", dest="to_id")
    nh.add_argument("--min-hops", type=int, default=2)
    nh.add_argument("--max-hops", type=int, default=8)
    nh.add_argument("--limit", type=int, default=10)
    nh.add_argument("--directed", action="store_true")
    nh.set_defaults(func=cmd_neo4j_hop)
    ng = neo.add_parser("grass", help="生成 Browser 配色")
    ng.add_argument("--output")
    ng.set_defaults(func=cmd_neo4j_grass)
    nw = neo.add_parser("wipe")
    nw.add_argument("--confirm", action="store_true")
    nw.add_argument("--drop", action="store_true", help="连库一起删")
    nw.set_defaults(func=cmd_neo4j_wipe)

    exp = sub.add_parser("export", help="导出交付包")
    exp.add_argument("--output", required=True)
    exp.add_argument("--include-harvest", action="store_true")
    exp.set_defaults(func=cmd_export)

    tpl = sub.add_parser("template", help="看 JSON 模板")
    tpl.add_argument("name", nargs="?")
    tpl.add_argument("--output", help="直接写成 UTF-8 文件（Windows 上比 `>` 可靠）")
    tpl.set_defaults(func=cmd_template)

    _spread_global_options(parser)
    return parser


GLOBAL_KEYS = ("session", "database", "neo4j_url", "lazysearch_url", "out_file")


def main(argv=None) -> int:
    global _OUT_FILE
    argv = list(sys.argv[1:] if argv is None else argv)
    # 全局选项在每个子命令上都注册了一份，而 argparse 的子解析器默认值会盖掉
    # 父层已解析的值。先用一个只认全局选项的预解析器兜住前置写法。
    pre_parser = argparse.ArgumentParser(add_help=False)
    _add_global_options(pre_parser)
    pre, _ = pre_parser.parse_known_args(argv)
    args = build_parser().parse_args(argv)
    for key in GLOBAL_KEYS:
        if not getattr(args, key, None) and getattr(pre, key, None):
            setattr(args, key, getattr(pre, key))
    if getattr(args, "out_file", None):
        _OUT_FILE = Path(args.out_file)
    try:
        return args.func(args)
    except (fgstore.StoreError, fgconfig.ConfigError, fglazy.LazyError,
            fgneo4j.Neo4jError) as exc:
        return fail(str(exc))
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
