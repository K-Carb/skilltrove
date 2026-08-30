"""cli/adapters.py 单元测试：5 种数据源形态的识别 / 切块 / 字段映射 + 端到端。

覆盖：
- auto 检测顺序（log-export / task-dirs / session-logs / git-repo / table / docs / 未知）
- table：gh JSON 数组（大写 state、assignees 对象）、Jira 风格 CSV、JSONL
- session-logs：Codex 风格（session_meta + response_item）、Claude Code 风格（custom-title/agent-name）
- git-repo：真实 git init + commit，commit = 任务
- task-dirs：别名文件 + meta.json 逃生舱 + artifacts
- docs：仅经验池降级（无归因/完成态）
- 端到端：export → score（用归一化 manifest）→ 高分可发现

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cli import adapters  # noqa: E402
from cli import score  # noqa: E402


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class TestPickAdapter(unittest.TestCase):
    def test_log_export_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "MANIFEST.json"),
                  json.dumps([{"issue": "A-1", "title": "t", "status": "done"}]))
            self.assertEqual(adapters.pick_adapter(tmp).shape, "log-export")

    def test_table_json_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "issues.json")
            write(p, json.dumps([{"number": 1, "title": "t"}]))
            self.assertEqual(adapters.pick_adapter(p).shape, "table")

    def test_session_logs_file_detected_before_table(self):
        # jsonl 会话文件必须判为 session-logs 而非 table
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "session.jsonl")
            write(p, '{"timestamp":"2025-01-01T00:00:00Z","type":"session_meta",'
                     '"payload":{"id":"s1","originator":"codex"}}\n')
            self.assertEqual(adapters.pick_adapter(p).shape, "session-logs")

    def test_git_repo_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True,
                           capture_output=True)
            self.assertEqual(adapters.pick_adapter(tmp).shape, "git-repo")

    def test_task_dirs_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "TASK-1-调研", "description.md"), "# 调研\n正文")
            self.assertEqual(adapters.pick_adapter(tmp).shape, "task-dirs")

    def test_docs_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "note.md"), "# 笔记\n内容")
            self.assertEqual(adapters.pick_adapter(tmp).shape, "docs")

    def test_unknown_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "data.bin"), "wb") as f:
                f.write(b"\x00\x01")
            with self.assertRaises(ValueError):
                adapters.pick_adapter(tmp)

    def test_explicit_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(adapters.pick_adapter(tmp, "docs").shape, "docs")
            with self.assertRaises(ValueError):
                adapters.pick_adapter(tmp, "nope")


class TestTableAdapter(unittest.TestCase):
    def test_gh_json_array(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "issues.json")
            write(p, json.dumps([
                {"number": 101, "title": "修复登录超时", "state": "MERGED",
                 "body": "调整 session 过期逻辑", "author": {"login": "alice"},
                 "assignees": [{"login": "alice"}],
                 "createdAt": "2026-08-01T09:00:00Z", "updatedAt": "2026-08-02T10:00:00Z"},
                {"number": 102, "title": "修复支付回调", "state": "CLOSED",
                 "body": "幂等处理", "author": {"login": "bob"}, "assignees": [{"login": "bob"}],
                 "createdAt": "2026-08-01T09:00:00Z", "updatedAt": "2026-08-02T10:00:00Z"},
                {"number": 103, "title": "写单元测试", "state": "OPEN",
                 "author": {"login": "carol"}, "assignees": []},
            ]))
            a = adapters.TableAdapter()
            tasks = a.discover(p)
            self.assertEqual(len(tasks), 3)
            ep = a.to_episode(p, tasks[0])
            self.assertEqual(ep["issue_key"], "101")
            self.assertEqual(ep["status"], "done")          # MERGED → done
            self.assertEqual(ep["agent_ids"], ["alice"])
            self.assertTrue(ep["output_signal"])
            self.assertEqual(ep["business_join_key"]["table"], "source_table")
            self.assertEqual(ep["business_join_key"]["measured_at"], "2026-08-02T10:00:00Z")
            self.assertEqual(a.to_episode(p, tasks[2])["status"], "open")  # OPEN 不归一为 done

    def test_jira_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "export.csv")
            write(p, "Issue key,Summary,Status,Assignee,Description,Created,Updated\n"
                     "DEMO-1,竞品调研机制拆解,Done,alice,全面调研参考对象机制,2026-08-01,2026-08-02\n"
                     "DEMO-2,MVP 范围定义,In Progress,bob,定义本地 MVP 范围,2026-08-01,\n")
            a = adapters.TableAdapter()
            tasks = a.discover(p)
            self.assertEqual(len(tasks), 2)
            ep1 = a.to_episode(p, tasks[0])
            self.assertEqual(ep1["issue_key"], "DEMO-1")
            self.assertEqual(ep1["status"], "done")
            self.assertEqual(ep1["agent_ids"], ["alice"])
            self.assertEqual(ep1["goal"], "竞品调研机制拆解")
            ep2 = a.to_episode(p, tasks[1])
            self.assertEqual(ep2["status"], "in progress")
            self.assertFalse(ep2["output_signal"])

    def test_jsonl_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "tickets.jsonl")
            write(p, '{"id": "T-1", "title": "修复登录", "state": "done"}\n'
                     '{"id": "T-2", "title": "修复支付", "state": "open"}\n')
            a = adapters.TableAdapter()
            self.assertEqual(len(a.discover(p)), 2)
            self.assertEqual(a.to_episode(p, a.discover(p)[0])["status"], "done")

    def test_row_without_key_uses_row_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "nokey.csv")
            write(p, "title,status\n修复登录,done\n")
            a = adapters.TableAdapter()
            ep = a.to_episode(p, a.discover(p)[0])
            self.assertEqual(ep["issue_key"], "row-1")

    def test_null_field_does_not_shadow_next_alias(self):
        # 回归：gh 数据里 assignee=null 不得遮蔽 assignees=[...]；作者字段是 user
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "gh.json")
            write(p, json.dumps([{
                "number": 1, "title": "修复登录超时", "state": "CLOSED",
                "assignee": None, "assignees": [{"login": "alice"}, {"login": "bob"}],
                "user": {"login": "carol"}, "body": "描述", "created_at": "2026-08-01T00:00:00Z",
                "updated_at": "2026-08-02T00:00:00Z",
            }]))
            a = adapters.TableAdapter()
            ep = a.to_episode(p, a.discover(p)[0])
            self.assertEqual(ep["agent_ids"], ["alice", "bob", "carol"])  # assignees + user 合并
            self.assertEqual(ep["main_agent"], "alice")
            self.assertEqual(ep["status"], "done")


class TestSessionLogsAdapter(unittest.TestCase):
    def test_codex_style(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "rollout-20250807-s1.jsonl")
            write(p,
                  '{"timestamp":"2025-05-07T17:24:21.123Z","type":"session_meta",'
                  '"payload":{"id":"s1","cwd":"/proj","originator":"codex","git":{"branch":"main"}}}\n'
                  '{"timestamp":"2025-05-07T17:24:22Z","type":"response_item",'
                  '"payload":{"type":"message","role":"assistant",'
                  '"content":[{"type":"output_text","text":"我来修复登录超时"}]}}\n'
                  '{"timestamp":"2025-05-07T17:25:00Z","type":"response_item",'
                  '"payload":{"type":"function_call","name":"shell","arguments":"{}"}}\n')
            a = adapters.SessionLogsAdapter()
            tasks = a.discover(tmp)
            self.assertEqual(len(tasks), 1)
            ep = a.to_episode(tmp, tasks[0])
            self.assertEqual(ep["agent_ids"], ["codex"])
            self.assertTrue(ep["output_signal"])       # assistant 文本
            self.assertEqual(ep["status"], "done")
            self.assertIn("修复登录超时", ep["title"])
            self.assertEqual(ep["business_join_key"]["table"], "session_logs")
            self.assertEqual(len(ep["comments"]), 1)   # 仅 assistant 文本 turn

    def test_claude_code_style(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "session-abc.jsonl")
            write(p,
                  '{"type":"user","sessionId":"s2","timestamp":"2026-05-21T00:00:00Z",'
                  '"cwd":"/home/u","gitBranch":"main",'
                  '"message":{"role":"user","content":[{"type":"text","text":"调研参考对象 A"}]}}\n'
                  '{"type":"assistant","sessionId":"s2","timestamp":"2026-05-21T00:01:00Z",'
                  '"message":{"role":"assistant","content":[{"type":"text","text":"好的按步骤调研"},'
                  '{"type":"tool_use","name":"Bash","input":{"command":"ls"}}]}}\n'
                  '{"type":"agent-name","sessionId":"s2","timestamp":"2026-05-21T00:02:00Z",'
                  '"message":{"role":"assistant","content":[{"type":"text","text":"research-agent"}]}}\n')
            a = adapters.SessionLogsAdapter()
            ep = a.to_episode(tmp, a.discover(tmp)[0])
            self.assertIn("research-agent", ep["agent_ids"])   # agent-name 行归因
            self.assertEqual(ep["title"], "调研参考对象 A")     # custom-title 缺失 → 首 turn
            self.assertTrue(ep["output_signal"])
            self.assertEqual(ep["comments"][0]["agent"], "user")
            self.assertEqual(ep["comments"][1]["agent"], "assistant")

    def test_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "empty.jsonl")
            write(p, "")
            a = adapters.SessionLogsAdapter()
            ep = a.to_episode(tmp, a.discover(tmp)[0])
            self.assertFalse(ep["output_signal"])
            self.assertEqual(ep["status"], "open")


class TestGitRepoAdapter(unittest.TestCase):
    def _make_repo(self, tmp: str) -> str:
        repo = os.path.join(tmp, "repo")
        os.makedirs(repo)
        subprocess.run(["git", "init", "-q", repo], check=True, capture_output=True)
        subprocess.run(["git", "-C", repo, "config", "user.email", "a@b.c"], check=True, capture_output=True)
        subprocess.run(["git", "-C", repo, "config", "user.name", "alice"], check=True, capture_output=True)
        for msg, content, fname in [
            ("fix: 修复登录超时", "timeout patch", "auth.py"),
            ("feat: 添加重试机制", "retry logic", "retry.py"),
        ]:
            write(os.path.join(repo, fname), content)
            subprocess.run(["git", "-C", repo, "add", fname], check=True, capture_output=True)
            subprocess.run(["git", "-C", repo, "commit", "-q", "-m", msg],
                           check=True, capture_output=True)
        return repo

    def test_commit_as_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._make_repo(tmp)
            a = adapters.GitRepoAdapter()
            tasks = a.discover(repo)
            self.assertGreaterEqual(len(tasks), 2)
            ep = a.to_episode(repo, tasks[0])
            self.assertEqual(ep["status"], "done")
            self.assertEqual(ep["agent_ids"], ["alice"])
            self.assertTrue(ep["output_signal"])
            self.assertEqual(ep["business_join_key"]["table"], "git_commit")
            self.assertTrue(ep["attachments"])          # 变更文件
            self.assertNotIn("merge", ep["title"].lower())

    def test_join_key_matches_issue_key(self):
        # 回归：business_join_key.id 必须与 manifest/episode 的 issue_key（短 hash）一致，
        # 否则 score 的 join 查证对不上（曾导致 git 源全部 low）
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._make_repo(tmp)
            a = adapters.GitRepoAdapter()
            ep = a.to_episode(repo, a.discover(repo)[0])
            self.assertEqual(ep["business_join_key"]["id"], ep["issue_key"])
            self.assertEqual(len(ep["business_join_key"]["id"]), 7)

    def test_max_commits_limit(self):
        # 大仓库历史窗口上限：SKILLTROVE_GIT_MAX_COMMITS 生效（O(n²) 向量层需要）
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._make_repo(tmp)  # 2 commits
            a = adapters.GitRepoAdapter()
            self.assertEqual(len(a.discover(repo)), 2)
            old = os.environ.get("SKILLTROVE_GIT_MAX_COMMITS")
            os.environ["SKILLTROVE_GIT_MAX_COMMITS"] = "1"
            try:
                self.assertEqual(len(a.discover(repo)), 1)
            finally:
                if old is None:
                    os.environ.pop("SKILLTROVE_GIT_MAX_COMMITS", None)
                else:
                    os.environ["SKILLTROVE_GIT_MAX_COMMITS"] = old


class TestTaskDirsAdapter(unittest.TestCase):
    def test_aliases_and_meta(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "TASK-1-调研", "description.md"), "# 全面调研\n调研正文")
            write(os.path.join(tmp, "TASK-1-调研", "comments.md"),
                  "## 2026-08-28T05:00:00Z | alice\n结论\n## 2026-08-28T06:00:00Z | carol\n补充\n")
            write(os.path.join(tmp, "TASK-2-开发", "task.md"), "# 实现重试\n代码实现\n")
            write(os.path.join(tmp, "TASK-2-开发", "meta.json"),
                  json.dumps({"status": "done", "agents": ["bob"]}))
            os.makedirs(os.path.join(tmp, "TASK-2-开发", "artifacts"))
            write(os.path.join(tmp, "TASK-2-开发", "artifacts", "retry.py"), "x")
            a = adapters.TaskDirsAdapter()
            tasks = a.discover(tmp)
            self.assertEqual(len(tasks), 2)
            ep1 = a.to_episode(tmp, next(t for t in tasks if t["id"] == "TASK-1"))
            self.assertEqual(ep1["agent_ids"], ["alice", "carol"])
            self.assertEqual(ep1["main_agent"], "alice")
            self.assertEqual(ep1["business_join_key"]["measured_at"], "2026-08-28T06:00:00Z")
            ep2 = a.to_episode(tmp, next(t for t in tasks if t["id"] == "TASK-2"))
            self.assertEqual(ep2["status"], "done")
            self.assertEqual(ep2["agent_ids"], ["bob"])
            self.assertEqual(ep2["attachments"], ["retry.py"])
            self.assertTrue(ep2["output_signal"])


class TestDocsAdapter(unittest.TestCase):
    def test_pool_only_degradation(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "经验一.md"), "# 做竞品调研\n先拆解再核实\n")
            write(os.path.join(tmp, "经验二.md"), "# 写单测\n先失败再实现\n")
            a = adapters.DocsAdapter()
            tasks = a.discover(tmp)
            self.assertEqual(len(tasks), 2)
            ep = a.to_episode(tmp, tasks[0])
            self.assertEqual(ep["title"], "做竞品调研")
            self.assertEqual(ep["agent_ids"], [])       # 无归因
            self.assertIsNone(ep["status"])             # 无完成态
            self.assertFalse(ep["output_signal"])
            self.assertEqual(ep["business_join_key"]["table"], "doc_file")


class TestEndToEnd(unittest.TestCase):
    """通用源 export → score（归一化 manifest）→ 能发现高分任务。"""

    def _export_and_score(self, source: str) -> dict:
        with tempfile.TemporaryDirectory() as out:
            adapter = adapters.pick_adapter(source)
            adapters.run_export(source, out, adapter)
            # 归一化 manifest 即 score 的 join 查证源
            with open(os.path.join(out, "manifest.json"), encoding="utf-8") as f:
                manifest = json.load(f)
            keys = {i["issue"] for i in manifest["issues"]}
            results = {}
            with open(os.path.join(out, "episodes.jsonl"), encoding="utf-8") as f:
                for line in f:
                    ep = json.loads(line)
                    results[ep["issue_key"]] = score.score_episode(ep, keys)["label"]
            return results

    def test_table_source_discovers_high(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "issues.json")
            write(p, json.dumps([
                {"number": 1, "title": "竞品调研机制拆解", "state": "MERGED",
                 "author": {"login": "a"}, "assignees": [{"login": "a"}],
                 "body": "全面调研", "createdAt": "2026-08-01T00:00:00Z",
                 "updatedAt": "2026-08-02T00:00:00Z"},
                {"number": 2, "title": "竞品调研证据核实", "state": "MERGED",
                 "author": {"login": "b"}, "assignees": [{"login": "b"}],
                 "body": "独立核实", "createdAt": "2026-08-01T00:00:00Z",
                 "updatedAt": "2026-08-02T00:00:00Z"},
                {"number": 3, "title": "写单元测试", "state": "OPEN",
                 "author": {"login": "c"}, "assignees": [{"login": "c"}],
                 "body": "计划", "createdAt": "2026-08-01T00:00:00Z",
                 "updatedAt": "2026-08-01T00:00:00Z"},
            ]))
            labels = self._export_and_score(p)
            self.assertEqual(labels["1"], "high")   # 关闭 + 产出信号
            self.assertEqual(labels["2"], "high")
            self.assertEqual(labels["3"], "low")    # open

    def test_session_source_discovers_high(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i, (title, agent) in enumerate([("竞品调研机制拆解", "codex"),
                                                ("竞品调研证据核实", "claude")]):
                write(os.path.join(tmp, f"s{i}.jsonl"),
                      f'{{"timestamp":"2026-08-01T00:00:00Z","type":"session_meta",'
                      f'"payload":{{"id":"s{i}","originator":"{agent}"}}}}\n'
                      f'{{"timestamp":"2026-08-01T00:01:00Z","type":"response_item",'
                      f'"payload":{{"type":"message","role":"assistant",'
                      f'"content":[{{"type":"output_text","text":"完成 {title} 的调研"}}]}}}}\n')
            labels = self._export_and_score(tmp)
            self.assertEqual(labels["s0"], "high")
            self.assertEqual(labels["s1"], "high")

    def test_docs_source_never_high(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "a.md"), "# 经验\n正文\n")
            write(os.path.join(tmp, "b.md"), "# 经验二\n正文\n")
            labels = self._export_and_score(tmp)
            self.assertEqual(set(labels.values()), {"low"})  # 降级：仅经验池


if __name__ == "__main__":
    unittest.main()
