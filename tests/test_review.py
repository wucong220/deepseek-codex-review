"""Offline regression tests. Codex verdicts are fixtures, never real review approvals."""
import argparse
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("review_runner", ROOT / "scripts/review.py")
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
THREAD = "10000000-0000-4000-8000-000000000001"
OTHER = "20000000-0000-4000-8000-000000000002"
REQUIREMENTS = {"R1": "保留完整原文", "R2": "验证真实产物"}
FINGERPRINT = "a" * 64
WHICH = shutil.which


def verdict(phase="PLAN", status="PASS", requirements=None, fingerprint=FINGERPRINT):
    return {"task_id": "test", "phase": phase, "review_fingerprint": fingerprint,
            "verdict": status, "summary": "中文审查夹具",
            "checks": [{"requirement_id": key, "requirement": text, "result": "PASS",
                        "evidence": "离线测试证据，非实际审查结论"}
                       for key, text in (requirements or REQUIREMENTS).items()],
            "required_changes": []}


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.project = base / "project"
        self.project.mkdir()
        (self.project / "app.py").write_text("print(5)\n", encoding="utf-8")
        (base / "request.md").write_text("保留完整原文；验证真实产物", encoding="utf-8")
        (base / "plan.md").write_text("完整计划", encoding="utf-8")
        r.save_json(base / "requirements.json", {"requirements": [
            {"id": key, "text": text} for key, text in REQUIREMENTS.items()]})
        r.save_json(base / "artifacts.json", {"paths": ["app.py", "deliverables"]})
        self.init_args = argparse.Namespace(project=str(self.project), request_file=str(base / "request.md"),
            plan_file=str(base / "plan.md"), requirements_file=str(base / "requirements.json"),
            artifacts_file=str(base / "artifacts.json"), task_id="test")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(r.init_task(self.init_args), 0)
        self.task = self.project / "work/codex-review/test"
        self.commands = []

    def fake_success(self, command, prompt, run_dir, timeout, on_thread):
        self.commands.append(command)
        on_thread(THREAD)
        schema = r.read_json(run_dir / "schema.json")
        phase = schema["properties"]["phase"]["enum"][0]
        result = verdict(phase, requirements=r.requirements_file(run_dir / "requirements.json"),
                         fingerprint=schema["properties"]["review_fingerprint"]["enum"][0])
        result["task_id"] = schema["properties"]["task_id"]["enum"][0]
        r.save_json(run_dir / "result.json", result)
        return 0

    def call(self, name, effect=None, images=()):
        args = argparse.Namespace(command=name, task=str(self.task), image=images, timeout=10)
        with patch.object(r, "invoke", side_effect=effect or self.fake_success), \
             patch.object(r.shutil, "which", side_effect=lambda name: "codex-fixture" if name in {"codex", "codex.exe"} else WHICH(name)), \
             patch.object(r, "observed_settings", return_value=None), redirect_stdout(io.StringIO()):
            return r.review(args)

    def accepted(self, images=()):
        self.assertEqual(self.call("plan"), 0)
        (self.task / "execution.md").write_text("真实产物及原始验证证据", encoding="utf-8")
        self.assertEqual(self.call("accept", images=images), 0)
        self.assertTrue(self.valid())

    def valid(self):
        _, state = r.load_task(self.task)
        return r.acceptance_validity(self.task, state)[0]

    def review_task(self):
        """建一个没有 plan.md 的任务，模拟直接评审已有项目。"""
        self.init_args.task_id = "direct"
        self.init_args.plan_file = None
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(r.init_task(self.init_args), 0)
        self.task = self.project / "work/codex-review/direct"
        return json.loads(out.getvalue().strip().splitlines()[-1])

    def test_review_entry_does_not_require_plan_approval(self):
        """REVIEW 直接审已有项目：没有 plan.md 也能评审并通过 verify。"""
        payload = self.review_task()
        self.assertFalse(payload["has_plan"])
        self.assertEqual(payload["next"], "review")
        self.assertFalse((self.task / "plan.md").exists())
        (self.task / "execution.md").write_text("已有项目说明与验证证据", encoding="utf-8")
        self.assertEqual(self.call("review"), 0)
        state = r.read_json(self.task / "state.json")
        self.assertEqual(state["runs"][0]["phase"], "REVIEW")
        self.assertEqual(state["runs"][0]["status"], "PASS")
        # REVIEW 不写 approved_plan，但结论仍然有效
        self.assertIsNone(state["approved_plan"])
        self.assertTrue(self.valid())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(r.status(argparse.Namespace(task=str(self.task), command="verify")), 0)

    def test_review_still_enforces_full_requirement_coverage(self):
        """免掉方案审批不等于免掉审查标准：REVIEW 仍必须覆盖全部需求。"""
        self.review_task()
        (self.task / "execution.md").write_text("证据", encoding="utf-8")
        def omit_one(command, prompt, run_dir, timeout, on_thread):
            self.fake_success(command, prompt, run_dir, timeout, on_thread)
            path = run_dir / "result.json"
            result = r.read_json(path)
            result["checks"] = result["checks"][:1]
            r.save_json(path, result)
        self.assertEqual(self.call("review", omit_one), 12)
        self.assertFalse(self.valid())

    def test_review_binds_artifacts_and_detects_later_changes(self):
        """REVIEW 的 PASS 同样绑定产物版本，事后改动会失效。"""
        self.review_task()
        (self.task / "execution.md").write_text("证据", encoding="utf-8")
        self.assertEqual(self.call("review"), 0)
        self.assertTrue(self.valid())
        (self.project / "app.py").write_text("print(6)\n", encoding="utf-8")
        self.assertFalse(self.valid())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(r.status(argparse.Namespace(task=str(self.task), command="verify")), 10)

    def test_accept_still_requires_plan_approval(self):
        """PLAN_REQUIRED 任务里，没有 PLAN/PASS 就不能 accept。"""
        self.call("plan")  # 夹具默认任务带 plan.md，走 PLAN_REQUIRED
        (self.task / "execution.md").write_text("证据", encoding="utf-8")
        state = r.read_json(self.task / "state.json")
        state["approved_plan"] = None
        r.save_json(self.task / "state.json", state)
        (self.task / "request.md").write_text("需求变了", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "PLAN/PASS"):
            self.call("accept")

    def test_plan_without_plan_file_is_rejected(self):
        """DIRECT_REVIEW 任务不允许 plan，即使事后往目录里塞了 plan.md。"""
        self.review_task()
        (self.task / "plan.md").write_text("事后补的方案", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "不允许 plan"):
            self.call("plan")

    def test_workflow_is_fixed_at_init_and_cannot_be_switched_by_plan_md(self):
        """核心防绕过：带 plan.md 的任务是 PLAN_REQUIRED，不能改走 review。"""
        self.assertEqual(r.read_json(self.task / "state.json")["workflow"], "PLAN_REQUIRED")
        (self.task / "execution.md").write_text("证据", encoding="utf-8")
        for mutate in (lambda: (self.task / "plan.md").unlink(),
                       lambda: (self.task / "plan.md").write_text("换个说法", encoding="utf-8")):
            mutate()
            with self.subTest(), self.assertRaisesRegex(ValueError, "不允许 review"):
                self.call("review")

    def test_review_task_cannot_reach_accept_even_after_adding_plan_md(self):
        """DIRECT_REVIEW 任务不能靠补 plan.md 变成需要方案批准的任务。"""
        self.review_task()
        (self.task / "plan.md").write_text("事后补的方案", encoding="utf-8")
        (self.task / "execution.md").write_text("证据", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "不允许 accept"):
            self.call("accept")

    def test_legacy_task_with_plan_md_but_no_plan_run_is_plan_required(self):
        """旧任务：有 plan.md 但还没跑过 plan，必须判为 PLAN_REQUIRED。

        否则这类任务会被当成 DIRECT_REVIEW，重新打开绕过方案门禁的路径。
        """
        state = r.read_json(self.task / "state.json")
        del state["workflow"]
        state["runs"] = []
        state["approved_plan"] = None
        r.save_json(self.task / "state.json", state)
        self.assertTrue((self.task / "plan.md").exists())
        self.assertEqual(r.task_workflow(r.read_json(self.task / "state.json")), "PLAN_REQUIRED")
        (self.task / "execution.md").write_text("证据", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "不允许 review"):
            self.call("review")

    def test_legacy_state_without_workflow_is_always_plan_required(self):
        """旧任务没有 workflow 记录时一律判为 PLAN_REQUIRED，不做降级推断。

        旧版 init 都要求方案文件，因此「没有类型记录」不能被解释成可以直接 review。
        """
        state = r.read_json(self.task / "state.json")
        del state["workflow"]
        state["runs"] = []
        state["approved_plan"] = None
        r.save_json(self.task / "state.json", state)
        self.assertEqual(r.task_workflow(r.read_json(self.task / "state.json")), "PLAN_REQUIRED")
        refreshed = r.read_json(self.task / "state.json")
        self.assertEqual(refreshed["workflow"], "PLAN_REQUIRED")
        self.assertIn("workflow_migration_note", refreshed)

    def test_legacy_task_without_plan_md_cannot_be_reviewed(self):
        """P1-01：旧任务即便 plan.md 丢失，也不能被迁移成 DIRECT_REVIEW 绕过方案门禁。"""
        state = r.read_json(self.task / "state.json")
        del state["workflow"]
        state["runs"] = []
        state["approved_plan"] = None
        r.save_json(self.task / "state.json", state)
        (self.task / "plan.md").unlink()
        (self.task / "execution.md").write_text("证据", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "不允许 review"):
            self.call("review")
        self.assertEqual(r.read_json(self.task / "state.json")["workflow"], "PLAN_REQUIRED")
        # 不能产生任何有效交付结论
        self.assertFalse(r.acceptance_validity(self.task, r.read_json(self.task / "state.json"))[0])

    def test_invalid_workflow_value_is_rejected(self):
        state = r.read_json(self.task / "state.json")
        state["workflow"] = "WHATEVER"
        r.save_json(self.task / "state.json", state)
        with self.assertRaisesRegex(ValueError, "工作流类型无效"):
            self.call("plan")

    def test_verify_requires_final_phase_matching_workflow(self):
        """有效交付结论的完工阶段必须与任务类型匹配。"""
        self.review_task()
        (self.task / "execution.md").write_text("证据", encoding="utf-8")
        self.assertEqual(self.call("review"), 0)
        state = r.read_json(self.task / "state.json")
        self.assertTrue(r.acceptance_validity(self.task, state)[0])
        # 把任务类型改成 PLAN_REQUIRED 后，REVIEW/PASS 不再是有效交付结论
        state["workflow"] = "PLAN_REQUIRED"
        r.save_json(self.task / "state.json", state)
        valid, reason = r.acceptance_validity(self.task, r.read_json(self.task / "state.json"))
        self.assertFalse(valid)
        self.assertIn("ACCEPTANCE/PASS", reason)

    def test_consult_ids_are_unique_even_within_the_same_second(self):
        """P1-02：同一秒创建的多条咨询不能共用目录、不能互相覆盖记录。"""
        question = Path(self.tmp.name) / "q.md"
        question.write_text("问题", encoding="utf-8")
        def ok(command, prompt, run_dir, timeout, on_thread):
            on_thread(THREAD)
            return "答复"
        ids = []
        with patch.object(r, "invoke_consult", side_effect=ok), \
             patch.object(r.shutil, "which", side_effect=lambda name: "codex-fixture" if name in {"codex", "codex.exe"} else WHICH(name)), \
             redirect_stdout(io.StringIO()) as out:
            for _ in range(3):
                out.truncate(0); out.seek(0)
                self.assertEqual(r.consult(argparse.Namespace(project=str(self.project), question_file=str(question),
                                                              session=None, timeout=10)), 0)
                ids.append(json.loads(out.getvalue().strip().splitlines()[-1])["consult_id"])
        self.assertEqual(len(set(ids)), 3)
        for cid in ids:
            self.assertRegex(cid, r"^C\d{8}T\d{6}Z-[0-9a-f]{8}$")
            record = r.read_json(self.project / "work/codex-review-consult" / cid / "consult.json")
            self.assertEqual(record["consult_id"], cid)
            self.assertEqual(len(record["turns"]), 1)

    def test_consult_session_rejects_path_traversal(self):
        """P2-01：--session 只接受程序生成的 ID，拒绝越界路径。"""
        question = Path(self.tmp.name) / "q.md"
        question.write_text("问题", encoding="utf-8")
        for bad in ("../../etc", "..", "C:/Windows", "a/b", "C20261026T060000Z"):
            with self.subTest(session=bad), self.assertRaisesRegex(ValueError, "session"):
                r.consult(argparse.Namespace(project=str(self.project), question_file=str(question),
                                             session=bad, timeout=10))

    def test_consult_rejects_switching_to_a_different_session(self):
        """P2-01：续接咨询时返回不同会话 ID 必须拒绝。"""
        question = Path(self.tmp.name) / "q.md"
        question.write_text("问题", encoding="utf-8")
        def first(command, prompt, run_dir, timeout, on_thread):
            on_thread(THREAD)
            return "答复"
        with patch.object(r, "invoke_consult", side_effect=first), \
             patch.object(r.shutil, "which", side_effect=lambda name: "codex-fixture" if name in {"codex", "codex.exe"} else WHICH(name)), \
             redirect_stdout(io.StringIO()):
            r.consult(argparse.Namespace(project=str(self.project), question_file=str(question), session=None, timeout=10))
        cid = next((self.project / "work/codex-review-consult").iterdir()).name
        def other(command, prompt, run_dir, timeout, on_thread):
            on_thread(OTHER)
            return "答复"
        with patch.object(r, "invoke_consult", side_effect=other), \
             patch.object(r.shutil, "which", side_effect=lambda name: "codex-fixture" if name in {"codex", "codex.exe"} else WHICH(name)), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(r.consult(argparse.Namespace(project=str(self.project), question_file=str(question),
                                                          session=cid, timeout=10)), 12)
        record = r.read_json(self.project / "work/codex-review-consult" / cid / "consult.json")
        self.assertEqual(record["thread_id"], THREAD)

    def test_consult_records_are_excluded_from_artifact_snapshot(self):
        """P2-03：咨询记录不属于正式产物，不能影响 PASS 的有效性。"""
        self.review_task()
        (self.task / "execution.md").write_text("证据", encoding="utf-8")
        artifacts = r.read_json(self.task / "artifacts.json")
        artifacts["paths"] = ["app.py", "work"]
        r.save_json(self.task / "artifacts.json", artifacts)
        (self.project / "work" / "kept.txt").write_text("真实产物", encoding="utf-8")
        self.assertEqual(self.call("review"), 0)
        self.assertTrue(self.valid())
        # 新增一条咨询不应让已有结论变成 STALE
        consult_dir = self.project / "work" / "codex-review-consult" / "C20261026T060000Z-deadbeef"
        (consult_dir / "001").mkdir(parents=True)
        r.save_json(consult_dir / "consult.json", {"consult_id": "C20261026T060000Z-deadbeef"})
        (consult_dir / "001" / "response.md").write_text("答复", encoding="utf-8")
        self.assertTrue(self.valid())

    def test_verify_takes_the_lock_and_reports_busy(self):
        """P1-03：verify 必须持锁校验，运行中一律 BUSY/10。"""
        self.review_task()
        (self.task / "execution.md").write_text("证据", encoding="utf-8")
        self.assertEqual(self.call("review"), 0)
        with r.task_lock(self.task):
            out = io.StringIO()
            with redirect_stdout(out):
                code = r.status(argparse.Namespace(task=str(self.task), command="verify"))
        self.assertEqual(code, 10)
        self.assertEqual(json.loads(out.getvalue().strip().splitlines()[-1])["status"], "BUSY")
        # 锁释放后恢复可用
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(r.status(argparse.Namespace(task=str(self.task), command="verify")), 0)

    def test_preparing_retry_reuses_only_last_same_phase_record(self):
        """P2-02：只复用最后一条、同阶段、未启动 Codex 的记录。"""
        # 先在 PLAN 阶段制造一次准备失败
        original = r.shutil.copyfile
        state = {"fail": True}
        def flaky(src, dst, *a, **kw):
            if state["fail"] and Path(dst).name == "request.md":
                raise OSError("模拟失败")
            return original(src, dst, *a, **kw)
        with patch.object(r.shutil, "copyfile", side_effect=flaky):
            self.assertEqual(self.call("plan"), 12)
        runs = r.read_json(self.task / "state.json")["runs"]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["phase"], "PLAN")
        self.assertEqual(runs[0]["status"], "ERROR")
        # 换到另一阶段（这里直接构造一条已启动的 PLAN 记录）不应抢走它
        state["fail"] = False
        self.assertEqual(self.call("plan"), 0)
        runs = r.read_json(self.task / "state.json")["runs"]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "PASS")
        self.assertIsNotNone(runs[0]["command"])

    def test_consult_records_advice_without_approval(self):
        """咨询只留轻量记录，不产生 PASS/REVISE，也不算验收通过。"""
        answers = []
        def fake_consult(command, prompt, run_dir, timeout, on_thread):
            answers.append((prompt, run_dir))
            on_thread(THREAD)
            return "建议：先加边界检查。这是咨询，不构成验收。"
        question = Path(self.tmp.name) / "q.md"
        question.write_text("这个架构有并发风险吗？", encoding="utf-8")
        out = io.StringIO()
        with patch.object(r, "invoke_consult", side_effect=fake_consult), \
             patch.object(r.shutil, "which", side_effect=lambda name: "codex-fixture" if name in {"codex", "codex.exe"} else WHICH(name)), \
             redirect_stdout(out):
            code = r.consult(argparse.Namespace(project=str(self.project), question_file=str(question),
                                                session=None, timeout=10))
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(payload["status"], "COMPLETED")
        self.assertIn("不代表验收通过", payload["note"])
        # 记录独立于审核状态机，且没有任何批准字段
        root = self.project / "work/codex-review-consult" / payload["consult_id"]
        record = r.read_json(root / "consult.json")
        self.assertEqual(record["status"], "COMPLETED")
        self.assertEqual(record["thread_id"], THREAD)
        self.assertNotIn("approved_plan", record)
        self.assertNotIn("verdict", record)
        # 咨询记录与审核任务目录互不相干：另一个干净项目里咨询不会创建任何审核任务
        clean = Path(self.tmp.name) / "clean"
        clean.mkdir()
        (clean / "app.py").write_text("print(1)\n", encoding="utf-8")
        question.write_text("再问一次", encoding="utf-8")
        with patch.object(r, "invoke_consult", side_effect=fake_consult), \
             patch.object(r.shutil, "which", side_effect=lambda name: "codex-fixture" if name in {"codex", "codex.exe"} else WHICH(name)), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(r.consult(argparse.Namespace(project=str(clean), question_file=str(question),
                                                          session=None, timeout=10)), 0)
        self.assertFalse((clean / "work/codex-review").exists())
        self.assertTrue((clean / "work/codex-review-consult").is_dir())
        self.assertEqual(record["turns"][0]["status"], "COMPLETED")
        self.assertTrue(Path(record["turns"][0]["response_file"]).exists())

    def test_consult_resumes_same_session_and_never_fabricates(self):
        question = Path(self.tmp.name) / "q.md"
        question.write_text("第一个问题", encoding="utf-8")
        def ok(command, prompt, run_dir, timeout, on_thread):
            on_thread(THREAD)
            return "答复"
        with patch.object(r, "invoke_consult", side_effect=ok), \
             patch.object(r.shutil, "which", side_effect=lambda name: "codex-fixture" if name in {"codex", "codex.exe"} else WHICH(name)), \
             redirect_stdout(io.StringIO()):
            r.consult(argparse.Namespace(project=str(self.project), question_file=str(question), session=None, timeout=10))
        cid = next((self.project / "work/codex-review-consult").iterdir()).name
        question.write_text("追问", encoding="utf-8")
        commands = []
        def ok2(command, prompt, run_dir, timeout, on_thread):
            commands.append(command)
            return "答复2"
        with patch.object(r, "invoke_consult", side_effect=ok2), \
             patch.object(r.shutil, "which", side_effect=lambda name: "codex-fixture" if name in {"codex", "codex.exe"} else WHICH(name)), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(r.consult(argparse.Namespace(project=str(self.project), question_file=str(question),
                                                          session=cid, timeout=10)), 0)
        self.assertIn("resume", commands[0])
        self.assertIn(THREAD, commands[0])

    def test_consult_failure_is_not_a_passing_consultation(self):
        question = Path(self.tmp.name) / "q.md"
        question.write_text("问题", encoding="utf-8")
        def broken(command, prompt, run_dir, timeout, on_thread):
            raise TimeoutError("超时")
        with patch.object(r, "invoke_consult", side_effect=broken), \
             patch.object(r.shutil, "which", side_effect=lambda name: "codex-fixture" if name in {"codex", "codex.exe"} else WHICH(name)), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(r.consult(argparse.Namespace(project=str(self.project), question_file=str(question),
                                                          session=None, timeout=10)), 12)
        record = r.read_json(next((self.project / "work/codex-review-consult").iterdir()) / "consult.json")
        self.assertEqual(record["status"], "ERROR")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.project), *args], check=True,
                              capture_output=True).stdout.decode("utf-8")

    def git_task(self):
        if not WHICH("git"):
            self.skipTest("git not installed")
        self.git("init", "--quiet")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("add", "app.py")
        self.git("commit", "--quiet", "-m", "baseline")
        self.init_args.task_id = "git"
        with redirect_stdout(io.StringIO()):
            r.init_task(self.init_args)
        self.task = self.project / "work/codex-review/git"

    def test_acceptance_requires_current_plan_approval(self):
        (self.task / "execution.md").write_text("产物与证据", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "PLAN/PASS"):
            self.call("accept")
        self.assertEqual(self.call("plan"), 0)
        (self.task / "request.md").write_text("用户新增要求", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "PLAN/PASS"):
            self.call("accept")

    def test_requirement_or_artifact_scope_change_requires_new_plan(self):
        self.call("plan")
        (self.task / "execution.md").write_text("执行记录", encoding="utf-8")
        for name in ("requirements.json", "artifacts.json"):
            original = (self.task / name).read_bytes()
            (self.task / name).write_bytes(original + b" ")
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "PLAN/PASS"):
                self.call("accept")
            (self.task / name).write_bytes(original)

    def test_resume_exact_id_and_inherit_settings(self):
        self.accepted()
        first, second = self.commands
        self.assertNotIn("resume", first)
        self.assertIn("resume", second)
        self.assertEqual(second[-2:], [THREAD, "-"])
        for cmd in self.commands:
            self.assertEqual(cmd[cmd.index("--sandbox") + 1], "read-only")
            for forbidden in ("--last", "--ephemeral", "--model", "--ignore-user-config", "-c"):
                self.assertNotIn(forbidden, cmd)
        self.assertLess(second.index("--sandbox"), second.index("resume"))

    def test_failed_first_call_preserves_id_for_retry(self):
        def failure(cmd, prompt, run_dir, timeout, on_thread):
            on_thread(THREAD)
            raise TimeoutError("额度或网络错误")
        self.assertEqual(self.call("plan", failure), 12)
        self.assertEqual(r.read_json(self.task / "state.json")["thread_id"], THREAD)
        self.assertEqual(self.call("plan"), 0)
        self.assertIn("resume", self.commands[-1])

    def test_cross_task_session_does_not_replace_existing(self):
        self.call("plan")
        def wrong_session(cmd, prompt, run_dir, timeout, on_thread):
            on_thread(OTHER)
        self.assertEqual(self.call("plan", wrong_session), 12)
        self.assertEqual(r.read_json(self.task / "state.json")["thread_id"], THREAD)

    def test_missing_final_output_is_not_pass(self):
        def no_output(cmd, prompt, run_dir, timeout, on_thread):
            on_thread(THREAD)
        self.assertEqual(self.call("plan", no_output), 12)
        self.assertIsNone(r.read_json(self.task / "state.json")["approved_plan"])

    def test_unidentified_prior_session_is_not_silently_replaced(self):
        def no_id(cmd, prompt, run_dir, timeout, on_thread):
            self.fake_success(cmd, prompt, run_dir, timeout, lambda _: None)
        self.assertEqual(self.call("plan", no_id), 12)
        with self.assertRaisesRegex(ValueError, "没有明确会话 ID"):
            self.call("plan")

    def test_input_edit_during_review_invalidates_conclusion(self):
        def mutate(*args):
            self.fake_success(*args)
            (self.task / "plan.md").write_text("审查期间修改方案", encoding="utf-8")
        self.assertEqual(self.call("plan", mutate), 12)
        self.assertIsNone(r.read_json(self.task / "state.json")["approved_plan"])

    def test_fail_unverified_empty_checks_and_wrong_identity_cannot_pass(self):
        for condition in ("FAIL", "UNVERIFIED", "empty", "phase", "task", "changes", "fingerprint"):
            result = verdict()
            if condition in {"FAIL", "UNVERIFIED"}:
                result["checks"][0]["result"] = condition
            elif condition == "empty":
                result["checks"] = []
            elif condition == "phase":
                result["phase"] = "ACCEPTANCE"
            elif condition == "task":
                result["task_id"] = "someone-else"
            elif condition == "fingerprint":
                result["review_fingerprint"] = "b" * 64
            else:
                result["required_changes"] = ["仍需修复"]
            with self.subTest(condition=condition), self.assertRaises(ValueError):
                r.validate_result(result, "test", "PLAN", REQUIREMENTS, FINGERPRINT)

    def test_missing_duplicate_unknown_and_unrelated_requirements_rejected(self):
        for condition in ("missing", "duplicate", "unknown", "unrelated"):
            result = verdict()
            if condition == "missing":
                result["checks"].pop()
            elif condition == "duplicate":
                result["checks"][1] = result["checks"][0].copy()
            elif condition == "unknown":
                result["checks"][0]["requirement_id"] = "R999"
            else:
                result["checks"][0]["requirement"] = "只检查 README 存在"
            with self.subTest(condition=condition), self.assertRaises(ValueError):
                r.validate_result(result, "test", "PLAN", REQUIREMENTS, FINGERPRINT)

    def test_invalid_requirement_manifest_rejected(self):
        for items in ([], [{"id": "R1", "text": ""}],
                      [{"id": "R1", "text": "a"}, {"id": "R1", "text": "b"}]):
            r.save_json(self.task / "requirements.json", {"requirements": items})
            with self.subTest(items=items), self.assertRaises(ValueError):
                self.call("plan")

    def test_expected_revise_and_blocked_are_preserved(self):
        for status, code in (("REVISE", 10), ("BLOCKED", 11)):
            def answer(*args):
                self.fake_success(*args)
                run_dir = args[2]
                result = r.read_json(run_dir / "result.json")
                result["verdict"] = status
                result["checks"][0]["result"] = "UNVERIFIED"
                result["required_changes"] = ["补充真实证据"]
                r.save_json(run_dir / "result.json", result)
            self.assertEqual(self.call("plan", answer), code)
            self.assertIsNone(r.read_json(self.task / "state.json")["approved_plan"])

    def test_lock_rejects_simultaneous_calls(self):
        with r.task_lock(self.task):
            with self.assertRaises(FileExistsError):
                self.call("plan")
        self.assertFalse((self.task / ".review.lock").exists())

    def test_status_reports_progress_while_review_is_running(self):
        """审查运行期间 status 必须能查到进度（旧实现会因抢锁而报错）。"""
        observed = {}
        def slow(cmd, prompt, run_dir, timeout, on_thread):
            out = io.StringIO()
            with redirect_stdout(out):
                code = r.status(argparse.Namespace(task=str(self.task), command="status"))
            observed["code"] = code
            observed["payload"] = json.loads(out.getvalue().strip().splitlines()[-1])
            self.fake_success(cmd, prompt, run_dir, timeout, on_thread)
        self.assertEqual(self.call("plan", slow), 0)
        self.assertEqual(observed["code"], 0)
        self.assertEqual(observed["payload"]["status"], "RUNNING")
        self.assertIsNotNone(observed["payload"]["running"])
        self.assertIsNone(observed["payload"]["running"].get("unreadable"))

    def test_verify_refuses_while_review_is_running(self):
        """审查进行中材料仍在变化，verify 不能给出交付结论。"""
        observed = {}
        def slow(cmd, prompt, run_dir, timeout, on_thread):
            out = io.StringIO()
            with redirect_stdout(out):
                code = r.status(argparse.Namespace(task=str(self.task), command="verify"))
            observed["code"] = code
            observed["payload"] = json.loads(out.getvalue().strip().splitlines()[-1])
            self.fake_success(cmd, prompt, run_dir, timeout, on_thread)
        self.assertEqual(self.call("plan", slow), 0)
        self.assertEqual(observed["code"], 10)
        self.assertEqual(observed["payload"]["status"], "BUSY")
        self.assertFalse(observed["payload"]["current_acceptance_valid"])

    def test_preparation_failure_is_retryable(self):
        """准备阶段失败后必须能重试（旧实现留下目录却不登记，重试永久 mkdir 失败）。"""
        original = r.shutil.copyfile
        state = {"fail": True}
        def flaky_copy(src, dst, *a, **kw):
            if state["fail"] and Path(dst).name == "request.md":
                raise OSError("模拟准备阶段写盘失败")
            return original(src, dst, *a, **kw)
        with patch.object(r.shutil, "copyfile", side_effect=flaky_copy):
            self.assertEqual(self.call("plan"), 12)
        runs = r.read_json(self.task / "state.json")["runs"]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "ERROR")
        self.assertIn("准备阶段失败", runs[0]["error"])
        state["fail"] = False
        self.assertEqual(self.call("plan"), 0)
        # 失败的那轮没有真正启动 Codex，重试就地复用它，不会产生重复编号的目录
        runs = r.read_json(self.task / "state.json")["runs"]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "PASS")
        plan_dirs = sorted(p.name for p in (self.task / "runs").iterdir())
        self.assertEqual(plan_dirs, ["001-plan"])
        self.assertNotIn("准备阶段失败", json.dumps(runs, ensure_ascii=False))

    def test_artifact_change_during_acceptance_rejects_pass(self):
        self.call("plan")
        (self.task / "execution.md").write_text("执行记录", encoding="utf-8")
        def mutate(*args):
            self.fake_success(*args)
            (self.project / "app.py").write_text("print(6)\n", encoding="utf-8")
        self.assertEqual(self.call("accept", mutate), 12)
        self.assertFalse(self.valid())

    def test_content_changes_without_report_changes_invalidate_pass(self):
        self.accepted()
        hashes = r.input_hashes(self.task, "ACCEPTANCE")
        (self.project / "app.py").write_text("print(6)\n", encoding="utf-8")
        self.assertEqual(hashes, r.input_hashes(self.task, "ACCEPTANCE"))
        self.assertFalse(self.valid())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(r.status(argparse.Namespace(task=str(self.task), command="verify")), 10)

    def test_addition_and_deletion_inside_artifact_directory_invalidate_pass(self):
        folder = self.project / "deliverables"
        folder.mkdir()
        output = folder / "report.txt"
        output.write_text("正式内容", encoding="utf-8")
        self.accepted()
        output.unlink()
        self.assertFalse(self.valid())
        output.write_text("正式内容", encoding="utf-8")
        self.assertTrue(self.valid())
        (folder / "new.txt").write_text("新增正式文件", encoding="utf-8")
        self.assertFalse(self.valid())

    def test_explicit_external_artifact_is_bound(self):
        external = Path(self.tmp.name) / "external-report.txt"
        external.write_text("版本 A", encoding="utf-8")
        r.save_json(self.task / "artifacts.json", {"paths": [str(external)]})
        self.accepted()
        external.write_text("版本 B", encoding="utf-8")
        self.assertFalse(self.valid())

    def test_images_are_bound(self):
        preview = self.project / "preview.png"
        preview.write_bytes(b"image fixture A")
        self.accepted(images=[str(preview)])
        preview.write_bytes(b"image fixture B")
        self.assertFalse(self.valid())

    def plan_image(self):
        # Outside the project so Git/declared artifacts cannot accidentally bind it.
        image = Path(self.tmp.name) / "design.png"
        image.write_bytes(b"design fixture A")
        self.assertEqual(self.call("plan", images=[str(image)]), 0)
        (self.task / "execution.md").write_text("真实产物记录", encoding="utf-8")
        return image

    def test_plan_image_changes_review_fingerprint(self):
        image = self.plan_image()
        first = r.read_json(self.task / "state.json")["runs"][-1]["review_fingerprint"]
        image.write_bytes(b"design fixture B")
        self.assertEqual(self.call("plan", images=[str(image)]), 0)
        second = r.read_json(self.task / "state.json")["runs"][-1]["review_fingerprint"]
        self.assertNotEqual(first, second)

    def test_plan_image_change_before_acceptance_requires_new_plan(self):
        image = self.plan_image()
        image.write_bytes(b"design fixture B")
        with self.assertRaisesRegex(ValueError, "图片|附图|PLAN/PASS"):
            self.call("accept")

    def test_plan_image_deletion_before_acceptance_requires_new_plan(self):
        image = self.plan_image()
        image.unlink()
        with self.assertRaisesRegex(ValueError, "图片|附图|PLAN/PASS"):
            self.call("accept")

    def test_plan_image_change_during_plan_rejects_pass(self):
        image = Path(self.tmp.name) / "design.png"
        image.write_bytes(b"design fixture A")
        def mutate(*args):
            self.fake_success(*args)
            image.write_bytes(b"design fixture B")
        self.assertEqual(self.call("plan", mutate, images=[str(image)]), 12)
        self.assertIsNone(r.read_json(self.task / "state.json")["approved_plan"])

    def test_plan_image_is_reused_at_acceptance_without_new_image_argument(self):
        image = self.plan_image()
        self.assertEqual(self.call("accept"), 0)
        self.assertIn(str(image.resolve()), self.commands[-1])
        state = r.read_json(self.task / "state.json")
        self.assertIn(str(image.resolve()), state["runs"][-1]["images"])
        self.assertTrue(self.valid())

    def test_plan_image_change_during_acceptance_rejects_pass(self):
        image = self.plan_image()
        def mutate(*args):
            self.fake_success(*args)
            image.write_bytes(b"design fixture B")
        self.assertEqual(self.call("accept", mutate), 12)
        self.assertFalse(self.valid())

    def test_plan_image_change_after_acceptance_invalidates_verify(self):
        image = self.plan_image()
        self.assertEqual(self.call("accept"), 0)
        self.assertTrue(self.valid())
        image.write_bytes(b"design fixture B")
        self.assertFalse(self.valid())

    def test_legacy_plan_image_approval_requires_new_plan(self):
        self.plan_image()
        state = r.read_json(self.task / "state.json")
        state["approved_plan"] = r.input_hashes(self.task, "PLAN")
        r.save_json(self.task / "state.json", state)
        with self.assertRaisesRegex(ValueError, "旧|附图|PLAN/PASS"):
            self.call("accept")

    def test_legacy_plan_without_images_remains_valid(self):
        self.accepted()
        state = r.read_json(self.task / "state.json")
        state["approved_plan"] = r.input_hashes(self.task, "PLAN")
        r.save_json(self.task / "state.json", state)
        self.assertTrue(self.valid())

    def test_result_file_edits_invalidate_pass(self):
        self.accepted()
        state = r.read_json(self.task / "state.json")
        output = Path(state["runs"][-1]["run_dir"]) / "result.json"
        result = r.read_json(output)
        result["summary"] = "手工改写"
        r.save_json(output, result)
        self.assertFalse(self.valid())

    def test_new_revise_cannot_reuse_older_acceptance_pass(self):
        self.accepted()
        def revise(*args):
            self.fake_success(*args)
            output = args[2] / "result.json"
            result = r.read_json(output)
            result["verdict"] = "REVISE"
            result["checks"][0]["result"] = "FAIL"
            result["required_changes"] = ["修正内容"]
            r.save_json(output, result)
        self.assertEqual(self.call("accept", revise), 10)
        self.assertFalse(self.valid())

    def test_review_runtime_does_not_invalidate_artifacts(self):
        self.accepted()
        (self.task / "unrelated-log.txt").write_text("日志", encoding="utf-8")
        self.assertTrue(self.valid())

    def test_git_baseline_and_status_remain_clean(self):
        self.git_task()
        state = r.read_json(self.task / "state.json")
        self.assertEqual(state["baseline"]["status"], "")
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=all"), "")
        self.accepted()
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=all"), "")

    def test_git_untracked_files_outside_declared_paths_are_bound(self):
        self.git_task()
        self.accepted()
        (self.project / "unexpected.py").write_text("extra = True", encoding="utf-8")
        self.assertFalse(self.valid())

    def test_ignored_but_declared_artifacts_are_bound(self):
        self.git_task()
        (self.project / ".gitignore").write_text("deliverables/\n", encoding="utf-8")
        folder = self.project / "deliverables"
        folder.mkdir()
        output = folder / "report.txt"
        output.write_text("版本 A", encoding="utf-8")
        self.accepted()
        output.write_text("版本 B", encoding="utf-8")
        self.assertFalse(self.valid())

    def test_git_head_and_index_are_bound(self):
        self.git_task()
        self.accepted()
        self.git("commit", "--quiet", "--allow-empty", "-m", "head changed")
        self.assertFalse(self.valid())
        self.assertEqual(self.call("accept"), 0)
        app = self.project / "app.py"
        app.write_text("print(6)\n", encoding="utf-8")
        self.git("add", "app.py")
        app.write_text("print(5)\n", encoding="utf-8")
        self.assertFalse(self.valid())

    def test_git_nested_project_excludes_only_its_review_records(self):
        self.git_task()
        nested = self.project / "nested [docs]"
        nested.mkdir()
        (nested / "app.py").write_text("print(5)\n", encoding="utf-8")
        self.git("add", "nested [docs]/app.py")
        self.git("commit", "--quiet", "-m", "nested fixture")
        self.project = nested
        self.init_args.project = str(nested)
        self.init_args.task_id = "nested"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(r.init_task(self.init_args), 0)
        self.task = nested / "work/codex-review/nested"
        state = r.read_json(self.task / "state.json")
        self.assertEqual(state["baseline"]["status"], "")
        self.assertNotIn("git_exclude_warning", state)
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=all"), "")

    def test_linked_artifact_parent_is_rejected(self):
        actual = self.project / "actual"
        actual.mkdir()
        (actual / "report.txt").write_text("内容", encoding="utf-8")
        linked = self.project / "linked"
        if sys.platform == "win32":
            if not WHICH("node"):
                self.skipTest("Node needed to create an unprivileged Windows junction")
            subprocess.run(["node", "-e", "require('node:fs').symlinkSync(process.argv[1], process.argv[2], 'junction')",
                            str(actual), str(linked)], check=True, capture_output=True)
        else:
            linked.symlink_to(actual, target_is_directory=True)
        self.assertTrue(r.is_link(linked))
        r.save_json(self.task / "artifacts.json", {"paths": ["linked/report.txt"]})
        with self.assertRaisesRegex(ValueError, "链接|联接"):
            r.artifact_snapshot(self.task, r.read_json(self.task / "state.json"))

    def test_review_runtime_is_not_an_explicit_artifact(self):
        for path in ("work/codex-review", ".git/config", "src/**"):
            r.save_json(self.task / "artifacts.json", {"paths": [path]})
            with self.subTest(path=path), self.assertRaises(ValueError):
                r.artifact_paths(self.task / "artifacts.json", self.project)

    def test_old_task_upgrade_preserves_session_and_invalidates_approval(self):
        self.accepted()
        state = r.read_json(self.task / "state.json")
        state["format"] = 1
        r.save_json(self.task / "state.json", state)
        self.assertFalse(self.valid())
        with self.assertRaisesRegex(ValueError, "upgrade"):
            self.call("plan")
        args = argparse.Namespace(task=str(self.task), requirements_file=self.init_args.requirements_file,
                                  artifacts_file=self.init_args.artifacts_file)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(r.upgrade_task(args), 0)
        upgraded = r.read_json(self.task / "state.json")
        self.assertEqual(upgraded["thread_id"], THREAD)
        self.assertEqual(upgraded["runs"], state["runs"])
        self.assertIsNone(upgraded["approved_plan"])
        self.assertEqual(self.call("plan"), 0)
        self.assertEqual(self.commands[-1][-2:], [THREAD, "-"])

    def test_real_subprocess_utf8_event_stream_errors_and_timeout(self):
        fake = self.project / "fake_cli.py"
        fake.write_text('''import json,sys,time
from pathlib import Path
prompt=sys.stdin.buffer.read().decode("utf-8")
Path("received.txt").write_text(prompt,encoding="utf-8")
sys.stderr.buffer.write("诊断信息".encode("utf-8"));sys.stderr.flush()
print(json.dumps({"type":"thread.started","thread_id":"10000000-0000-4000-8000-000000000001"}),flush=True)
if prompt=="sleep":time.sleep(60)
if prompt=="error":print(json.dumps({"type":"error","message":"failure"}),flush=True)
print(json.dumps({"type":"turn.completed"}),flush=True)
''', encoding="utf-8")
        ids = []
        for index, prompt in enumerate(("中文与 UTF-8", "error", "sleep")):
            run_dir = self.project / f"transport-{index}"
            run_dir.mkdir()
            cmd = [sys.executable, str(fake), "-C", str(self.project)]
            if prompt == "error":
                with self.assertRaises(RuntimeError):
                    r.invoke(cmd, prompt, run_dir, 5, ids.append)
            elif prompt == "sleep":
                with self.assertRaises(TimeoutError):
                    r.invoke(cmd, prompt, run_dir, 0.5, ids.append)
                self.assertNotEqual(r.read_json(run_dir / "process.json")["exit_code"], 0)
            else:
                self.assertEqual(r.invoke(cmd, prompt, run_dir, 5, ids.append), 0)
                self.assertEqual((self.project / "received.txt").read_text(encoding="utf-8"), prompt)
                self.assertEqual((run_dir / "stderr.log").read_text(encoding="utf-8"), "诊断信息")
        self.assertEqual(ids, [THREAD] * 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
