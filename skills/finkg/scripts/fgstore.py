"""会话存储层：目录、原子写、JSONL、harvest 仓库、明文人在回路台账。

设计取向：一切都是可读明文，任何时刻可以用编辑器打开检查或手工修补。
没有哈希链、没有签名、没有一次性 token —— 台账只是"我们商量过什么"的可读记录。
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "finkg/1"


class StoreError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def slug(text: str, fallback: str = "topic", maxlen: int = 40) -> str:
    """把任意主题压成可用于目录名与 Neo4j 库名的 ASCII slug。"""
    norm = unicodedata.normalize("NFKD", str(text or ""))
    ascii_part = re.sub(r"[^A-Za-z0-9]+", "-", norm.encode("ascii", "ignore").decode()).strip("-")
    if not ascii_part:
        # 中文主题拿不到 ASCII，用码点摘要保证稳定且可读性可接受
        digest = "".join(f"{ord(c):x}" for c in str(text or "")[:6]) or "0"
        ascii_part = f"{fallback}-{digest}"
    return ascii_part.lower()[:maxlen].strip("-") or fallback


def neo4j_db_name(session_id: str) -> str:
    """Neo4j 库名：3-63 字符，仅 ASCII 字母数字点横线，不得以 system 开头。"""
    name = re.sub(r"[^a-z0-9.-]+", "-", slug(session_id, "graph", 55)).strip("-.")
    name = f"fg-{name}" if not name.startswith("fg-") else name
    name = name[:63].rstrip("-.")
    while len(name) < 3:
        name += "0"
    return name


_BOMS = ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe\x00\x00", "utf-32-le"),
         (b"\x00\x00\xfe\xff", "utf-32-be"), (b"\xff\xfe", "utf-16-le"),
         (b"\xfe\xff", "utf-16-be"))


def read_text(path: Path) -> str:
    """读文本并自动识别 BOM。

    Windows 上 PowerShell 的 `>` 重定向会写出带 BOM 的 UTF-16，
    直接按 utf-8 读会炸。这里统一嗅探，省掉一类无谓的报错。
    """
    raw = Path(path).read_bytes()
    for bom, encoding in _BOMS:
        if raw.startswith(bom):
            return raw.decode(encoding).lstrip("\ufeff")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StoreError(
            f"{path} 不是 UTF-8 也不带可识别的 BOM（位置 {exc.start} 处 0x{raw[exc.start]:02x}）。"
            f"在 PowerShell 里生成文件请用 `fg.py template <名称> --output <文件>` "
            f"或 `| Out-File -Encoding utf8`，不要用 `>`。") from exc


def _json_hint(path: Path, exc: json.JSONDecodeError) -> str:
    hint = f"{path} 不是合法 JSON：{exc.msg}（第 {exc.lineno} 行第 {exc.colno} 列）。"
    if "control character" in exc.msg.lower():
        hint += ("看起来字符串中间被塞进了换行——Windows PowerShell 会按控制台宽度折断"
                 "原生命令的输出，`>` 和 `Out-File` 都救不了。"
                 "要落盘请让工具自己写：`fg.py template <名称> --output <文件>` 或"
                 "任意命令加 `--out-file <文件>`；或者直接用编辑器/文件写入工具生成 JSON。")
    return hint


def write_json(path: Path, payload) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def read_json(path: Path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    text = read_text(path)
    if not text.strip():
        return default
    return json.loads(text)


def append_jsonl(path: Path, records) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(records, dict):
        records = [records]
    with path.open("a", encoding="utf-8") as fh:
        count = 0
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            count += 1
    return count


def read_jsonl(path: Path) -> list:
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for lineno, line in enumerate(read_text(path).splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise StoreError(f"{path}:{lineno} 不是合法 JSON 行: {exc}") from exc
    return out


def rewrite_jsonl(path: Path, records) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    return len(records)


def load_records(path: Path) -> list:
    """接受 JSONL、JSON 数组，或单个 JSON 对象；统一返回列表。"""
    path = Path(path)
    if not path.exists():
        raise StoreError(f"文件不存在: {path}")
    text = read_text(path).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise StoreError(_json_hint(path, exc)) from exc
        if not isinstance(data, list):
            raise StoreError(f"{path} 顶层必须是数组或对象")
        return data
    if text.startswith("{") and text.count("\n{") == 0 and text.rstrip().endswith("}"):
        try:
            single = json.loads(text)
        except json.JSONDecodeError:
            single = None
        if isinstance(single, dict):
            return [single]
    return read_jsonl(path)


class Session:
    """一个研究主题的全部落盘状态。"""

    def __init__(self, root: Path):
        self.root = Path(root)

    # ---- 路径 ----------------------------------------------------------
    @property
    def meta_path(self) -> Path:
        return self.root / "session.json"

    @property
    def harvest_dir(self) -> Path:
        return self.root / "harvest"

    @property
    def harvest_index_path(self) -> Path:
        return self.harvest_dir / "index.json"

    @property
    def entities_path(self) -> Path:
        return self.root / "entities.jsonl"

    @property
    def facts_path(self) -> Path:
        return self.root / "facts.jsonl"

    @property
    def dispositions_path(self) -> Path:
        return self.root / "dispositions.json"

    @property
    def graph_path(self) -> Path:
        return self.root / "graph.json"

    @property
    def ledger_path(self) -> Path:
        return self.root / "ledger.jsonl"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def drafts_dir(self) -> Path:
        """挖掘草稿（entities.json / facts.json）只放这里，不要堆工作区根目录。"""
        return self.root / "drafts"

    def ensure_layout(self) -> "Session":
        self.harvest_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        return self

    def resolve_input(self, spec: str | Path) -> Path:
        """读文件：绝对路径原样；相对路径优先会话 drafts/，再会话根，最后 cwd。"""
        path = Path(spec)
        if path.is_absolute():
            if not path.exists():
                raise StoreError(f"文件不存在: {path}")
            return path
        candidates = []
        if len(path.parts) > 1:
            candidates.append(self.root / path)
        candidates.extend([self.drafts_dir / path.name, self.root / path, Path.cwd() / path])
        seen = set()
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            if resolved.is_file():
                return resolved
        raise StoreError(
            f"找不到 {spec}。草稿请写到 {self.drafts_dir}，不要堆在工作区根目录。")

    def resolve_output(self, spec: str | Path | None, default_name: str) -> Path:
        """写文件：未指定或相对文件名 → 会话 drafts/；带目录的相对路径相对会话根。"""
        if spec is None:
            return self.drafts_dir / default_name
        path = Path(spec)
        if path.is_absolute():
            return path
        if len(path.parts) > 1:
            return self.root / path
        return self.drafts_dir / path.name

    # ---- 生命周期 ------------------------------------------------------
    @classmethod
    def create(cls, sessions_root: Path, session_id: str, meta: dict) -> "Session":
        root = Path(sessions_root) / session_id
        if root.exists():
            raise StoreError(f"会话已存在: {root}（换个 --id，或直接继续用它）")
        obj = cls(root)
        obj.ensure_layout()
        payload = {"schema": SCHEMA, "session_id": session_id, "created_at": now(),
                   "updated_at": now(), **meta}
        write_json(obj.meta_path, payload)
        write_json(obj.harvest_index_path, {"schema": SCHEMA, "harvests": []})
        write_json(obj.dispositions_path, {"schema": SCHEMA, "unused": {}})
        obj.facts_path.touch()
        obj.entities_path.touch()
        obj.ledger_path.touch()
        return obj

    @classmethod
    def resolve(cls, sessions_root: Path, session_id: str | None) -> "Session":
        sessions_root = Path(sessions_root)
        if session_id:
            root = sessions_root / session_id
            if not (root / "session.json").exists():
                raise StoreError(f"找不到会话 {session_id}（在 {sessions_root} 下）")
            return cls(root)
        env = os.environ.get("FG_SESSION")
        if env:
            return cls.resolve(sessions_root, env)
        found = sorted(p.parent for p in sessions_root.glob("*/session.json"))
        if not found:
            raise StoreError(f"{sessions_root} 下还没有任何会话，先跑 `fg.py session new`")
        if len(found) > 1:
            newest = max(found, key=lambda p: (p / "session.json").stat().st_mtime)
            names = ", ".join(p.name for p in found)
            raise StoreError(
                f"有多个会话（{names}）。用 --session 指定，或设置 FG_SESSION。"
                f"最近改动的是 {newest.name}。")
        return cls(found[0])

    @staticmethod
    def list_all(sessions_root: Path) -> list:
        out = []
        for meta_path in sorted(Path(sessions_root).glob("*/session.json")):
            meta = read_json(meta_path, {}) or {}
            out.append({"session_id": meta.get("session_id", meta_path.parent.name),
                        "topic": meta.get("topic", ""),
                        "as_of": meta.get("as_of", ""),
                        "updated_at": meta.get("updated_at", ""),
                        "path": str(meta_path.parent)})
        return out

    # ---- 元数据 --------------------------------------------------------
    def meta(self) -> dict:
        data = read_json(self.meta_path)
        if data is None:
            raise StoreError(f"{self.meta_path} 缺失或为空")
        return data

    def update_meta(self, **changes) -> dict:
        data = self.meta()
        data.update(changes)
        data["updated_at"] = now()
        write_json(self.meta_path, data)
        return data

    def database(self) -> str:
        meta = self.meta()
        return meta.get("neo4j_database") or neo4j_db_name(meta["session_id"])

    # ---- harvest -------------------------------------------------------
    def harvest_index(self) -> dict:
        return read_json(self.harvest_index_path, {"schema": SCHEMA, "harvests": []})

    def next_harvest_id(self) -> str:
        used = {h["id"] for h in self.harvest_index().get("harvests", [])}
        n = 1
        while f"h-{n:04d}" in used:
            n += 1
        return f"h-{n:04d}"

    def harvest_path(self, harvest_id: str) -> Path:
        return self.harvest_dir / f"{harvest_id}.json"

    def save_harvest(self, record: dict, summary: dict) -> Path:
        path = self.harvest_path(record["id"])
        write_json(path, record)
        index = self.harvest_index()
        rows = [h for h in index.get("harvests", []) if h["id"] != record["id"]]
        rows.append(summary)
        rows.sort(key=lambda h: h["id"])
        index["harvests"] = rows
        index["schema"] = SCHEMA
        write_json(self.harvest_index_path, index)
        return path

    def harvest(self, harvest_id: str) -> dict:
        data = read_json(self.harvest_path(harvest_id))
        if data is None:
            raise StoreError(f"没有 harvest {harvest_id}；`fg.py harvest list` 看看有哪些")
        return data

    def harvests(self) -> list:
        return self.harvest_index().get("harvests", [])

    def set_harvest_state(self, harvest_id: str, **changes) -> dict:
        index = self.harvest_index()
        for row in index.get("harvests", []):
            if row["id"] == harvest_id:
                row.update(changes)
                write_json(self.harvest_index_path, index)
                return row
        raise StoreError(f"没有 harvest {harvest_id}")

    # ---- 事实与实体 ----------------------------------------------------
    def facts(self) -> list:
        return read_jsonl(self.facts_path)

    def entities(self) -> list:
        return read_jsonl(self.entities_path)

    def dispositions(self) -> dict:
        return read_json(self.dispositions_path, {"schema": SCHEMA, "unused": {}})

    def save_dispositions(self, data: dict) -> Path:
        data["schema"] = SCHEMA
        return write_json(self.dispositions_path, data)

    def next_fact_id(self) -> str:
        used = {f.get("id") for f in self.facts()}
        n = 1
        while f"F{n:05d}" in used:
            n += 1
        return f"F{n:05d}"

    # ---- 台账 ----------------------------------------------------------
    def ledger(self) -> list:
        return read_jsonl(self.ledger_path)

    def log(self, kind: str, **fields) -> dict:
        entry = {"ts": now(), "kind": kind, **fields}
        append_jsonl(self.ledger_path, entry)
        return entry
