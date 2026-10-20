"""The CI workflow against the rest of the lab: every action is pinned to a
commit, the token can't write, and CI runs every script the lab ships, so a
script nobody runs can't quietly stop working."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

# Scripts CI doesn't run by name, and why
NOT_RUN = {
    "generate-collector-config.sh": "a developer tool; the unit tests fail if its output is out of date",
    "install-offline.sh": "runs as install.sh, from inside the bundle",
}


class WorkflowTest(unittest.TestCase):
    def test_every_action_is_pinned_to_a_commit(self):
        uses = re.findall(r"uses: (\S+)(.*)$", WORKFLOW, re.MULTILINE)
        self.assertTrue(uses)
        for action, comment in uses:
            with self.subTest(action=action):
                self.assertRegex(action, r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")
                self.assertRegex(comment, r"^ # v\d+\.\d+\.\d+$")  # the version, for people and for Dependabot

    def test_the_token_is_read_only(self):
        self.assertRegex(WORKFLOW, r"(?m)^permissions:\n  contents: read$")
        self.assertNotRegex(WORKFLOW, r":\s*write\b")

    def test_ci_runs_every_script(self):
        scripts = {path.name for path in (ROOT / "scripts").glob("*.sh")}
        run = set(re.findall(r"scripts/([\w-]+\.sh)", WORKFLOW))
        self.assertLessEqual(run, scripts)
        self.assertEqual(scripts - run, set(NOT_RUN))


if __name__ == "__main__":
    unittest.main()