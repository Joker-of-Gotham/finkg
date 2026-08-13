"""配置解析：LazySearch 端点、Neo4j 端点、会话根目录。仅用标准库。

**这个仓库不携带任何部署信息。** 端点地址、用户名、密码都没有默认值，
必须由使用者在自己的工作区里配置，未配置时命令会明确报错并给出配置步骤。
这样公开的代码与文档里不会出现任何主机、账号或口令。

优先级（后者覆盖前者）：
    内置默认（仅非部署相关项）
      < skill 自带 fg.defaults.json（仅非部署相关项）
      < 工作区 financial_graph.json（可提交的团队共享项，不含密码）
      < 工作区 financial_graph.local.json（本机私有，唯一允许放密码的地方）
      < FG_* 环境变量
      < CLI 显式参数

工作区 = FG_WORKSPACE，或从当前目录向上查找含 .git / AGENTS.md /
financial_graph.local.json 等标记的目录。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]

# 部署相关项一律留空，逼使用者显式配置；其余是与部署无关的协议/习惯默认值。
DEFAULTS = {
    "lazysearch_url": "",
    "lazysearch_mcp_path": "/mcp",
    "lazysearch_timeout": 600,
    "neo4j_url": "",
    "neo4j_user": "",
    "neo4j_password": "",
    "neo4j_allow_http_auth": False,
    "neo4j_database": "",
    "sessions_dir": "financial-graph-sessions",
}

# 只允许出现在工作区 local 文件或 FG_* 环境变量里，其他配置源里一律忽略。
SECRET_KEYS = ("neo4j_password",)
# 未配置就无法工作的部署项。
DEPLOYMENT_KEYS = ("lazysearch_url", "neo4j_url", "neo4j_user", "neo4j_password")

LOCAL_FILE = "financial_graph.local.json"
ENV_FILE = "finkg.environment.md"
_MARKERS = (".git", "AGENTS.md", LOCAL_FILE, "financial_graph.json", ".claude", ".agents")


class ConfigError(RuntimeError):
    pass


def workspace_root(start: str | os.PathLike | None = None) -> Path:
    env = os.environ.get("FG_WORKSPACE")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(start or Path.cwd()).resolve()
    for cand in (here, *here.parents):
        if any((cand / m).exists() for m in _MARKERS):
            return cand
    return here


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} 不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} 顶层必须是 JSON 对象")
    return data


def _absorb(cfg: dict, path: Path, allow_secret: bool) -> None:
    if not path.exists():
        return
    for key, value in _read_json(path).items():
        if key not in DEFAULTS:
            continue
        if key in SECRET_KEYS and not allow_secret:
            continue
        cfg[key] = value


def _coerce(cfg: dict) -> dict:
    try:
        cfg["lazysearch_timeout"] = int(cfg["lazysearch_timeout"])
    except (TypeError, ValueError):
        cfg["lazysearch_timeout"] = DEFAULTS["lazysearch_timeout"]
    raw = cfg.get("neo4j_allow_http_auth")
    if isinstance(raw, str):
        cfg["neo4j_allow_http_auth"] = raw.strip().lower() in ("1", "true", "yes", "on")
    else:
        cfg["neo4j_allow_http_auth"] = bool(raw)
    for key in ("lazysearch_url", "neo4j_url"):
        cfg[key] = str(cfg[key] or "").strip().rstrip("/")
    cfg["lazysearch_mcp_path"] = "/" + str(cfg["lazysearch_mcp_path"] or "mcp").strip("/")
    return cfg


def load(overrides: dict | None = None, start=None) -> dict:
    """返回完整配置，附带 `_workspace`、`_sessions_root`、`_env_file` 派生路径。"""
    cfg = dict(DEFAULTS)
    _absorb(cfg, SKILL_ROOT / "fg.defaults.json", allow_secret=False)
    root = workspace_root(start)
    _absorb(cfg, root / "financial_graph.json", allow_secret=False)
    _absorb(cfg, root / LOCAL_FILE, allow_secret=True)
    for key in DEFAULTS:
        env = os.environ.get("FG_" + key.upper())
        if env is not None:
            cfg[key] = env
    for key, value in (overrides or {}).items():
        if value not in (None, "") and key in DEFAULTS:
            cfg[key] = value
    cfg = _coerce(cfg)
    cfg["_workspace"] = str(root)
    sessions = Path(cfg["sessions_dir"])
    cfg["_sessions_root"] = str(sessions if sessions.is_absolute() else root / sessions)
    cfg["_local_config"] = str(root / LOCAL_FILE)
    cfg["_env_file"] = str(root / ENV_FILE)
    return cfg


def redacted(cfg: dict) -> dict:
    """可安全打印/贴进 issue 的配置视图：地址与账号只显示是否已配置。"""
    out = {}
    for key, value in cfg.items():
        if key.startswith("_"):
            continue
        if key in SECRET_KEYS:
            out[key] = "<已配置>" if value else "<未配置>"
        elif key in DEPLOYMENT_KEYS:
            out[key] = "<已配置>" if value else "<未配置>"
        else:
            out[key] = value
    return out


def configured(cfg: dict) -> dict:
    """每个部署项是否已配置，供 doctor 与错误提示使用。"""
    return {key: bool(cfg.get(key)) for key in DEPLOYMENT_KEYS}


def setup_hint(cfg: dict, keys=DEPLOYMENT_KEYS) -> str:
    missing = [k for k in keys if not cfg.get(k)]
    if not missing:
        return ""
    sample = {
        "lazysearch_url": "http://<你的 LazySearch 主机>:<端口>",
        "neo4j_url": "http://<你的 Neo4j 主机>:7474",
        "neo4j_user": "<用户名>",
        "neo4j_password": "<密码>",
    }
    body = ",\n  ".join(f'"{k}": "{sample[k]}"' for k in missing)
    return (
        f"缺少配置：{'、'.join(missing)}。\n"
        f"请在工作区 {cfg.get('_local_config', LOCAL_FILE)} 里补上（该文件必须 gitignore）：\n"
        f"{{\n  {body},\n  \"neo4j_allow_http_auth\": true\n}}\n"
        f"也可以用环境变量：" + "、".join("FG_" + k.upper() for k in missing) + "\n"
        f"或者跑 `fg.py setup --help` 让工具帮你生成。"
    )


def require(cfg: dict, *keys: str) -> None:
    """用到某个端点/账号之前调用；未配置时给出可照做的指引而不是裸报错。"""
    missing = [k for k in keys if not cfg.get(k)]
    if missing:
        raise ConfigError(setup_hint(cfg, tuple(missing)))


def require_password(cfg: dict) -> str:
    require(cfg, "neo4j_password")
    return str(cfg["neo4j_password"])
