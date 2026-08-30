"""web/app.py API 层测试：FastAPI TestClient 直测端点（GET 用自带演示数据，POST 用 mock 隔离写路径）。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from web.app import APP_VERSION, create_app  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class WebApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Host 固定为 127.0.0.1（Host 允许列表内的合法来源）
        cls.client = TestClient(create_app(), base_url="http://127.0.0.1")

    # ---------- 读端点（自带演示数据） ----------

    def test_health(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["version"], APP_VERSION)

    def test_health_instruments_golden_signals(self):
        # SRE Ch6：流量/错误/存活可在单端点可查
        self.client.get("/api/registry")
        body = self.client.get("/api/health").json()
        self.assertGreaterEqual(body["uptime_s"], 0)
        self.assertGreaterEqual(body["requests_total"], 2)   # 本测试至少已发 2 个请求
        self.assertEqual(body["responses_5xx"], 0)
        self.assertIn("total", body["pipeline_runs"])
        self.assertIn("failed", body["pipeline_runs"])

    def test_registry_enriched_with_description(self):
        skills = self.client.get("/api/registry").json()["skills"]
        self.assertTrue(skills, "registry 不应为空")
        self.assertTrue(skills[0]["description"], "description 注入缺失")

    def test_sources_detect_example_table(self):
        srcs = self.client.get("/api/sources").json()["sources"]
        wiki = [s for s in srcs if s["name"] == "wiki-tasks.json"]
        self.assertTrue(wiki, "示例数据源未被扫描到")
        self.assertEqual(wiki[0]["adapter"], "table")
        self.assertEqual(wiki[0]["count"], 6)

    def test_llm_status_shape(self):
        body = self.client.get("/api/llm_status").json()
        self.assertIn("backend", body)
        self.assertIn("available", body)

    def test_version_single_source_of_truth(self):
        # 发布工程：APP_VERSION 必须与 CHANGELOG 最新节一致（防版本漂移）
        import re
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        changelog = open(os.path.join(root, "CHANGELOG.md"), encoding="utf-8").read()
        latest = re.search(r"^## \[([^\]]+)\]", changelog, re.M)
        self.assertIsNotNone(latest, "CHANGELOG 缺版本节")
        self.assertEqual(latest.group(1), APP_VERSION)

    def test_skill_detail_404_for_unknown(self):
        self.assertEqual(self.client.get("/api/skills/definitely-not-exist").status_code, 404)

    def test_error_detail_leaks_no_filesystem_path(self):
        # ASVS V7.2：错误响应不得泄露内部文件系统路径
        r = self.client.get("/api/skills/definitely-not-exist")
        detail = r.json().get("detail", "")
        self.assertNotIn("/", detail)
        self.assertNotIn("\\", detail)

    def test_skill_detail_has_frontmatter_fields(self):
        d = self.client.get("/api/skills/implementation-research").json()
        self.assertTrue(d["description"])
        self.assertTrue(d["when_to_use"])

    # ---------- 写端点（mock 隔离，不碰仓库真实数据） ----------

    def test_issue_rejects_blank_text(self):
        self.assertEqual(
            self.client.post("/api/skills/implementation-research/issue", json={"text": "   "}).status_code,
            400)

    def test_issue_unknown_skill_404(self):
        with tempfile.TemporaryDirectory() as td:
            with patch("web.app.issues_path", return_value=os.path.join(td, "issues.json")):
                self.assertEqual(
                    self.client.post("/api/skills/nope/issue", json={"text": "x"}).status_code,
                    404)

    def test_issue_appends_and_persists(self):
        with tempfile.TemporaryDirectory() as td:
            fake = os.path.join(td, "issues.json")
            with patch("web.app.issues_path", return_value=fake):
                r = self.client.post("/api/skills/implementation-research/issue",
                                     json={"text": "步骤 2 的说法过时了"})
                self.assertEqual(r.status_code, 200)
                data = json.load(open(fake, encoding="utf-8"))
                self.assertEqual(data[0]["skill"], "implementation-research")
                self.assertEqual(data[0]["status"], "open")
                # 再提一条，应插到最前
                self.client.post("/api/skills/implementation-research/issue", json={"text": "第二条"})
                data = json.load(open(fake, encoding="utf-8"))
                self.assertEqual(len(data), 2)
                self.assertEqual(data[0]["text"], "第二条")

    def test_issue_list_endpoint(self):
        with tempfile.TemporaryDirectory() as td:
            fake = os.path.join(td, "issues.json")
            json.dump([{"skill": "s", "text": "t", "status": "open", "ts": "2026-08-29T00:00:00Z"}],
                      open(fake, "w", encoding="utf-8"))
            with patch("web.app.issues_path", return_value=fake):
                body = self.client.get("/api/skill-issues").json()
                self.assertEqual(len(body["issues"]), 1)

    def _patched_registry(self, td):
        fake = os.path.join(td, "registry.json")
        shutil.copy(os.path.join(ROOT, "registry", "registry.json"), fake)
        return patch("web.app.registry_path", return_value=fake)

    def test_review_updates_status(self):
        with tempfile.TemporaryDirectory() as td, self._patched_registry(td):
            r = self.client.post("/api/review/implementation-research", json={"status": "deprecated"})
            self.assertEqual(r.status_code, 200)
            reg = json.load(open(os.path.join(td, "registry.json"), encoding="utf-8"))
            self.assertEqual(reg["skills"][0]["review_status"], "deprecated")

    def test_review_rejects_illegal_transition(self):
        # 状态机：draft -> in_review -> published -> deprecated；published 不能回到 in_review
        with tempfile.TemporaryDirectory() as td, self._patched_registry(td):
            self.assertEqual(
                self.client.post("/api/review/implementation-research", json={"status": "in_review"}).status_code,
                400)

    def test_review_rejects_bad_status(self):
        with tempfile.TemporaryDirectory() as td, self._patched_registry(td):
            self.assertEqual(
                self.client.post("/api/review/implementation-research", json={"status": "nope"}).status_code,
                400)

    def test_review_unknown_skill_404(self):
        with tempfile.TemporaryDirectory() as td, self._patched_registry(td):
            self.assertEqual(
                self.client.post("/api/review/nope", json={"status": "published"}).status_code,
                404)


if __name__ == "__main__":
    unittest.main()


class HostGuardTests(unittest.TestCase):
    """DNS rebinding 防护（OWASP A05，对照 Vite GHSA-vg6x-rcgg-rjx6）。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(create_app(), base_url="http://127.0.0.1")

    def test_localhost_host_allowed(self):
        r = self.client.get("/api/health", headers={"Host": "localhost:8000"})
        self.assertEqual(r.status_code, 200)

    def test_foreign_host_rejected(self):
        r = self.client.get("/api/health", headers={"Host": "evil.example"})
        self.assertEqual(r.status_code, 403)

    def test_rejected_hosts_counted_as_security_signal(self):
        before = self.client.get("/api/health").json().get("rejected_hosts", 0)
        self.client.get("/api/health", headers={"Host": "evil2.example"})
        after = self.client.get("/api/health").json().get("rejected_hosts", 0)
        self.assertGreaterEqual(after, before + 1)

    def test_foreign_host_with_port_rejected(self):
        r = self.client.get("/api/registry", headers={"Host": "evil.example:8000"})
        self.assertEqual(r.status_code, 403)

    def test_nosniff_header_present(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.headers.get("x-content-type-options"), "nosniff")

