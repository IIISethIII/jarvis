"""Workspace changes touch single entries only and leave everything else byte-identical.

Pure standard library, runs anywhere:  python -m unittest discover tests/unit
"""
import os
import shutil
import tempfile
import unittest

from core import workspace

MEMORY = "# MEMORY\n\n## Sport\n- Zone 2: 147-159 bpm\n- Owns a road bike\n\n## Home\n- Lives in Munich\n"


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.environ["JARVIS_WORKSPACE"] = self.dir
        workspace.ensure()
        workspace._write(workspace._path("MEMORY.md"), MEMORY)

    def tearDown(self):
        os.environ.pop("JARVIS_WORKSPACE", None)
        shutil.rmtree(self.dir)

    def apply(self, *changes, source="test"):
        run = workspace.Run(source)
        errors = [run.apply(c) for c in changes]
        run.commit()
        return errors

    def test_replace_keeps_the_rest_identical(self):
        self.assertEqual(self.apply({"target": "MEMORY", "action": "replace", "old_text": "147-159", "text": "148-160"}), [None])
        self.assertEqual(workspace.read_file("MEMORY"), MEMORY.replace("147-159", "148-160"))

    def test_replace_does_not_double_the_bullet(self):
        self.apply({"target": "MEMORY", "action": "replace", "old_text": "Owns a road bike", "text": "- Owns two road bikes"})
        self.assertEqual(workspace.read_file("MEMORY"), MEMORY.replace("Owns a road bike", "Owns two road bikes"))

    def test_remove_drops_only_that_line(self):
        self.apply({"target": "MEMORY", "action": "remove", "old_text": "road bike"})
        self.assertEqual(workspace.read_file("MEMORY"), MEMORY.replace("- Owns a road bike\n", ""))

    def test_add_into_section(self):
        self.apply({"target": "MEMORY", "action": "add", "text": "Climbs twice a week", "section": "Sport"})
        self.assertEqual(workspace.read_file("MEMORY"),
                         MEMORY.replace("- Owns a road bike\n", "- Owns a road bike\n- Climbs twice a week\n"))

    def test_add_splits_bullets_and_strips_section_hashes(self):
        self.apply({"target": "MEMORY", "action": "add", "text": "- Climbs twice a week\n- Owns a road bike\n- Runs 10 km", "section": "## Sport"})
        self.assertEqual(workspace.read_file("MEMORY"),
                         MEMORY.replace("- Owns a road bike\n", "- Owns a road bike\n- Climbs twice a week\n- Runs 10 km\n"))

    def test_add_with_heading_line_goes_into_that_section(self):
        self.apply({"target": "MEMORY", "action": "add", "text": "## Home\n- Has a balcony\n- Third floor"})
        self.assertEqual(workspace.read_file("MEMORY"), MEMORY + "- Has a balcony\n- Third floor\n")

    def test_rejects_duplicates_ambiguity_and_budget(self):
        errors = self.apply(
            {"target": "MEMORY", "action": "add", "text": "- owns a ROAD bike"},
            {"target": "MEMORY", "action": "replace", "old_text": "- ", "text": "x"},
            {"target": "MEMORY", "action": "add", "text": "x" * 6000},
        )
        self.assertIn("duplicate", errors[0])
        self.assertIn("occurs", errors[1])
        self.assertIn("budget", errors[2])
        self.assertEqual(workspace.read_file("MEMORY"), MEMORY)

    def test_locked_targets_are_rejected(self):
        run = workspace.Run("test")
        run.locked = {"SOUL"}
        self.assertIn("can't be changed", run.apply({"target": "SOUL", "action": "add", "text": "Be grumpy"}))

    def test_undo_restores_previous_state(self):
        self.apply({"target": "MEMORY", "action": "remove", "old_text": "Munich"})
        self.apply({"action": "undo"})
        self.assertEqual(workspace.read_file("MEMORY"), MEMORY)
        self.assertIn("undo", "".join(workspace.recent_changes()))

    def test_skills_create_load_and_usage(self):
        self.apply({"target": "skill", "action": "create", "skill": "yamaha-sleep",
                    "description": "Set the sleep timer of the Yamaha receiver", "text": "Press sleep: 120, 90, 60, 30, off."})
        self.assertEqual([s["name"] for s in workspace.list_skills()], ["yamaha-sleep"])
        self.assertIn("120, 90", workspace.load_skills(["yamaha-sleep", "nope"]))
        self.assertIn("yamaha-sleep", workspace.skill_usage())
        self.apply({"target": "skill", "action": "delete", "skill": "yamaha-sleep"})
        self.assertEqual(workspace.list_skills(), [])

    def test_skill_steps_may_follow_in_a_separate_add(self):
        self.apply({"target": "skill", "action": "create", "skill": "good-night", "description": "Bedtime routine"},
                   {"target": "skill", "action": "add", "skill": "good-night", "text": "1. Lights off."},
                   {"target": "skill", "action": "create", "skill": "empty", "description": "Never filled"},
                   {"target": "skill", "action": "create", "skill": "morning", "description": "Morning routine"},
                   {"target": "skill", "action": "replace", "skill": "morning", "old_text": "", "text": "1. Lights on."})
        self.assertEqual([s["name"] for s in workspace.list_skills()], ["good-night", "morning"])
        self.assertEqual([s["body"] for s in workspace.list_skills()], ["1. Lights off.", "1. Lights on."])

    def test_notes_queue(self):
        workspace.add_note("remember A")
        workspace.add_note("remember B")
        self.assertEqual([n["note"] for n in workspace.read_notes()], ["remember A", "remember B"])
        workspace.drop_notes(1)
        self.assertEqual([n["note"] for n in workspace.read_notes()], ["remember B"])


class PromptTest(unittest.TestCase):
    def test_guides_follow_loaded_tools(self):
        from core import prompt
        with_music = "\n".join(prompt.guides({"music_play", "music_control"}))
        self.assertIn("VOLUME & MUSIC", with_music)
        self.assertNotIn("TIMERS", with_music)
        self.assertIn("TIMERS", "\n".join(prompt.guides({"manage_timer"})))

    def test_templates_render_without_placeholders_left(self):
        from core import prompt
        tmp = tempfile.mkdtemp()
        os.environ["JARVIS_WORKSPACE"] = tmp
        try:
            text = prompt.static_prompt({"call_ha_service", "manage_timer", "ask_openclaw", "music_play", "music_control"})
        finally:
            os.environ.pop("JARVIS_WORKSPACE")
            shutil.rmtree(tmp)
        self.assertNotRegex(text, r"\{[a-z_]+\}")
        self.assertIn("PERSONALITY", text)


if __name__ == "__main__":
    unittest.main()
