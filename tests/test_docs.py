"""Links in the README and the docs point at files that exist. GitHub shows a
broken relative link as a 404, and nothing else would notice."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES = [ROOT / "README.md", ROOT / "scenarios" / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")


class DocsTest(unittest.TestCase):
    def test_relative_links_point_at_files_that_exist(self):
        checked = 0
        for page in PAGES:
            for target in LINK.findall(page.read_text(encoding="utf-8")):
                if target.startswith(("http://", "https://")):
                    continue
                with self.subTest(page=page.name, link=target):
                    self.assertTrue((page.parent / target).exists(), f"{page.name} links to {target}")
                checked += 1
        self.assertGreater(checked, 5)


if __name__ == "__main__":
    unittest.main()