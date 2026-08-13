# 贡献指南

## 上手

```bash
git clone https://github.com/Joker-of-Gotham/finkg.git
cd finkg
python install.py --check        # 校验 SKILL.md 规范 + 脚本编译
python tests/test_finkg.py       # 66 个离线单测，不依赖任何外部服务
python install.py --here         # 在本仓库内建宿主镜像，方便自己用着改
```

不需要虚拟环境，不需要 `pip install`。Python 3.10+ 即可。

## 改哪里

| 想改什么 | 改哪个文件 |
| --- | --- |
| agent 的工作流程与硬规则 | `skills/finkg/SKILL.md` |
| 检索策略、数据源路由、提问法 | `skills/finkg/references/LAZYSEARCH.md` |
| 节点该有哪些属性 | `skills/finkg/references/NODE_PROFILE.md` |
| 关系词表与边属性契约 | `skills/finkg/references/EDGE_SEMANTICS.md` |
| 纵深判据 | `skills/finkg/references/DEPTH.md` + `skills/finkg/scripts/fgdepth.py` |
| 质量指标与阈值 | `skills/finkg/references/QUALITY.md` + `skills/finkg/scripts/fg.py` 的 `PROFILES` |
| 事实校验规则 | `skills/finkg/scripts/fgmodel.py` |
| LazySearch 通道与切层 | `skills/finkg/scripts/fglazy.py` |
| Neo4j 装载与查询 | `skills/finkg/scripts/fgneo4j.py` |
| 安装与宿主适配 | `install.py` |

**`skills/finkg/` 是唯一真源。** `.agents/`、`.claude/` 等目录是 `install.py --here` 生成的
副本，已 gitignore，在里面改代码会被下次同步覆盖。

## 提交前必须做的

```bash
python install.py --check
python tests/test_finkg.py
```

改了 `skills/finkg/` 之后如果本仓库内有镜像，同步一下：

```bash
python install.py --here --force
```

## 硬性约束

违反这几条的 PR 会被要求修改：

1. **零第三方依赖。** `skills/finkg/scripts/` 只能 import 标准库。
   `tests/test_finkg.py::test_no_third_party_imports` 会检查。
2. **`description` ≤500 字符且单行。** 超了 Codex 会静默不加载。
   `install.py --check` 会检查。
3. **`name` 必须等于目录名。** 改名要同步 `package.json`、`.claude-plugin/*`、
   `gemini-extension.json`、`install.py` 的 `SKILL_NAME` 和全部文档引用。
4. **改行为要补会失败的反例测试。** 只改文档不算修复。
   例如给纵深加一条判据，就要有一个"不满足这条判据的图会被判非实质"的测试。
5. **不加仪式。** 不引入 Gate、阶段门、签名、HMAC、一次性令牌、工件哈希链、密钥环境变量。
   这不是疏漏，是[明确的设计决定](docs/ARCHITECTURE.md#没有-gate签名令牌哈希链)。
6. **质量校验只判内容。** 不加「必须有某个 key」「哈希必须匹配」这类形式检查。
7. **文档里的命令不写死安装路径。** 用 `$FG` 指代 skill 目录下的 `scripts/fg.py`。
8. **仓库里不能有任何部署信息。** 主机名、内网 IP、账号、密码、内部数仓库表名、内部工具
   专有名称，一律不许出现——包括「示例值」，示例请用 `<占位符>` 写法。
   `tests/test_finkg.py::TestNoDeploymentSpecifics` 会强制检查。
   部署信息属于使用者的 `financial_graph.local.json`（地址与凭据）与
   `finkg.environment.md`（本机有哪些库表与工具），两者都已 gitignore。
   提交前 `git status` 再扫一眼。
9. **端点不给默认值。** `fgconfig.DEFAULTS` 里部署项必须留空，未配置时报错并给出配置步骤。
   给它们填「方便的默认值」会让公开仓库重新携带部署事实。

## 代码风格

- 中文注释与中文报错信息。用户是中文使用者，报错要能直接看懂并知道下一步做什么。
- 注释只写代码本身表达不了的约束、取舍或坑（例如「PowerShell 会按控制台宽度折断输出」），
  不写「这一行在做什么」。
- 报错信息要给出**下一步动作**，不只是陈述失败。
  对比：`quote 找不到` → `quote 在 h-0001 的返回里找不到；不要凭记忆改写原文`。
- 函数保持短。`fg.py` 的子命令函数只做参数处理与输出组装，逻辑放到对应模块。

## 加一个新的宿主

在 `install.py` 的 `TARGETS` 里加一项：

```python
"newagent": {
    "label": "New Agent（说明它还会读哪些目录）",
    "user": Path.home() / ".newagent" / "skills",
    "project": Path(".newagent") / "skills",
    "detect": [Path.home() / ".newagent"],
},
```

然后更新 `README.md` 与 `docs/INSTALL.md` 的路径表。**路径要有实证依据**——
查该宿主的官方文档，或在装了它的机器上确认目录真实存在，不要照抄第三方博客。

## 报告问题

用 [issue 模板](.github/ISSUE_TEMPLATE/)。附 `install.py --check`、
`tests/test_finkg.py` 和 `fg doctor` 的输出会让排查快很多——
但**先去掉主机地址等敏感信息**。

## 许可

贡献的代码按 [MIT](LICENSE) 许可发布。
