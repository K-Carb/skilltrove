"""SkillTrove Web 看板（FastAPI）：读产物 + 审核动作（Deliverable Plan M0–M2）。

运行：python web/app.py  （或 uvicorn 手动启动）
API：
  GET  /                     看板单页
  GET  /api/registry         registry.json 全量
  GET  /api/skills/<name>    skill 详情（SKILL.md / 证据 / evals）
  GET  /api/candidates       聚类候选
  GET  /api/metrics          M1–M4 指标
  GET  /api/recalls          回采 run 列表
  POST /api/review/<name>    {"status": "published"|"deprecated"} 审核动作（写 registry）
"""

from __future__ import annotations

import json
import os
import sys

from pydantic import BaseModel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cli import registry as registry_mod  # noqa: E402
from cli import llm as llm_mod  # noqa: E402
from web import pipeline  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


class NoCacheStaticFiles(StaticFiles):
    """静态资源禁止缓存（no-cache/no-store）：前端迭代快，浏览器缓存旧 JS 会
    反复出现"看不到新功能"的假象（曾因缓存看不到流水线/一键发现）。

    注意：本 Starlette 版本 get_response 是 async（返回 coroutine），必须 await。
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response


def _load(path: str, default=None):
    if not os.path.isfile(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _read_text(path: str) -> str:
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


def registry_path() -> str:
    return os.path.join(ROOT, "registry", "registry.json")


def _skill_frontmatter(name: str) -> dict:
    """从 SKILL.md frontmatter 取人类可读字段（description / when_to_use）。"""
    import re

    text = _read_text(os.path.join(ROOT, "skills", name, "SKILL.md"))
    out = {}
    for key in ("description", "when_to_use"):
        m = re.search(rf"^{key}:\s*(.+)$", text, re.M)
        if m:
            out[key] = m.group(1).strip()
    return out


# ---------------------------------------------------------------------------
# 请求模型（必须定义在模块顶层：闭包内模型 + `from __future__ import annotations`
# 会使 FastAPI 的 get_type_hints 解析不到，请求体被误判为 query 参数而恒 422）
# ---------------------------------------------------------------------------

class ReviewBody(BaseModel):
    """审核请求体：status = published / deprecated / in_review。"""

    status: str


class PipelineRunBody(BaseModel):
    """流水线运行请求体：steps = export/score/cluster/draft/publish/recall 子集。"""

    steps: list[str]
    source: str = ""
    adapter: str = "auto"
    threshold: float | None = None
    exclude_agents: str = ""
    llm_backend: str = ""
    skill: str = ""
    status: str = "published"
    agent: str = ""
    candidate: str = ""


class IssueBody(BaseModel):
    """技能问题反馈请求体。"""

    text: str


# ---------------------------------------------------------------------------
# 数据查询
# ---------------------------------------------------------------------------

def get_registry() -> dict:
    reg = _load(registry_path(), {"schema": "skilltrove-registry-v1", "skills": []})
    # 给每个技能补人类可读字段（内部代号 implementation-research 之外的说明与适用场景）
    for s in reg.get("skills", []):
        fm = _skill_frontmatter(s["name"])
        s["description"] = fm.get("description", "")
        s["when_to_use"] = fm.get("when_to_use", "")
    return reg


def issues_path() -> str:
    return os.path.join(ROOT, "data", "skill-issues.json")


def get_skill_detail(name: str) -> dict | None:
    reg = get_registry()
    entry = next((s for s in reg["skills"] if s["name"] == name), None)
    if entry is None:
        return None
    base = os.path.join(ROOT, "skills", name)
    detail = dict(entry)
    detail["skill_md"] = _read_text(os.path.join(base, "SKILL.md"))
    fm = _skill_frontmatter(name)
    detail["description"] = fm.get("description", "")
    detail["when_to_use"] = fm.get("when_to_use", "")
    detail["review_notes"] = _read_text(os.path.join(base, "references", "review-notes.md"))
    detail["evidence_index"] = _load(os.path.join(base, "references", "evidence-index.json"), {})
    cases = []
    cases_root = os.path.join(base, "evals", "cases")
    if os.path.isdir(cases_root):
        for cid in sorted(os.listdir(cases_root)):
            cdir = os.path.join(cases_root, cid)
            if os.path.isdir(cdir):
                cases.append({
                    "id": cid,
                    "config": _read_text(os.path.join(cdir, "config.yaml")),
                    "fixture": _load(os.path.join(cdir, "fixture", "evidence.json"), {}),
                })
    detail["cases"] = cases
    # 回采结果
    detail["eval_results"] = []
    results_root = os.path.join(ROOT, "evals", "results")
    if os.path.isdir(results_root):
        for fn in sorted(os.listdir(results_root)):
            if fn.endswith(".json"):
                detail["eval_results"].append({"file": fn, "content": _read_text(os.path.join(results_root, fn))})
    return detail


def get_candidates() -> dict:
    return _load(os.path.join(ROOT, "data", "candidates.json"), {"candidates": []})


def get_recalls() -> list[dict]:
    runs_root = os.path.join(ROOT, "data", "recall-runs")
    runs = []
    if os.path.isdir(runs_root):
        for rid in sorted(os.listdir(runs_root), reverse=True):
            rdir = os.path.join(runs_root, rid)
            if not os.path.isdir(rdir):
                continue
            result = _load(os.path.join(rdir, "result.json"), {})
            output = _read_text(os.path.join(rdir, "agent-output.md"))
            runs.append({"run_id": rid, "result": result, "output": output})
    return runs


def get_metrics() -> dict:
    reg = get_registry()
    skills = reg.get("skills", [])

    applied_total = sum((s.get("usage") or {}).get("applied_count", 0) for s in skills)
    distinct_appliers = set()
    for s in skills:
        distinct_appliers.update((s.get("usage") or {}).get("applier_ids", []))
    published = [s for s in skills if s.get("review_status") == "published"]
    deprecated = [s for s in skills if s.get("review_status") == "deprecated"]
    drafts = [s for s in skills if s.get("review_status") in ("draft", "in_review")]

    high_count = 0
    total_count = 0
    if os.path.isfile(os.path.join(ROOT, "archive", "scored.jsonl")):
        with open(os.path.join(ROOT, "archive", "scored.jsonl"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                total_count += 1
                if json.loads(line).get("score", {}).get("label") == "high":
                    high_count += 1

    recalls = get_recalls()
    runs_with_applied = sum(1 for r in recalls if (r.get("result") or {}).get("applied"))

    m4_denom = len(published) + len(deprecated)
    return {
        "M1_复用次数": {"value": applied_total, "unit": "次（applied 累计）", "note": "采用率，非效果（效果由 eval 判定）"},
        "M3_调用率口径": {"value": f"{runs_with_applied}/{len(recalls)}", "unit": "run 数",
                        "note": "有 applied 的 run / 总回采 run"},
        "M4_审核通过率": {"value": f"{len(published)}/{m4_denom}" if m4_denom else "0/0",
                        "unit": "通过/已审结", "note": "首个批次结果即基线"},
        "M2_重复探索口径": {"value": f"高分 {high_count} / 总 {total_count}", "unit": "episode 数",
                          "note": "MVP 无 holdout，仅口径基线"},
        "skill 状态": {"published": len(published), "deprecated": len(deprecated),
                      "draft/in_review": len(drafts), "distinct_appliers": len(distinct_appliers)},
    }


# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------

def create_app():
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="SkillTrove 看板", version="0.1.0")

    @app.get("/api/registry")
    def api_registry():
        return get_registry()

    @app.get("/api/skills/{name}")
    def api_skill(name: str):
        detail = get_skill_detail(name)
        if detail is None:
            raise HTTPException(404, f"skill 不存在: {name}")
        return detail

    @app.get("/api/candidates")
    def api_candidates():
        return get_candidates()

    @app.get("/api/metrics")
    def api_metrics():
        return get_metrics()

    @app.get("/api/recalls")
    def api_recalls():
        return get_recalls()

    @app.get("/api/skill-issues")
    def api_skill_issues():
        """技能问题反馈列表（消费侧反馈闭环，收件箱展示）。"""
        return {"issues": _load(issues_path(), [])}

    @app.post("/api/skills/{name}/issue")
    def api_skill_issue(name: str, body: IssueBody):
        """提交技能问题反馈（技能被使用后发现过时/错误时的回传通道）。"""
        text = body.text.strip()
        if not text:
            raise HTTPException(400, "反馈内容不能为空")
        if registry_mod.find_entry(get_registry(), name) is None:
            raise HTTPException(404, f"skill 不存在: {name}")
        issues = _load(issues_path(), [])
        import datetime

        issues.insert(0, {
            "skill": name, "text": text, "status": "open",
            "ts": datetime.datetime.now().astimezone().isoformat(),
        })
        os.makedirs(os.path.dirname(issues_path()), exist_ok=True)
        with open(issues_path(), "w", encoding="utf-8") as f:
            json.dump(issues, f, ensure_ascii=False, indent=2)
        return {"ok": True}

    @app.post("/api/review/{name}")
    def api_review(name: str, body: ReviewBody):
        """审核动作：更新 registry 审核状态（published/deprecated/in_review）。"""
        reg = get_registry()
        entry = registry_mod.find_entry(reg, name)
        if entry is None:
            raise HTTPException(404, f"skill 不存在: {name}")
        try:
            registry_mod.set_status(reg, name, body.status)
        except ValueError as e:
            raise HTTPException(400, str(e))
        registry_mod.save_registry(reg, registry_path())
        return {"ok": True, "name": name, "review_status": body.status}

    # ---------------- 可视化流水线（D-交互） ----------------

    @app.post("/api/pipeline/run")
    def api_pipeline_run(body: PipelineRunBody):
        """提交一次流水线运行（串行队列），返回 run_id。"""
        if not body.steps:
            raise HTTPException(400, "steps 不能为空")
        for s in body.steps:
            if s not in ("export", "score", "cluster", "draft", "publish", "recall"):
                raise HTTPException(400, f"未知步骤: {s}")
        if ("export" in body.steps) and not body.source:
            raise HTTPException(400, "export 步骤需要 source（数据源路径）")
        if ("publish" in body.steps or "recall" in body.steps) and not body.skill:
            raise HTTPException(400, "publish/recall 步骤需要 skill 名")
        params = body.model_dump()
        run_id = pipeline.start_run(body.steps, params)
        return {"run_id": run_id, "status": "queued"}

    @app.get("/api/pipeline/runs")
    def api_pipeline_runs():
        return {"runs": pipeline.list_jobs()}

    @app.get("/api/pipeline/runs/{run_id}")
    def api_pipeline_run_detail(run_id: str):
        job = pipeline.get_job(run_id)
        if job is None:
            raise HTTPException(404, f"run 不存在: {run_id}")
        return job

    @app.get("/api/pipeline/runs/{run_id}/logs")
    def api_pipeline_logs(run_id: str, after: int = 0):
        data = pipeline.logs_since(run_id, after)
        if data is None:
            raise HTTPException(404, f"run 不存在: {run_id}")
        return data

    @app.post("/api/pipeline/runs/{run_id}/cancel")
    def api_pipeline_cancel(run_id: str):
        ok = pipeline.cancel_run(run_id)
        if not ok:
            raise HTTPException(404, f"run 不存在: {run_id}")
        return {"ok": True, "run_id": run_id}

    @app.get("/api/sources")
    def api_sources():
        """可选的本地数据源（含适配器自动识别结果）。"""
        return {"sources": pipeline.detect_sources()}

    @app.get("/api/llm_status")
    def api_llm_status():
        """LLM 后端可用性（不实际调用，只探测 CLI 是否在 PATH）。"""
        backend = os.environ.get("LLM_BACKEND", "kimi")
        probes = llm_mod.probe()
        # CLI 型后端可本地探测；openai-compatible 等自定义后端无法探测，置 None
        available = probes.get(backend) if backend in probes else None
        return {"backend": backend, "available": available}

    @app.get("/api/pipeline/config")
    def api_pipeline_config():
        return {
            "adapter_choices": list(pipeline.ADAPTER_CHOICES),
            "step_choices": ["export", "score", "cluster", "draft", "publish", "recall"],
            "sources_dirs": pipeline.SOURCES_DIRS,
            "llm_default": __import__("os").environ.get("LLM_BACKEND", "kimi"),
        }

    @app.get("/")
    def index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    app.mount("/static", NoCacheStaticFiles(directory=STATIC_DIR), name="static")
    return app


if __name__ == "__main__":
    import uvicorn

    app = create_app()
    host = os.environ.get("SKILLTROVE_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("SKILLTROVE_WEB_PORT", "8000"))
    log_dir = os.path.join(ROOT, "web")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "server.log")
    uvicorn.run(app, host=host, port=port, log_level="info",
                log_config={
                    "version": 1,
                    "disable_existing_loggers": False,
                    "formatters": {"default": {"format": "%(asctime)s %(levelname)s %(message)s"}},
                    "handlers": {"file": {"class": "logging.FileHandler", "filename": log_file,
                                          "formatter": "default", "encoding": "utf-8"},
                                 "console": {"class": "logging.StreamHandler", "formatter": "default"}},
                    "root": {"handlers": ["file", "console"], "level": "INFO"},
                })
