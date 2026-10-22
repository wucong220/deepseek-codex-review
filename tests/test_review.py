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
