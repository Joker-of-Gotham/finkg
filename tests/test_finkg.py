#!/usr/bin/env python3
"""finkg 离线单测。不依赖 Neo4j、LazySearch 或任何网络。

    python tests/test_finkg.py
    python tests/test_finkg.py -v

只用标准库 unittest，这样 CI 和用户本机都不需要装任何东西。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / "skills" / "finkg"
SCRIPTS = SKILL / "scripts"
EXAMPLE = REPO / "examples" / "catl-lithium"

sys.path.insert(0, str(SCRIPTS))

import fgdepth  # noqa: E402
import fglazy  # noqa: E402
import fgmodel  # noqa: E402
import fgstore  # noqa: E402


# ==========================================================================
# 规范与打包
# ==========================================================================
class TestSkillContract(unittest.TestCase):
    """SKILL.md 必须满足 Agent Skills 规范，否则宿主会静默不加载。"""

    @classmethod
    def setUpClass(cls):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        cls.body = text
        cls.front = text.split("---", 2)[1]
        cls.fields = {}
        for line in cls.front.strip().splitlines():
            if ":" in line and not line.startswith((" ", "\t", "#")):
                key, value = line.split(":", 1)
                cls.fields[key.strip()] = value.strip()

    def test_frontmatter_present(self):
        self.assertTrue(self.body.startswith("---"), "SKILL.md 必须以 YAML frontmatter 开头")

    def test_name_matches_directory(self):
        self.assertEqual(self.fields.get("name"), SKILL.name)

    def test_name_charset_and_length(self):
        name = self.fields.get("name", "")
        self.assertRegex(name, r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertLessEqual(len(name), 64)

    def test_description_single_line_under_500(self):
        desc = self.fields.get("description", "")
        self.assertTrue(desc, "description 不能为空")
        self.assertLessEqual(len(desc), 500, "Codex 会拒绝超过 500 字符的 description")
        self.assertNotIn("\n", desc)

    def test_frontmatter_is_yaml_safe(self):
        """未加引号的标量里出现「: 」会让 YAML 解析成嵌套映射，宿主直接跳过整个 skill。

        这不是假想问题：`Keywords: …` 曾让 Skills CLI 报
        "Nested mappings are not allowed in compact mappings" 并拒绝安装。
        """
        sys.path.insert(0, str(REPO))
        import install as installer

        for key, value in self.fields.items():
            with self.subTest(field=key):
                self.assertEqual(installer.yaml_scalar_problems(key, value), [])

    def test_yaml_safety_check_actually_catches_the_bug(self):
        sys.path.insert(0, str(REPO))
        import install as installer

        self.assertTrue(installer.yaml_scalar_problems(
            "description", "有用的说明。Keywords: neo4j, graph"),
            "「: 」必须被抓住")
        self.assertTrue(installer.yaml_scalar_problems("description", "[不能这样开头"))
        self.assertTrue(installer.yaml_scalar_problems("description", "说明 # 注释被截断"))
        self.assertEqual(installer.yaml_scalar_problems(
            "description", '"引号里的 Keywords: 可以"'), [],
            "加了引号就不该报")
        self.assertEqual(installer.yaml_scalar_problems(
            "description", "正常的一句中文说明，含逗号、句号与括号（都没问题）。"), [])

    def test_frontmatter_parses_with_real_yaml_if_available(self):
        try:
            import yaml  # type: ignore
        except ImportError:
            self.skipTest("未安装 PyYAML；CI 里会装上做真解析")
        parsed = yaml.safe_load(self.front)
        self.assertIsInstance(parsed, dict)
        self.assertIsInstance(parsed.get("name"), str)
        self.assertIsInstance(parsed.get("description"), str)
        self.assertEqual(parsed["name"], SKILL.name)

    def test_expected_layout(self):
        for sub in ("references", "scripts", "assets"):
            self.assertTrue((SKILL / sub).is_dir(), f"缺子目录 {sub}/")
        self.assertTrue((SCRIPTS / "fg.py").exists())
        self.assertEqual(len(list((SKILL / "references").glob("*.md"))), 7)

    def test_scripts_compile(self):
        for path in sorted(SCRIPTS.glob("*.py")):
            with self.subTest(script=path.name):
                compile(path.read_text(encoding="utf-8"), str(path), "exec")

    def test_no_third_party_imports(self):
        """零依赖是核心属性：复制目录就能用。"""
        allowed = {m.stem for m in SCRIPTS.glob("*.py")} | set(sys.stdlib_module_names)
        for path in sorted(SCRIPTS.glob("*.py")):
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("import ") or line.startswith("from "):
                    module = line.split()[1].split(".")[0]
                    if module in ("__future__",):
                        continue
                    with self.subTest(script=path.name, module=module):
                        self.assertIn(module, allowed, f"{path.name} 引入了第三方模块 {module}")

    def test_templates_are_valid_json(self):
        templates = list((SKILL / "assets" / "templates").glob("*.json"))
        self.assertGreaterEqual(len(templates), 4)
        for path in templates:
            with self.subTest(template=path.name):
                json.loads(path.read_text(encoding="utf-8"))

    def test_defaults_hold_no_secret(self):
        data = json.loads((SKILL / "fg.defaults.json").read_text(encoding="utf-8"))
        self.assertNotIn("neo4j_password", data,
                         "默认值文件里绝不能出现密码")


class TestPackaging(unittest.TestCase):
    def test_installer_check_passes(self):
        result = subprocess.run([sys.executable, str(REPO / "install.py"), "--check"],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_plugin_manifests_valid(self):
        for rel in (".claude-plugin/plugin.json", ".claude-plugin/marketplace.json",
                    "gemini-extension.json", "package.json"):
            path = REPO / rel
            with self.subTest(manifest=rel):
                self.assertTrue(path.exists(), f"缺 {rel}")
                json.loads(path.read_text(encoding="utf-8"))

    def test_manifests_agree_on_skill_name(self):
        plugin = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertIn("./skills/finkg", plugin["skills"])
        pkg = json.loads((REPO / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(pkg["agentSkills"]["skills"][0]["name"], "finkg")
        self.assertEqual(pkg["agentSkills"]["skills"][0]["path"], "skills/finkg")

    def test_gitignore_protects_secrets(self):
        text = (REPO / ".gitignore").read_text(encoding="utf-8")
        for pattern in ("financial_graph.local.json", "financial-graph-sessions/",
                        "finkg.environment.md"):
            self.assertIn(pattern, text)


class TestNoDeploymentSpecifics(unittest.TestCase):
    """公开仓库里不能出现任何部署事实：主机、端口、账号、口令、内部库表与工具名。

    曾经真的漏过：内网 IP 与内部数仓表名被写进了 SKILL.md、docs 和默认配置。
    这组测试是那次事故的回归防线。
    """

    # 允许出现的例外：占位符、回环地址、示例仓库名、Neo4j 标准端口作为文档示例
    ALLOW = re.compile(
        r"127\.0\.0\.1|localhost|<[^>]*>|YOUR-|example\.com|examplewh|"
        r"0\.0\.0\.0|github\.com|raw\.githubusercontent\.com")

    PATTERNS = {
        "内网 IPv4": r"\b(?:192\.168|10)\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"
                     r"|\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b",
        "带主机名的内网服务地址": r"https?://(?!<|localhost|127\.0\.0\.1|github\.com"
                                r"|raw\.githubusercontent\.com|agent-config\.com|skills\.sh"
                                r"|semver\.org|img\.shields\.io|opensource\.org)"
                                r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?::\d+)?",
        "内部数仓 schema": r"\b(?:dm_[a-z]+|dwd_[a-z]+|dws_[a-z]+|ods_[a-z]+)\.[A-Za-z_]+",
        # 私有制品库/包源。刻意不写任何具体机构名——上面那条主机名规则已经能覆盖，
        # 而把机构域名片段硬编码进检测器本身就是一种泄露。
        "私有包源": r"\bartifactory\b|\bnexus3?\b|--index-url\s+(?!https?://pypi\.org)",
    }

    def _files(self):
        for path in REPO.rglob("*"):
            rel = path.relative_to(REPO)
            if not path.is_file():
                continue
            if rel.parts[0] in (".git", "__pycache__", "node_modules"):
                continue
            if rel.parts[0].startswith(".") and rel.parts[0] not in (
                    ".github", ".claude-plugin", ".cursor", ".gitignore", ".gitattributes"):
                continue
            if path.name in ("financial_graph.local.json",):
                continue  # 本机私有文件，已 gitignore
            if path.suffix in (".png", ".jpg", ".gif", ".zip"):
                continue
            yield rel, path

    def test_no_deployment_specifics_in_repo(self):
        for label, pattern in self.PATTERNS.items():
            compiled = re.compile(pattern)
            hits = []
            for rel, path in self._files():
                if rel.parts[0] == "tests":
                    continue  # 本文件自身含这些正则
                for lineno, line in enumerate(
                        path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    for match in compiled.finditer(line):
                        if self.ALLOW.search(match.group(0)):
                            continue
                        hits.append(f"{rel}:{lineno}: {match.group(0)}")
            with self.subTest(pattern=label):
                self.assertEqual(hits, [], f"{label} 泄露：{hits[:8]}")

    def test_no_credentials_anywhere(self):
        suspicious = re.compile(
            r'"(?:neo4j_password|password|token|secret|api_key)"\s*:\s*"(?!<|\s*$|YOUR)[^"]{3,}"')
        hits = []
        for rel, path in self._files():
            if rel.parts[0] == "tests":
                continue
            for lineno, line in enumerate(
                    path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if suspicious.search(line):
                    hits.append(f"{rel}:{lineno}")
        self.assertEqual(hits, [], f"疑似硬编码凭据：{hits}")

    def test_defaults_carry_no_deployment_keys(self):
        data = json.loads((SKILL / "fg.defaults.json").read_text(encoding="utf-8"))
        sys.path.insert(0, str(SCRIPTS))
        import fgconfig
        for key in fgconfig.DEPLOYMENT_KEYS:
            self.assertNotIn(key, data, f"fg.defaults.json 不能带部署项 {key}")

    def test_config_defaults_are_empty_for_deployment_keys(self):
        sys.path.insert(0, str(SCRIPTS))
        import fgconfig
        for key in fgconfig.DEPLOYMENT_KEYS:
            self.assertEqual(fgconfig.DEFAULTS[key], "",
                             f"{key} 必须留空，逼使用者显式配置")

    def test_unconfigured_gives_actionable_hint(self):
        sys.path.insert(0, str(SCRIPTS))
        import fgconfig
        cfg = dict(fgconfig.DEFAULTS)
        cfg["_local_config"] = "/tmp/financial_graph.local.json"
        with self.assertRaises(fgconfig.ConfigError) as ctx:
            fgconfig.require(cfg, "neo4j_url", "neo4j_password")
        message = str(ctx.exception)
        self.assertIn("financial_graph.local.json", message)
        self.assertIn("FG_NEO4J_PASSWORD", message)

    def test_redacted_config_hides_endpoints_and_secrets(self):
        sys.path.insert(0, str(SCRIPTS))
        import fgconfig
        cfg = dict(fgconfig.DEFAULTS)
        cfg.update({"neo4j_url": "http://secret-host.internal:7474",
                    "neo4j_user": "admin", "neo4j_password": "hunter2",
                    "lazysearch_url": "http://another-host.internal:9999"})
        rendered = json.dumps(fgconfig.redacted(cfg), ensure_ascii=False)
        for leak in ("secret-host", "another-host", "hunter2", "admin"):
            self.assertNotIn(leak, rendered, f"redacted() 漏了 {leak}")


# ==========================================================================
# 收割切层与单元格清点
# ==========================================================================
class TestHarvestPartition(unittest.TestCase):
    """LazySearch 返回必须被正确切层：prompt 不算数据，tool 返回才是富矿。"""

    def setUp(self):
        self.payload = {
            "final_answer": "营业总收入 4,237.02 亿元。",
            "history": [
                {"role": "user", "content": "<knowledge_card>" + "x" * 5000 + "</knowledge_card>"},
                {"role": "assistant", "content": "",
                 "tool_calls": [{"function": {"name": "ExampleFinancialTool",
                                              "arguments": '{"search_type": "利润表"}'},
                                 "id": "c1"}]},
                {"role": "tool", "tool_call_id": "c1", "content":
                    "<tool_response>\n<metadata>\n- source_table: examplewh.income_statement\n"
                    "- search_type: 利润表\n</metadata>\n"
                    "<executed_sql>\n```sql\nSELECT p.OPERATEREVE AS `营业收入` FROM "
                    "examplewh.income_statement p LIMIT 4\n```\n</executed_sql>\n"
                    "<rows format=\"csv\">\n```csv\nrow_idx,证券代码,营业总收入,研发费用\n"
                    "0,300750.SZ,423701834000.00,22146581000.00\n```\n</rows>\n</tool_response>"},
            ],
            "tool_briefs": [{"tool_name": "ExampleFinancialTool",
                             "brief": json.dumps({"source_table": "examplewh.income_statement",
                                                  "search_type": "利润表"})}],
        }
        self.parts = fglazy.partition(self.payload, "问题", fglazy.CHANNEL_HTTP)

    def test_prompt_turn_is_not_data(self):
        kinds = {t["index"]: t["kind"] for t in self.parts["turns"]}
        self.assertEqual(kinds[0], "prompt")
        self.assertEqual(kinds[1], "plan")
        self.assertEqual(kinds[2], "data")
        self.assertEqual(len(self.parts["data_blocks"]), 1)

    def test_provenance_extracts_tables_and_sql(self):
        prov = fglazy.provenance(self.parts)
        self.assertIn("examplewh.income_statement", prov["source_tables"])
        self.assertIn("ExampleFinancialTool", prov["lazysearch_tools"])
        self.assertTrue(prov["executed_sql"])

    def test_data_cells_exclude_query_mechanics(self):
        cells = fglazy.data_cells(self.parts)
        values = {c["value"] for c in cells}
        self.assertIn("423701834000.00", values)
        self.assertIn("22146581000.00", values)
        # row_idx 是索引列，不是数据
        self.assertNotIn("row_idx", {c["column"] for c in cells})
        # 5000 个 x 的 knowledge card 不应产生任何单元格
        self.assertLess(len(cells), 40, "prompt 泄漏进了数据面")

    def test_cell_cleaning_strips_html_entities(self):
        parts = fglazy.partition({"final_answer":
            "| 科目 | 金额 |\n|---|---|\n| &nbsp;&nbsp;**研发费用** | 221.47 |",
            "history": []}, "q", fglazy.CHANNEL_MCP)
        values = {c["value"] for c in fglazy.data_cells(parts)}
        self.assertIn("研发费用", values, "&nbsp; 与 ** 应被清掉，否则引文对不上")

    def test_searchable_text_covers_tool_returns(self):
        record = {"parts": self.parts}
        text = fglazy.searchable_text(record)
        self.assertIn("423701834000.00", text)
        self.assertIn("营业总收入 4,237.02 亿元", text)


# ==========================================================================
# 事实校验：内容质量，不是格式
# ==========================================================================
def _entities():
    return [
        {"id": "E-a", "kind": "Company", "name": "甲公司", "short": "甲"},
        {"id": "E-b", "kind": "Company", "name": "乙公司", "short": "乙"},
    ]


def _numeric_fact(**over):
    fact = {
        "id": "F00001", "subject": "E-a", "predicate": "营业总收入",
        "object": {"kind": "number", "value": 100.0, "unit": "元", "currency": "CNY"},
        "period": {"kind": "duration", "start": "2025-01-01", "end": "2025-12-31"},
        "epistemic": "reported", "harvest_id": "h-0001",
        "quote": "营业总收入 | 100.00",
        "target": {"kind": "prop", "node": "E-a", "key": "财务.利润表.2025年报.营业总收入"},
    }
    fact.update(over)
    return fact


HAYSTACK = {"h-0001": "报表摘录：营业总收入 | 100.00 | 单位元\n甲公司向乙公司供货"}


def _issues(facts, entities=None, haystack=None):
    report = fgmodel.validate_facts(facts, entities or _entities(),
                                    haystack if haystack is not None else HAYSTACK)
    return [p["issue"] for p in report["problems"] if p["level"] == "error"]


class TestFactValidation(unittest.TestCase):
    def test_valid_fact_passes(self):
        self.assertEqual(_issues([_numeric_fact()]), [])

    def test_quote_must_exist_in_harvest(self):
        bad = _numeric_fact(quote="营业总收入 | 999.00")
        self.assertTrue(any("找不到" in i for i in _issues([bad])),
                        "凭记忆改写的引文必须被拒绝")

    def test_quote_tolerates_whitespace_and_separators(self):
        ok = _numeric_fact(quote="营业总收入|100.00")
        self.assertEqual(_issues([ok]), [], "空白与千分位差异应该宽容")

    def test_numeric_needs_unit_or_currency(self):
        bad = _numeric_fact(object={"kind": "number", "value": 100.0})
        self.assertTrue(any("单位" in i for i in _issues([bad])))

    def test_numeric_needs_period(self):
        bad = _numeric_fact()
        bad.pop("period")
        self.assertTrue(any("period" in i for i in _issues([bad])))

    def test_dangling_subject_rejected(self):
        bad = _numeric_fact(subject="E-nope")
        self.assertTrue(any("不在实体表里" in i for i in _issues([bad])))

    def test_inference_needs_basis(self):
        bad = _numeric_fact(epistemic="inference")
        self.assertTrue(any("basis_fact_ids" in i for i in _issues([bad])))

    def test_inference_basis_cycle_rejected(self):
        a = _numeric_fact(id="F1", epistemic="inference", basis_fact_ids=["F2"], rule="r")
        b = _numeric_fact(id="F2", epistemic="inference", basis_fact_ids=["F1"], rule="r")
        self.assertTrue(any("成环" in i for i in _issues([a, b])))

    def test_missing_harvest_id_rejected(self):
        bad = _numeric_fact()
        bad.pop("harvest_id")
        self.assertTrue(any("harvest_id" in i for i in _issues([bad])))


def _edge_fact(**target_over):
    target = {
        "kind": "edge", "from": "E-a", "to": "E-b",
        "relation": "长协供应", "layer": "supply_operation",
        "mechanism": "长协指数联动，价格波动经采购成本进入营业成本",
        "attrs": {"合同类型": "长期协议", "期间": "2025年", "品类": "电池级碳酸锂"},
    }
    target.update(target_over)
    return {"id": "F00001", "subject": "E-a", "predicate": "供应",
            "object": {"kind": "entity", "entity": "E-b"},
            "epistemic": "reported", "harvest_id": "h-0001",
            "quote": "甲公司向乙公司供货", "target": target}


class TestEdgeQuality(unittest.TestCase):
    def test_good_edge_passes(self):
        self.assertEqual(_issues([_edge_fact()]), [])

    def test_phrase_relation_accepted(self):
        for word in ("持股", "挂牌", "长协供应", "准入约束", "贡献收入"):
            with self.subTest(relation=word):
                self.assertEqual(_issues([_edge_fact(relation=word)]), [])

    def test_vague_relation_rejected(self):
        for word in ("相关", "影响", "关联", "related", "affects"):
            with self.subTest(relation=word):
                issues = _issues([_edge_fact(relation=word)])
                self.assertTrue(any("太空泛" in i for i in issues), f"「{word}」应被拒绝")

    def test_sentence_relation_rejected(self):
        """关系类型必须是短语，不能是整句。"""
        issues = _issues([_edge_fact(relation="向客户长协供应电池级碳酸锂")])
        self.assertTrue(any("句子" in i for i in issues), issues)

    def test_load_blockers_ignore_scale(self):
        report = {"ok": False, "findings": [
            {"level": "guide", "area": "覆盖广度", "issue": "节点 9 个"},
            {"level": "error", "area": "纵深", "issue": "只有 1 条 6 跳"},
            {"level": "error", "area": "证据", "issue": "quote 在 h-0001 的返回里找不到"},
        ]}
        blockers = fgmodel.load_blockers(report)
        self.assertEqual(len(blockers), 1)
        self.assertIn("quote", blockers[0]["issue"])
        self.assertEqual(fgmodel.load_blockers(None), [])

    def test_missing_mechanism_is_warning_not_error(self):
        """缺机制会拉低可分析率并让纵深断掉，但不阻止入库。"""
        report = fgmodel.validate_facts([_edge_fact(mechanism="")], _entities(), HAYSTACK)
        levels = {p["level"] for p in report["problems"]}
        self.assertIn("warn", levels)
        self.assertNotIn("error", levels)

    def test_edge_quality_metrics(self):
        meta = {"topic": "t", "anchors": [{"id": "E-a"}]}
        facts = [_edge_fact(), _numeric_fact(id="F00002")]
        graph = fgmodel.compile_graph(meta, _entities(), facts)
        quality = fgmodel.edge_quality(graph)
        self.assertEqual(quality["edge_count"], 1)
        self.assertEqual(quality["analyzable_ratio"], 1.0)
        self.assertEqual(quality["layers_used"], 1)

    def test_unanalyzable_edge_lowers_ratio(self):
        meta = {"topic": "t", "anchors": []}
        facts = [_edge_fact(), _edge_fact(mechanism="", attrs={}, relation="整车供应")]
        facts[1]["id"] = "F00002"
        graph = fgmodel.compile_graph(meta, _entities(), facts)
        quality = fgmodel.edge_quality(graph)
        self.assertEqual(quality["analyzable_ratio"], 0.5)
        self.assertEqual(len(quality["missing_mechanism"]), 1)


# ==========================================================================
# 编译与节点丰富度
# ==========================================================================
class TestCompile(unittest.TestCase):
    def test_props_group_by_first_key_segment(self):
        facts = [
            _numeric_fact(id="F1", target={"kind": "prop", "node": "E-a",
                                           "key": "财务.利润表.2025年报.营业总收入"}),
            _numeric_fact(id="F2", target={"kind": "prop", "node": "E-a",
                                           "key": "所有权.第一大股东.持股比例"}),
            _numeric_fact(id="F3", target={"kind": "prop", "node": "E-a",
                                           "key": "风险.供应链.集中度"}),
        ]
        graph = fgmodel.compile_graph({"topic": "t", "anchors": [{"id": "E-a"}]},
                                      _entities(), facts)
        node = next(n for n in graph["nodes"] if n["id"] == "E-a")
        self.assertEqual(node["prop_count"], 3)
        self.assertEqual(node["prop_groups"], ["所有权", "财务", "风险"])

    def test_prop_conflict_is_surfaced_not_silently_overwritten(self):
        facts = [
            _numeric_fact(id="F1"),
            _numeric_fact(id="F2", object={"kind": "number", "value": 200.0,
                                           "unit": "元", "currency": "CNY"}),
        ]
        graph = fgmodel.compile_graph({"topic": "t", "anchors": []}, _entities(), facts)
        self.assertEqual(len(graph["prop_conflicts"]), 1)

    def test_value_conflicts_detected(self):
        facts = [
            _numeric_fact(id="F1"),
            _numeric_fact(id="F2", object={"kind": "number", "value": 200.0,
                                           "unit": "元", "currency": "CNY"}),
        ]
        self.assertEqual(len(fgmodel.value_conflicts(facts)), 1)

    def test_identical_edges_merge(self):
        facts = [_edge_fact(), _edge_fact()]
        facts[1]["id"] = "F00002"
        graph = fgmodel.compile_graph({"topic": "t", "anchors": []}, _entities(), facts)
        self.assertEqual(len(graph["edges"]), 1)
        self.assertEqual(len(graph["edges"][0]["fact_ids"]), 2)

    def test_name_only_nodes_reported(self):
        facts = [_edge_fact()]
        graph = fgmodel.compile_graph({"topic": "t", "anchors": []}, _entities(), facts)
        richness = fgmodel.node_richness(graph, [])
        self.assertEqual(len(richness["name_only_nodes"]), 2)


# ==========================================================================
# 纵深：实质性判据
# ==========================================================================
def _chain(hops: int, layers: list[str], relations: list[str],
           epistemics: list[str] | None = None, with_facts: bool = True) -> dict:
    nodes = [{"id": f"N{i}", "kind": "Company", "caption": f"节点{i}"} for i in range(hops + 1)]
    edges = []
    for i in range(hops):
        edges.append({
            "id": f"R{i}", "from": f"N{i}", "to": f"N{i+1}",
            "relation": relations[i % len(relations)],
            "layer": layers[i % len(layers)],
            "mechanism": "机制说明",
            "attrs": {"期间": "2025年"},
            "epistemic": (epistemics or ["reported"])[i % len(epistemics or [1])]
            if epistemics else "reported",
            "period": "", "confidence": "",
            "fact_ids": [f"F{i}"] if with_facts else [],
            "harvest_ids": ["h-0001"], "quote": "",
        })
    return {"schema": "finkg/1", "topic": "t", "as_of": "", "center_question": "",
            "anchors": ["N0"], "nodes": nodes, "edges": edges, "prop_conflicts": []}


class TestDepthSubstance(unittest.TestCase):
    LAYERS = ["policy_regulation", "demand_market", "supply_operation",
              "financial_capital", "expectation_valuation", "risk_feedback"]

    def _describe(self, graph):
        view = fgdepth.business_view(graph)
        paths = fgdepth.enumerate_paths(view, "N0", min_hops=1, max_hops=20)
        deepest = max(paths, key=lambda p: len(p["edges"]))
        return fgdepth.describe_path(view, deepest)

    def test_genuine_deep_chain_is_substantive(self):
        graph = _chain(8, self.LAYERS, [f"具体动作{i}" for i in range(8)])
        described = self._describe(graph)
        self.assertEqual(described["hops"], 8)
        self.assertTrue(described["substantive"], described["weak_because"])
        self.assertGreaterEqual(described["distinct_layers"], 3)

    def test_single_relation_chain_rejected(self):
        """A供应B供应C供应D… 跳数够但只说明一件事。"""
        graph = _chain(10, self.LAYERS, ["向下游供应"])
        described = self._describe(graph)
        self.assertFalse(described["substantive"])
        self.assertTrue(any("重复接龙" in r for r in described["weak_because"]))

    def test_single_layer_chain_rejected(self):
        graph = _chain(8, ["supply_operation"], [f"动作{i}" for i in range(8)])
        described = self._describe(graph)
        self.assertFalse(described["substantive"])
        self.assertTrue(any("语义层" in r for r in described["weak_because"]))

    def test_unsupported_hop_rejected(self):
        graph = _chain(6, self.LAYERS, [f"动作{i}" for i in range(6)], with_facts=False)
        described = self._describe(graph)
        self.assertFalse(described["substantive"])
        self.assertTrue(any("事实支撑" in r for r in described["weak_because"]))

    def test_long_inference_run_rejected(self):
        graph = _chain(6, self.LAYERS, [f"动作{i}" for i in range(6)],
                       epistemics=["inference"])
        described = self._describe(graph)
        self.assertFalse(described["substantive"])
        self.assertTrue(any("连续" in r for r in described["weak_because"]))

    def test_meta_nodes_do_not_count_as_hops(self):
        """公司→文档→观测→指标→公司 不应算 4 跳业务纵深。"""
        graph = _chain(4, self.LAYERS, [f"动作{i}" for i in range(4)])
        for pos, kind in enumerate(["Company", "Document", "Observation", "Metric", "Company"]):
            graph["nodes"][pos]["kind"] = kind
        structure = fgdepth.structure(graph)
        self.assertEqual(structure["business_nodes"], 2)
        self.assertEqual(structure["business_edges"], 0)

    def test_independent_witnesses_dedupe_shared_trunk(self):
        """共享主干、只换尾巴的多条路径只算一条。"""
        graph = _chain(6, self.LAYERS, [f"动作{i}" for i in range(6)])
        for tail in range(3):  # 从 N3 再分出三条尾巴
            graph["nodes"].append({"id": f"T{tail}", "kind": "Company", "caption": f"尾{tail}"})
            graph["edges"].append({
                "id": f"RT{tail}", "from": "N3", "to": f"T{tail}",
                "relation": f"尾部动作{tail}", "layer": "risk_feedback",
                "mechanism": "机制", "attrs": {"期间": "2025年"}, "epistemic": "reported",
                "period": "", "confidence": "", "fact_ids": [f"FT{tail}"],
                "harvest_ids": ["h-0001"], "quote": ""})
        view = fgdepth.business_view(graph)
        described = [fgdepth.describe_path(view, p)
                     for p in fgdepth.enumerate_paths(view, "N0", min_hops=4, max_hops=12)]
        strong = [d for d in described if d["substantive"]]
        self.assertGreater(len(strong), 1, "应该找到多条路径")
        picked = fgdepth.independent(strong)
        for a in range(len(picked)):
            for b in range(a + 1, len(picked)):
                self.assertFalse(set(picked[a]["edge_ids"]) & set(picked[b]["edge_ids"]),
                                 "独立见证之间不能共享边")

    def test_pure_chain_flagged_as_zero_crosscheck(self):
        graph = _chain(8, self.LAYERS, [f"动作{i}" for i in range(8)])
        structure = fgdepth.structure(graph)
        self.assertEqual(structure["two_core_ratio"], 0.0)
        self.assertEqual(structure["bridge_ratio"], 1.0)

    def test_mechanism_case_reports_gaps(self):
        graph = _chain(3, ["supply_operation"], ["向下游供应"])
        case = {"id": "M1", "question": "q", "from": "N0", "to": "N3",
                "min_hops": 3, "independent": 2,
                "layers": ["policy_regulation", "expectation_valuation"]}
        result = fgdepth.answer_case(graph, case)
        self.assertFalse(result["ok"])
        self.assertTrue(result["gaps"])


# ==========================================================================
# 存储层：编码与 IO 兜底
# ==========================================================================
class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reads_utf8_and_bom_variants(self):
        payload = [{"id": "E-a", "名称": "甲公司"}]
        raw = json.dumps(payload, ensure_ascii=False)
        for label, encoding in (("utf-8", "utf-8"), ("utf-8-sig", "utf-8-sig"),
                                ("utf-16-le", "utf-16"), ("utf-16-be", "utf-16-be")):
            with self.subTest(encoding=label):
                path = self.tmp / f"{label}.json"
                if encoding == "utf-16-be":
                    path.write_bytes(b"\xfe\xff" + raw.encode("utf-16-be"))
                else:
                    path.write_text(raw, encoding=encoding)
                self.assertEqual(fgstore.load_records(path), payload)

    def test_broken_json_gives_actionable_hint(self):
        (self.tmp / "bad.json").write_text('[{"a": "断\n行"}]', encoding="utf-8")
        with self.assertRaises(fgstore.StoreError) as ctx:
            fgstore.load_records(self.tmp / "bad.json")
        self.assertIn("--out-file", str(ctx.exception))

    def test_neo4j_db_name_is_legal(self):
        import re
        for topic in ["宁德时代动力电池", "a", "UPPER_Case_Topic", "锂-价 传导 2026",
                      "x" * 200, "system-thing"]:
            with self.subTest(topic=topic):
                name = fgstore.neo4j_db_name(topic)
                self.assertRegex(name, r"^[A-Za-z0-9][A-Za-z0-9.-]*$")
                self.assertTrue(3 <= len(name) <= 63)
                self.assertFalse(name.endswith((".", "-")))
                self.assertFalse(name.lower().startswith("system"))
                self.assertNotIn("_", name)

    def test_session_roundtrip_and_ledger(self):
        session = fgstore.Session.create(self.tmp, "s1", {"topic": "主题", "anchors": []})
        self.assertEqual(session.meta()["topic"], "主题")
        self.assertEqual(session.ledger(), [], "Session.create 本身不写台账")
        session.log("align", stage="scope", question="问题", answer="")
        session.log("align", stage="scope", question="第二问", answer="答")
        entries = session.ledger()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[-1]["answer"], "答")
        self.assertTrue(all("ts" in e and "kind" in e for e in entries))

    def test_atomic_write_leaves_no_temp(self):
        fgstore.write_json(self.tmp / "a.json", {"k": "值"})
        self.assertEqual(json.loads((self.tmp / "a.json").read_text(encoding="utf-8"))["k"], "值")
        self.assertEqual(list(self.tmp.glob("*.tmp")), [])


# ==========================================================================
# Neo4j 桥：只测不需要连接的纯函数
# ==========================================================================
class TestNeo4jMapping(unittest.TestCase):
    def setUp(self):
        import fgneo4j
        self.fgneo4j = fgneo4j
        facts = [_numeric_fact(id="F1"), _edge_fact()]
        facts[1]["id"] = "F2"
        self.graph = fgmodel.compile_graph({"topic": "主题", "anchors": [{"id": "E-a"}]},
                                           _entities(), facts)

    def test_labels_and_chinese_relation_types(self):
        nodes = self.fgneo4j.node_rows(self.graph)
        edges = self.fgneo4j.edge_rows(self.graph)
        self.assertIn("Company", nodes)
        self.assertIn("长协供应", edges)

    def test_props_stay_neo4j_compatible(self):
        for rows in self.fgneo4j.node_rows(self.graph).values():
            for row in rows:
                for key, value in row["props"].items():
                    with self.subTest(key=key):
                        self.assertTrue(
                            value is None or isinstance(value, (str, int, float, bool, list)),
                            f"{key} 的值类型 Neo4j 不接受：{type(value)}")
                        if isinstance(value, list):
                            kinds = {type(v) for v in value}
                            self.assertLessEqual(len(kinds), 1, f"{key} 是异质数组")

    def test_edge_attrs_carry_mechanism_and_layer(self):
        rows = self.fgneo4j.edge_rows(self.graph)["长协供应"]
        attrs = rows[0]["attrs"]
        self.assertTrue(attrs["机制"])
        self.assertEqual(attrs["语义层"], "supply_operation")

    def test_database_name_validation_rejects_bad_names(self):
        for bad in ("ab", "sys" + "x" * 80, "has_underscore", "trailing-", "system"):
            with self.subTest(name=bad):
                with self.assertRaises(self.fgneo4j.Neo4jError):
                    self.fgneo4j.validate_db(bad)

    def test_grass_covers_every_label_and_relation(self):
        style = self.fgneo4j.grass(self.graph)
        self.assertIn("node.Company", style)
        self.assertIn("relationship.长协供应", style)


# ==========================================================================
# 样例数据必须真的能用
# ==========================================================================
class TestExample(unittest.TestCase):
    def test_example_files_parse(self):
        for name in ("entities.json", "facts-props.json", "facts-edges.json",
                     "mechanism-questions.json"):
            with self.subTest(file=name):
                json.loads((EXAMPLE / name).read_text(encoding="utf-8"))

    def test_example_quotes_exist_in_harvest(self):
        haystack = (EXAMPLE / "harvest.txt").read_text(encoding="utf-8")
        for name in ("facts-props.json", "facts-edges.json"):
            for fact in json.loads((EXAMPLE / name).read_text(encoding="utf-8")):
                with self.subTest(file=name, quote=fact["quote"][:30]):
                    self.assertTrue(fgmodel.quote_found(fact["quote"], haystack),
                                    f"样例引文在 harvest.txt 里找不到：{fact['quote']}")

    def test_example_compiles_into_a_connected_graph(self):
        entities = json.loads((EXAMPLE / "entities.json").read_text(encoding="utf-8"))
        facts = json.loads((EXAMPLE / "facts-props.json").read_text(encoding="utf-8"))
        facts += json.loads((EXAMPLE / "facts-edges.json").read_text(encoding="utf-8"))
        for pos, fact in enumerate(facts, 1):
            fact.setdefault("id", f"F{pos:05d}")
            fact.setdefault("harvest_id", "h-0001")
        meta = {"topic": "样例", "anchors": [{"id": "E-catl"}, {"id": "E-catl-a"}]}
        graph = fgmodel.compile_graph(meta, entities, facts)
        structure = fgdepth.structure(graph)
        self.assertEqual(structure["isolated_nodes"], [])
        self.assertGreaterEqual(structure["business_edges"], 8)
        self.assertGreaterEqual(len(structure["layers_used"]), 5)

    def test_example_facts_validate(self):
        entities = json.loads((EXAMPLE / "entities.json").read_text(encoding="utf-8"))
        facts = json.loads((EXAMPLE / "facts-props.json").read_text(encoding="utf-8"))
        facts += json.loads((EXAMPLE / "facts-edges.json").read_text(encoding="utf-8"))
        for pos, fact in enumerate(facts, 1):
            fact.setdefault("id", f"F{pos:05d}")
            fact.setdefault("harvest_id", "h-0001")
        haystack = {"h-0001": (EXAMPLE / "harvest.txt").read_text(encoding="utf-8")}
        report = fgmodel.validate_facts(facts, entities, haystack)
        errors = [p for p in report["problems"] if p["level"] == "error"]
        self.assertEqual(errors, [], f"样例数据自身不该有 error：{errors}")

    def test_example_answers_its_mechanism_questions(self):
        entities = json.loads((EXAMPLE / "entities.json").read_text(encoding="utf-8"))
        facts = json.loads((EXAMPLE / "facts-props.json").read_text(encoding="utf-8"))
        facts += json.loads((EXAMPLE / "facts-edges.json").read_text(encoding="utf-8"))
        for pos, fact in enumerate(facts, 1):
            fact.setdefault("id", f"F{pos:05d}")
            fact.setdefault("harvest_id", "h-0001")
        graph = fgmodel.compile_graph(
            {"topic": "样例", "anchors": [{"id": "E-catl"}]}, entities, facts)
        cases = json.loads((EXAMPLE / "mechanism-questions.json").read_text(encoding="utf-8"))
        for case in cases:
            with self.subTest(case=case["id"]):
                result = fgdepth.answer_case(graph, case)
                self.assertTrue(result["ok"], f"{case['id']} 未被回答：{result['gaps']}")


# ==========================================================================
# CLI 冒烟（不触网）
# ==========================================================================
class TestCliOffline(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *args, expect=0):
        env = {**dict(__import__("os").environ),
               "FG_SESSIONS_DIR": str(self.tmp / "sessions"),
               "FG_WORKSPACE": str(self.tmp),
               "PYTHONIOENCODING": "utf-8"}
        result = subprocess.run([sys.executable, str(SCRIPTS / "fg.py"), *args],
                                capture_output=True, text=True, encoding="utf-8",
                                cwd=self.tmp, env=env)
        self.assertEqual(result.returncode, expect,
                         f"args={args}\nstdout={result.stdout}\nstderr={result.stderr}")
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def test_full_offline_pipeline(self):
        self._run("session", "new", "离线测试", "--id", "t1", "--profile", "probe",
                  "--anchor", "E-catl=宁德时代:Company")
        self._run("harvest", "add", "利润表", "--file", str(EXAMPLE / "harvest.txt"),
                  "--channel", "manual")
        self._run("entity", "add", str(EXAMPLE / "entities.json"))
        self._run("fact", "add", str(EXAMPLE / "facts-props.json"), "--harvest", "h-0001")
        self._run("fact", "add", str(EXAMPLE / "facts-edges.json"), "--harvest", "h-0001")
        compiled = self._run("compile")
        self.assertGreaterEqual(compiled["business_edges"], 8)
        self.assertEqual(compiled["isolated_nodes"], [])

        node = self._run("node", "E-catl")
        self.assertGreaterEqual(node["prop_count"], 8)
        self.assertIn("财务", node["prop_groups"])

        usage = self._run("usage")
        self.assertGreater(usage["total_data_cells"], 50)
        self.assertGreater(usage["used"], 0)

        depth = self._run("depth", "--min-hops", "4", "--brief")
        self.assertGreaterEqual(depth["deepest_hops"], 4)

        # 规模/跳数不再让 ok=false；有证据缺陷才失败
        quality = self._run("quality", "--brief", expect=0)
        self.assertTrue(quality["ok"])
        self.assertTrue(any(f["level"] == "guide" for f in quality["findings"]))
        self.assertFalse(any(
            f["level"] == "error" and any(k in f["issue"] for k in ("档", "目标 ≥", "跳路径"))
            for f in quality["findings"]))

        brief = self._run("brief")
        self.assertTrue(brief["next"])

    def test_out_file_works_before_and_after_subcommand(self):
        self._run("session", "new", "参数位置", "--id", "t2")
        for args in (("--out-file", str(self.tmp / "a.json"), "session", "show"),
                     ("session", "show", "--out-file", str(self.tmp / "b.json"))):
            with self.subTest(args=args):
                self._run(*args)
        for name in ("a.json", "b.json"):
            data = json.loads((self.tmp / name).read_text(encoding="utf-8"))
            self.assertEqual(data["meta"]["session_id"], "t2")

    def test_align_and_answer_recorded_in_plain_ledger(self):
        self._run("session", "new", "台账", "--id", "t3")
        self._run("align", "--stage", "scope", "--question", "口径按合并还是母公司",
                  "--option", "合并", "--option", "母公司", "--recommendation", "合并")
        self._run("answer", "按合并口径", "--effect", "全部财务事实标注合并报表")
        ledger = self._run("ledger", "--kind", "align")
        entry = ledger["entries"][-1]
        self.assertEqual(entry["answer"], "按合并口径")
        self.assertNotIn("attestation", json.dumps(ledger, ensure_ascii=False))
        self.assertNotIn("resume_token", json.dumps(ledger, ensure_ascii=False))

    def test_templates_write_valid_utf8(self):
        self._run("session", "new", "模板", "--id", "t4")
        for name in ("entity", "fact", "fact-edge", "mechanism-question"):
            target = self.tmp / f"{name}.json"
            self._run("template", name, "--output", str(target))
            self.assertTrue(json.loads(target.read_text(encoding="utf-8")))

    def test_bad_quote_is_rejected_end_to_end(self):
        self._run("session", "new", "引文", "--id", "t5",
                  "--anchor", "E-catl=宁德时代:Company")
        self._run("harvest", "add", "利润表", "--file", str(EXAMPLE / "harvest.txt"),
                  "--channel", "manual")
        self._run("entity", "add", str(EXAMPLE / "entities.json"))
        forged = self.tmp / "forged.json"
        forged.write_text(json.dumps([{
            "subject": "E-catl", "predicate": "营业总收入",
            "object": {"kind": "number", "value": 999.0, "unit": "元", "currency": "CNY"},
            "period": {"kind": "duration", "start": "2025-01-01", "end": "2025-12-31"},
            "epistemic": "reported", "harvest_id": "h-0001",
            "quote": "一、营业总收入 | 9,999.99 | 999,999,999,999.00",
            "target": {"kind": "prop", "node": "E-catl", "key": "财务.利润表.2025年报.营业总收入"},
        }], ensure_ascii=False), encoding="utf-8")
        result = self._run("fact", "add", str(forged), expect=1)
        self.assertTrue(any("找不到" in p["issue"] for p in result["problems"]))

    def test_template_defaults_to_session_drafts(self):
        self._run("session", "new", "草稿", "--id", "t6")
        result = self._run("template", "entity")
        written = Path(result["written"])
        self.assertEqual(written.name, "entities.json")
        self.assertEqual(written.parent.name, "drafts")
        self.assertTrue(written.exists())
        self._run("template", "fact", "--output", "facts.json")
        self.assertTrue((self.tmp / "sessions" / "t6" / "drafts" / "facts.json").exists())
        self.assertFalse((self.tmp / "facts.json").exists())

    def test_harvest_mine_reads_drafts(self):
        self._run("session", "new", "挖掘", "--id", "t7",
                  "--anchor", "E-catl=宁德时代:Company")
        self._run("harvest", "add", "利润表", "--file", str(EXAMPLE / "harvest.txt"),
                  "--channel", "manual")
        drafts = self.tmp / "sessions" / "t7" / "drafts"
        drafts.mkdir(parents=True, exist_ok=True)
        shutil.copy(EXAMPLE / "entities.json", drafts / "entities.json")
        shutil.copy(EXAMPLE / "facts-props.json", drafts / "facts.json")
        mined = self._run("harvest", "mine", "h-0001", "--done")
        self.assertGreater(mined["entities_added"], 0)
        self.assertGreater(mined["facts_added"], 0)

    def test_brief_points_at_missing_sectors_and_stray_root(self):
        self._run("session", "new", "引导", "--id", "t8",
                  "--anchor", "E-catl=宁德时代:Company")
        (self.tmp / "entities.json").write_text("[]", encoding="utf-8")
        brief = self._run("brief")
        self.assertIn("entities.json", brief["stray_workspace_files"])
        self.assertTrue(brief["sectors"]["missing"])
        self.assertTrue(any("扇区" in item for item in brief["next"]))
        self.assertTrue(any("根目录" in item for item in brief["next"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
