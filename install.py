#!/usr/bin/env python3
"""finkg 安装器：把 skills/finkg 装到各家 agent 的 skill 目录。

零第三方依赖，Python 3.9+。常用法：

    python install.py                      # 交互：检测本机 agent，选择装哪些
    python install.py --all                # 装到所有检测到的 agent（用户级）
    python install.py --agent claude cursor codex opencode
    python install.py --all --project      # 装到当前项目而不是用户目录
    python install.py --here               # 在本仓库内建各宿主镜像（开发自用）
    python install.py --list               # 只看目标路径，不写任何东西
    python install.py --check              # 校验 SKILL.md 是否合规
    python install.py --all --uninstall    # 卸载

默认优先创建符号链接（改一处，所有宿主同步生效）；Windows 无权限时自动回退为复制。
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
SKILL_NAME = "finkg"
SOURCE = REPO / "skills" / SKILL_NAME

# 实测存在的宿主目录。user = 用户级，project = 项目级。
# 说明列里写清"谁还会读这个目录"，避免重复安装。
TARGETS: dict[str, dict] = {
    "agents": {
        "label": "Agent Skills 通用路径（Cursor / Codex / Gemini 原生读取）",
        "user": Path.home() / ".agents" / "skills",
        "project": Path(".agents") / "skills",
        "detect": [Path.home() / ".agents", Path.home() / ".codex", Path.home() / ".cursor"],
    },
    "claude": {
        "label": "Claude Code（GitHub Copilot 也读这里）",
        "user": Path.home() / ".claude" / "skills",
        "project": Path(".claude") / "skills",
        "detect": [Path.home() / ".claude"],
    },
    "cursor": {
        "label": "Cursor 专用目录（已装 agents 通用路径时可跳过）",
        "user": Path.home() / ".cursor" / "skills",
        "project": Path(".cursor") / "skills",
        "detect": [Path.home() / ".cursor"],
    },
    "codex": {
        "label": "OpenAI Codex CLI（新版已迁到 agents 通用路径）",
        "user": Path.home() / ".codex" / "skills",
        "project": Path(".agents") / "skills",
        "detect": [Path.home() / ".codex"],
    },
    "opencode": {
        "label": "OpenCode",
        "user": Path.home() / ".config" / "opencode" / "skills",
        "project": Path(".opencode") / "skills",
        "detect": [Path.home() / ".config" / "opencode"],
    },
    "gemini": {
        "label": "Gemini CLI",
        "user": Path.home() / ".gemini" / "skills",
        "project": Path(".gemini") / "skills",
        "detect": [Path.home() / ".gemini"],
    },
    "copilot": {
        "label": "GitHub Copilot（项目级；用户级它读 ~/.claude/skills）",
        "user": None,
        "project": Path(".github") / "skills",
        "detect": [Path.home() / ".github"],
    },
    "windsurf": {
        "label": "Windsurf",
        "user": Path.home() / ".windsurf" / "skills",
        "project": Path(".windsurf") / "skills",
        "detect": [Path.home() / ".windsurf"],
    },
}

# --here 只建这两个：Cursor/Codex/Gemini 读 .agents，Claude/Copilot 读 .claude。
HERE_TARGETS = ("agents", "claude")

GREEN, YELLOW, RED, DIM, BOLD, OFF = (
    ("\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[1m", "\033[0m")
    if sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
    else ("", "", "", "", "", "")
)


def say(msg: str = "") -> None:
    print(msg, flush=True)


def ok(msg: str) -> None:
    say(f"{GREEN}✓{OFF} {msg}")


def warn(msg: str) -> None:
    say(f"{YELLOW}!{OFF} {msg}")


def err(msg: str) -> None:
    say(f"{RED}✗{OFF} {msg}")


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------
def yaml_scalar_problems(key: str, raw: str) -> list[str]:
    """检测未加引号的 YAML 标量里会导致 parse error 的模式。

    真实踩过的坑：description 里写了「Keywords: xxx」，YAML 把它当成嵌套映射，
    Skills CLI 直接报 "Nested mappings are not allowed in compact mappings" 并跳过
    整个 skill —— 而按 ':' 朴素切分的校验完全看不出来。
    """
    if raw.startswith(('"', "'")):
        return []  # 已加引号，内部内容不再被解析
    problems = []
    if ": " in raw or raw.rstrip().endswith(":"):
        problems.append(f"{key} 含未加引号的「: 」，YAML 会当成嵌套映射而报 parse error；"
                        f"去掉冒号或给整个值加引号")
    if raw.lstrip()[:1] in ("[", "{", "&", "*", "!", "|", ">", "%", "@", "`"):
        problems.append(f"{key} 以 YAML 保留字符开头，必须加引号")
    if " #" in raw:
        problems.append(f"{key} 含「 #」，YAML 会把后面当注释截断")
    if "\t" in raw:
        problems.append(f"{key} 含制表符，YAML 不允许用 tab 缩进")
    return problems


def check_skill() -> tuple[list[str], dict[str, str]]:
    """按 Agent Skills 规范校验。返回（问题列表, frontmatter 字段）。"""
    problems: list[str] = []
    skill_md = SOURCE / "SKILL.md"
    if not skill_md.exists():
        return [f"找不到 {skill_md}"], {}
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return ["SKILL.md 必须以 YAML frontmatter（--- 分隔）开头"], {}
    front = text.split("---", 2)[1]
    fields: dict[str, str] = {}
    for line in front.strip().splitlines():
        if ":" in line and not line.startswith((" ", "\t", "#")):
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()

    for key, value in fields.items():
        problems.extend(yaml_scalar_problems(key, value))
    # 有 PyYAML 就做一次真解析，这是唯一能覆盖全部 YAML 语法陷阱的办法
    try:
        import yaml  # type: ignore
    except ImportError:
        pass
    else:
        try:
            parsed = yaml.safe_load(front)
            if not isinstance(parsed, dict):
                problems.append("frontmatter 解析结果不是映射")
            else:
                for key in ("name", "description"):
                    if not isinstance(parsed.get(key), str):
                        problems.append(f"{key} 解析后不是字符串，说明 YAML 结构不对")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"frontmatter 不是合法 YAML: {exc}")

    name = fields.get("name", "")
    if not name:
        problems.append("frontmatter 缺 name")
    else:
        if name != SOURCE.name:
            problems.append(f"name「{name}」必须与目录名「{SOURCE.name}」一致")
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name):
            problems.append(f"name「{name}」只能用小写字母、数字和中间连字符")
        if len(name) > 64:
            problems.append(f"name 长度 {len(name)} 超过 64")

    desc = fields.get("description", "")
    if not desc:
        problems.append("frontmatter 缺 description")
    elif len(desc) > 500:
        problems.append(f"description 长度 {len(desc)} 超过 500（Codex 会拒绝）")

    for sub in ("references", "scripts", "assets"):
        if not (SOURCE / sub).is_dir():
            problems.append(f"缺子目录 {sub}/")
    entry = SOURCE / "scripts" / "fg.py"
    if not entry.exists():
        problems.append("缺 scripts/fg.py")
    return problems, fields


def compile_scripts() -> list[str]:
    """确认所有 Python 语法可编译，避免装出一个跑不起来的 skill。"""
    broken = []
    for path in sorted((SOURCE / "scripts").glob("*.py")):
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except SyntaxError as exc:
            broken.append(f"{path.name}:{exc.lineno}: {exc.msg}")
    return broken


# --------------------------------------------------------------------------
# 安装
# --------------------------------------------------------------------------
def detected(agent: str) -> bool:
    return any(Path(p).exists() for p in TARGETS[agent]["detect"])


def resolve(agent: str, scope: str, project_root: Path) -> Path | None:
    base = TARGETS[agent][scope]
    if base is None:
        return None
    base = Path(base)
    if not base.is_absolute():
        base = project_root / base
    return base / SKILL_NAME


def _clear(dest: Path, force: bool) -> bool:
    if not (dest.exists() or dest.is_symlink()):
        return True
    if not force:
        warn(f"已存在，跳过（加 --force 覆盖）：{dest}")
        return False
    if dest.is_symlink() or dest.is_file():
        dest.unlink()
    else:
        shutil.rmtree(dest)
    return True


def _ignore(_dir, names):
    return [n for n in names if n in ("__pycache__", ".DS_Store") or n.endswith(".pyc")]


def place(dest: Path, mode: str, force: bool, dry: bool) -> str | None:
    """把 SOURCE 放到 dest。返回实际使用的方式，跳过则返回 None。"""
    if dry:
        say(f"  {DIM}[dry-run]{OFF} {dest}")
        return "dry-run"
    if not _clear(dest, force):
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    if mode in ("link", "auto"):
        try:
            dest.symlink_to(SOURCE, target_is_directory=True)
            return "symlink"
        except (OSError, NotImplementedError) as exc:
            if mode == "link":
                err(f"符号链接失败：{dest} —— {exc}")
                return None
            # Windows 未开开发者模式时属常见情况，静默回退
    shutil.copytree(SOURCE, dest, ignore=_ignore, dirs_exist_ok=False)
    return "copy"


def remove(dest: Path, dry: bool) -> bool:
    if not (dest.exists() or dest.is_symlink()):
        return False
    if dry:
        say(f"  {DIM}[dry-run] 会删除{OFF} {dest}")
        return True
    if dest.is_symlink() or dest.is_file():
        dest.unlink()
    else:
        shutil.rmtree(dest)
    return True


# --------------------------------------------------------------------------
# 交互
# --------------------------------------------------------------------------
def choose(scope: str, project_root: Path) -> list[str]:
    found = [a for a in TARGETS if detected(a) and resolve(a, scope, project_root)]
    missing = [a for a in TARGETS if a not in found]
    say(f"\n{BOLD}检测到的 agent{OFF}（scope={scope}）")
    for pos, agent in enumerate(found, 1):
        say(f"  {pos}. {agent:<10} {DIM}{TARGETS[agent]['label']}{OFF}")
        say(f"     {DIM}→ {resolve(agent, scope, project_root)}{OFF}")
    if missing:
        say(f"\n{DIM}未检测到：{', '.join(missing)}（想装可以用 --agent 显式指定）{OFF}")
    if not found:
        warn("没检测到任何 agent 目录。用 --agent <名称> 显式指定，或 --here 装在本仓库内。")
        return []
    say(f"\n{BOLD}装哪些？{OFF} 回车=全部；或输入序号如 1,2；q 退出")
    try:
        raw = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        say()
        return []
    if raw.lower() in ("q", "quit", "exit"):
        return []
    if not raw:
        return found
    picked = []
    for token in re.split(r"[,\s]+", raw):
        if token.isdigit() and 1 <= int(token) <= len(found):
            picked.append(found[int(token) - 1])
        elif token in TARGETS:
            picked.append(token)
        else:
            warn(f"忽略无法识别的输入：{token}")
    return list(dict.fromkeys(picked))


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="install.py", description="把 finkg 装到各家 agent 的 skill 目录",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="agent 可选值：" + "、".join(TARGETS))
    parser.add_argument("--agent", nargs="+", metavar="NAME", help="指定 agent")
    parser.add_argument("--all", action="store_true", help="所有检测到的 agent")
    parser.add_argument("--project", action="store_true", help="装到当前项目而非用户目录")
    parser.add_argument("--project-root", default=".", help="项目根目录（默认当前目录）")
    parser.add_argument("--here", action="store_true",
                        help="在本仓库内建 .agents/ 与 .claude/ 镜像（开发自用）")
    parser.add_argument("--link", action="store_true", help="强制符号链接")
    parser.add_argument("--copy", action="store_true", help="强制复制")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的安装")
    parser.add_argument("--uninstall", action="store_true", help="卸载")
    parser.add_argument("--list", action="store_true", help="只列目标路径")
    parser.add_argument("--check", action="store_true", help="只做规范校验")
    parser.add_argument("--dry-run", action="store_true", help="演练，不写任何东西")
    args = parser.parse_args(argv)

    if not SOURCE.is_dir():
        err(f"找不到 skill 源目录：{SOURCE}")
        return 1

    problems, fields = check_skill()
    broken = compile_scripts()
    if args.check:
        say(f"{BOLD}校验 {SOURCE}{OFF}")
        for item in problems + broken:
            err(item)
        if not problems and not broken:
            ok("SKILL.md 合规，脚本全部可编译")
            say(f"  {DIM}name={fields.get('name')}  "
                f"description={len(fields.get('description', ''))}/500 字符  "
                f"scripts={len(list((SOURCE / 'scripts').glob('*.py')))} 个  "
                f"references={len(list((SOURCE / 'references').glob('*.md')))} 份{OFF}")
        return 0 if not (problems or broken) else 1
    if problems or broken:
        for item in problems + broken:
            err(item)
        err("skill 本身不合规，先修好再装。（`--check` 看细节）")
        return 1

    project_root = Path(args.project_root).resolve()
    scope = "project" if (args.project or args.here) else "user"
    if args.here:
        project_root = REPO
        agents = list(HERE_TARGETS)
    elif args.all:
        agents = [a for a in TARGETS if detected(a) and resolve(a, scope, project_root)]
    elif args.agent:
        agents = []
        for name in args.agent:
            if name not in TARGETS:
                err(f"未知 agent：{name}（可选：{'、'.join(TARGETS)}）")
                return 1
            agents.append(name)
    elif args.list or args.uninstall:
        agents = list(TARGETS)
    else:
        agents = choose(scope, project_root)
        if not agents:
            say("什么都没做。")
            return 0

    if args.list:
        say(f"{BOLD}finkg 安装目标{OFF}  (scope={scope})")
        for agent in agents:
            dest = resolve(agent, scope, project_root)
            mark = "已装" if dest and (dest.exists() or dest.is_symlink()) else "  — "
            found = "检测到" if detected(agent) else "未检测"
            say(f"  [{mark}] [{found}] {agent:<10} {dest or '（该 scope 不适用）'}")
            say(f"           {DIM}{TARGETS[agent]['label']}{OFF}")
        return 0

    if args.uninstall:
        removed = 0
        for agent in agents:
            for sc in ("user", "project"):
                dest = resolve(agent, sc, project_root)
                if dest and remove(dest, args.dry_run):
                    ok(f"已移除 {agent} ({sc})：{dest}")
                    removed += 1
        if not removed:
            say("没有找到已安装的副本。")
        return 0

    mode = "link" if args.link else "copy" if args.copy else "auto"
    installed: list[tuple[str, Path, str]] = []
    for agent in agents:
        dest = resolve(agent, scope, project_root)
        if dest is None:
            warn(f"{agent} 没有 {scope} 级路径，跳过")
            continue
        used = place(dest, mode, args.force, args.dry_run)
        if used:
            installed.append((agent, dest, used))
            ok(f"{agent:<10} {used:<8} {dest}")

    if not installed:
        warn("没有装成任何位置。")
        return 1

    say()
    if any(used == "copy" for _, _, used in installed) and platform.system() == "Windows":
        say(f"{DIM}Windows 未开启开发者模式时无法建符号链接，已改为复制。"
            f"仓库更新后重跑一次 install.py --force 即可同步。{OFF}")

    agent_dest = installed[0][1]
    say(f"{BOLD}下一步{OFF}")
    say(f"  1. 告诉你的 agent：使用 {BOLD}finkg{OFF} skill（Claude/Codex 里也可以打 $finkg）")
    say(f"  2. 工具箱路径：{BOLD}{agent_dest / 'scripts' / 'fg.py'}{OFF}")
    say(f"  3. 自检：")
    if platform.system() == "Windows":
        say(f"     $env:PYTHONIOENCODING=\"utf-8\"")
        say(f"     python \"{agent_dest / 'scripts' / 'fg.py'}\" doctor")
    else:
        say(f"     python \"{agent_dest / 'scripts' / 'fg.py'}\" doctor")
    say(f"  4. 配置 Neo4j 密码：在你的工作区建 financial_graph.local.json")
    say(f"     {DIM}{{\"neo4j_password\": \"…\", \"neo4j_allow_http_auth\": true}}{OFF}")
    say(f"\n  {DIM}文档：docs/QUICKSTART.md · docs/INSTALL.md · docs/CONFIGURATION.md{OFF}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
