"""纯文字偏好须贯穿模型输入、保存、迁移和后续正常轮次。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import test_title as fixtures
import oil_codex_title as title
from codex_adapter import generate_title, normalize_project_prefix


class PlainTitleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.backend = fixtures.FakeBackend()
        self.config = {**title.DEFAULTS, "title_style": "plain"}
        title.atomic_json(self.root / "config.json", self.config)

    def tearDown(self):
        self.tmp.cleanup()

    def process(self, model=fixtures.proposal):
        return title.process_thread(self.backend, model, fixtures.ID, self.root, self.config,
                                    apply=True, event_turn=self.backend.thread["turns"][-1]["id"])

    def test_hook_saves_plain_title_even_if_model_returns_category_icon(self):
        result = self.process()
        self.assertEqual(result["status"], "renamed")
        self.assertEqual(self.backend.read(fixtures.ID)["name"], "产品视频｜讲解大纲")

    def test_keep_old_icon_migrates_only_format_and_later_confirmation_stays_plain(self):
        self.backend.thread["name"] = "📝 会议室预约｜冲突处理规则"
        model = Mock(return_value=({"action": "keep", "title": "", "reason": "主线准确"}, {}))
        self.assertEqual(self.process(model)["title"], "会议室预约｜冲突处理规则")
        self.backend.thread["turns"].append({"id": fixtures.NEW_TURN, "status": "completed",
            "items": [{"type": "userMessage", "content": [{"type": "text", "text": "好的"}]}]})
        result = self.process(model)
        self.assertEqual(result["status"], "kept")
        self.assertEqual(result["title"], "会议室预约｜冲突处理规则")
        self.assertEqual(result["skip_reason"], "confirmation_only")
        self.assertEqual(model.call_count, 1)

    def test_style_change_rechecks_unchanged_history(self):
        self.config["title_style"] = "emoji"
        title.atomic_json(self.root / "config.json", self.config)
        self.process()
        self.config["title_style"] = "plain"
        title.atomic_json(self.root / "config.json", self.config)
        result = self.process()
        self.assertEqual(result["status"], "renamed")
        self.assertEqual(result["title"], "产品视频｜讲解大纲")

    def test_style_change_while_model_runs_discards_old_style(self):
        def change(context):
            title.atomic_json(self.root / "config.json", {"title_style": "emoji"})
            return fixtures.proposal(context)
        self.assertEqual(self.process(change)["status"], "configuration_changed")
        self.assertEqual(self.backend.writes, [])

    def test_plain_mode_still_rejects_icons_in_body_and_unsafe_output(self):
        for text in ("会议室｜安排 🚀", "会议室｜安排\n", "会议室｜a@b.com", "会议室||安排"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                title.validate_candidate({"action": "rename", "title": text, "reason": ""}, "", "plain")
        for plain in ("Email verification｜Fix expiry", "A｜B"):
            result = title.validate_candidate({"action": "rename", "title": plain, "reason": ""}, "", "plain")
            self.assertEqual(result["title"], plain)

    def test_model_receives_setting_and_plain_prefix_dedup_preserves_content(self):
        proposal = {"action": "rename", "title": "Kite LMS 邮箱注册｜修复", "reason": ""}
        context = {"original_goal": "修复邮箱注册", "project_hint": "kite-lms", "current_title": "旧标题"}
        with patch("codex_adapter.generate_json", return_value=(proposal, {})) as model:
            result, _ = generate_title("unused", self.config, context, fixtures.ROOT)
        self.assertEqual(model.call_args.args[2]["title_style"], "plain")
        self.assertEqual(result["title"], "邮箱注册｜修复")
        other = {**proposal, "title": "Kite LMS 与 Cedar｜同步"}
        self.assertEqual(normalize_project_prefix(other, context), other)

    def test_configure_persists_plain_without_changing_model(self):
        proc = subprocess.run([sys.executable, str(fixtures.ROOT / "scripts/oil_codex_title.py"),
            "configure", "--title-style", "plain"], capture_output=True, text=True,
            env=os.environ | {"OIL_CODEX_TITLE_DATA": str(self.root)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        saved = json.loads((self.root / "config.json").read_text())
        self.assertEqual(saved["title_style"], "plain")
        self.assertEqual(saved["model"], self.config["model"])

    def test_locked_title_remains_protected_in_plain_mode(self):
        title.atomic_json(title.state_path(self.root, fixtures.ID), {"locked": True})
        model = Mock(side_effect=AssertionError("不能调用模型"))
        self.assertEqual(self.process(model)["status"], "locked")
        self.assertEqual(self.backend.writes, [])
