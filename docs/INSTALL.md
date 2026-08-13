# 安装

需要 **Python 3.10+**。skill 本身零第三方依赖，不需要 `pip install` 任何东西。

四条安装途径，随便选一条。都装完之后跳到[验证](#验证)。

---

## 途径 1：Skills CLI（推荐）

[skills.sh](https://skills.sh) 的 CLI，会自动识别你机器上装了哪些 agent 并逐个写入。

```bash
npx skills add Joker-of-Gotham/finkg              # 装到当前项目
npx skills add Joker-of-Gotham/finkg -g           # 装到用户目录，所有项目可用
npx skills add Joker-of-Gotham/finkg --list       # 先列出仓库里有哪些 skill
npx skills add Joker-of-Gotham/finkg -a claude-code -a cursor   # 只装指定宿主
npx skills add Joker-of-Gotham/finkg -g -y        # 非交互（CI 友好）
npx skills add Joker-of-Gotham/finkg --copy       # Windows 未开开发者模式时用复制
```

已支持 amp、antigravity、claude-code、codex、cursor、droid、gemini、gemini-cli、github-copilot、
goose、kilo、kiro-cli、opencode、roo、trae、windsurf 等宿主。

更新与卸载：

```bash
npx skills update
npx skills list
```

## 途径 2：SkillHub CLI

```bash
npx @skillhub/cli install Joker-of-Gotham/finkg/finkg
npx @skillhub/cli install Joker-of-Gotham/finkg/finkg --platform codex
npx @skillhub/cli install Joker-of-Gotham/finkg/finkg --project   # 装到当前项目
npx @skillhub/cli install Joker-of-Gotham/finkg/finkg --force     # 覆盖
```

路径形式是 `<owner>/<repo>/<skill>`，所以最后一段是 `finkg`（skill 名），不是仓库名。

## 途径 3：git clone + 自带安装器

控制最细，也最适合想读源码或改一改的人。

```bash
git clone https://github.com/Joker-of-Gotham/finkg.git
cd finkg

python install.py                  # 交互：检测本机 agent，选择装哪些
python install.py --all            # 装到所有检测到的 agent（用户级）
python install.py --all --project  # 装到当前项目
python install.py --agent claude cursor codex opencode
python install.py --list           # 只看目标路径，什么都不写
python install.py --check          # 校验 SKILL.md 合规性 + 脚本可编译
python install.py --dry-run --all  # 演练
python install.py --all --force    # 覆盖已有安装
python install.py --all --uninstall
```

链接方式：

```bash
python install.py --all --link     # 强制符号链接（改一处，所有宿主同步）
python install.py --all --copy     # 强制复制
```

默认是 `auto`：先试符号链接，失败（例如 Windows 未开开发者模式）就自动回退为复制，并在结尾提示。
用复制方式安装的，仓库更新后需要重跑一次 `python install.py --all --force` 同步。

### 一行安装（不用先 clone）

```bash
curl -fsSL https://raw.githubusercontent.com/Joker-of-Gotham/finkg/main/install.sh | bash -s -- --all
```

```powershell
iwr -useb https://raw.githubusercontent.com/Joker-of-Gotham/finkg/main/install.ps1 | iex
```

脚本会把仓库 clone 到临时目录、执行安装、然后清理临时目录。

### 在仓库内自用

想在 clone 出来的仓库里直接用这个 skill（开发或试用）：

```bash
python install.py --here
```

它在仓库内建 `.agents/skills/finkg` 与 `.claude/skills/finkg` 两个镜像（已 gitignore）。
Cursor、Codex、Gemini 读 `.agents/skills/`；Claude Code、Copilot 读 `.claude/skills/`。

## 途径 4：Claude Code 插件市场

```
/plugin marketplace add Joker-of-Gotham/finkg
/plugin install finkg
```

以插件安装时 skill 会被命名空间化为 `finkg:finkg`，不会和你手工装的同名 skill 冲突。

## 手动放置

skill 就是一个自包含目录，复制过去就行：

```bash
cp -r skills/finkg ~/.agents/skills/finkg
cp -r skills/finkg ~/.claude/skills/finkg
```

```powershell
Copy-Item -Recurse skills\finkg "$HOME\.agents\skills\finkg"
Copy-Item -Recurse skills\finkg "$HOME\.claude\skills\finkg"
```

**这两个目录就够覆盖大多数宿主。** 完整对照表：

| 宿主 | 用户级 | 项目级 | 备注 |
| --- | --- | --- | --- |
| **通用路径** | `~/.agents/skills/finkg` | `.agents/skills/finkg` | Cursor、Codex、Gemini **原生读取**，首选 |
| Claude Code | `~/.claude/skills/finkg` | `.claude/skills/finkg` | GitHub Copilot 也读这里 |
| Cursor | `~/.cursor/skills/finkg` | `.cursor/skills/finkg` | 装了通用路径可跳过 |
| Codex CLI | `~/.codex/skills/finkg` | `.agents/skills/finkg` | 新版已迁到通用路径 |
| OpenCode | `~/.config/opencode/skills/finkg` | `.opencode/skills/finkg` | 注意是 `skills` 复数 |
| Gemini CLI | `~/.gemini/skills/finkg` | `.gemini/skills/finkg` | 也读通用路径 |
| GitHub Copilot | 读 `~/.claude/skills` | `.github/skills/finkg` | |
| Windsurf | `~/.windsurf/skills/finkg` | `.windsurf/skills/finkg` | |

Codex 还会从当前目录逐级向上扫 `.agents/skills/`，所以放在仓库根目录的项目级安装对子目录同样有效。

---

## 配置

**finkg 不携带任何地址、账号和密码。** 装完之后必须先配置你自己的环境，否则任何要联网的
命令都会报错并列出缺什么。这是刻意的：公开仓库里不应出现任何主机或口令，而且每套部署
都不一样，内置默认值只会让人误以为能开箱即用。

把 skill 目录下的 `scripts/fg.py` 记为 `$FG`：

```bash
export FG="$HOME/.agents/skills/finkg/scripts/fg.py"
python $FG setup
```

```powershell
$env:PYTHONIOENCODING = "utf-8"
$env:FG = "$HOME\.agents\skills\finkg\scripts\fg.py"
python $env:FG setup
```

`setup` 会在**你的工作区根目录**（不是 skill 目录）写出 `financial_graph.local.json`：

```json
{
  "lazysearch_url": "http://<你的 LazySearch 主机>:<端口>",
  "neo4j_url": "http://<你的 Neo4j 主机>:7474",
  "neo4j_user": "<用户名>",
  "neo4j_password": "<密码>",
  "neo4j_allow_http_auth": true
}
```

它还会检查这个文件是否已被 `.gitignore` 覆盖，没有就警告 —— **这个文件永远不要提交**。

局域网明文 HTTP 下 `neo4j_allow_http_auth` 必须为 `true`，否则工具会拒绝把凭据发出去。
不想写文件的话，四项都能用环境变量代替：`FG_LAZYSEARCH_URL`、`FG_NEO4J_URL`、
`FG_NEO4J_USER`、`FG_NEO4J_PASSWORD`。完整配置项见 [CONFIGURATION.md](CONFIGURATION.md)。

### 环境档案（可选但强烈建议）

还有一类信息也不进仓库：你这套部署有哪些数据表、字段和检索工具。

```bash
python $FG env --init      # 在工作区建 finkg.environment.md
```

第一次用时花十几分钟做一轮探索把它填起来，之后取数会精确得多。它同样应当 gitignore。
填写方法见 `skills/finkg/references/LAZYSEARCH.md`。

## 验证

```bash
python $FG doctor
```

期望输出（节选）：

```json
{
  "configured": { "lazysearch_url": true, "neo4j_url": true,
                  "neo4j_user": true, "neo4j_password": true },
  "checks": {
    "lazysearch_health": { "ok": true, "reason": "ok" },
    "lazysearch_mcp":    { "ok": true, "reason": "ok" },
    "neo4j":             { "ok": true, "reason": "ok",
      "detail": { "product": "Neo4j Kernel", "version": "5.x", "edition": "enterprise" } }
  },
  "ok": true
}
```

`reason` 区分两种失败：`unconfigured` 是还没填，`unreachable` 是填了但连不上。

这份输出**不含主机地址、账号、密码，也不含其他项目的数据库名**（地址与凭据只显示
`<已配置>`），所以基本可以直接贴进 issue。唯一需要留意的是它含本机路径
（`workspace` / `sessions_root`），介意的话贴之前替换掉。本机排错要看库名加 `--verbose`。

再确认宿主认得这个 skill：

- **Claude Code**：`/skills` 里应出现 `finkg`，或直接打 `$finkg`
- **Codex CLI**：`/skills`，或 `$finkg`
- **Cursor**：新会话里问「有哪些 skill 可用」，或直接说「用 finkg …」
- **OpenCode / Gemini CLI**：查看各自的 skill 列表命令

没出现就检查：目录名是否正好是 `finkg`、`SKILL.md` 是否在该目录根下、宿主是否需要重启。

## 更新

```bash
# 途径 1
npx skills update

# 途径 3（符号链接安装：git pull 即生效）
cd finkg && git pull

# 途径 3（复制安装：需要重新同步）
cd finkg && git pull && python install.py --all --force
```

## 卸载

```bash
python install.py --all --uninstall      # 自带安装器
npx skills remove finkg                  # Skills CLI
```

或者直接删掉对应目录。skill 不在系统里留任何其他痕迹；你的会话数据在工作区
`financial-graph-sessions/` 下，需要的话单独处理。

## 排错

装不上或宿主认不出来，见 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)。
