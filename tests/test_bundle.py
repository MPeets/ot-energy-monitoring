"""The offline bundle and installer against the rest of the lab.

The bundle takes its image versions from .env.example, while compose.yaml
falls back to versions of its own when .env doesn't set one. If those drift
apart, the images tested here aren't the images shipped. The two scripts also
have to fingerprint images the same way, or every install fails its check."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLE_SH = (ROOT / "scripts" / "bundle.sh").read_text(encoding="utf-8")
INSTALL_SH = (ROOT / "scripts" / "install-offline.sh").read_text(encoding="utf-8")


def function(script: str, name: str) -> str:
    return re.search(rf"^{name}\(\) \{{\n.*?^\}}$", script, re.MULTILINE | re.DOTALL).group(0)


class BundleTest(unittest.TestCase):
    def test_both_scripts_fingerprint_images_the_same_way(self):
        self.assertEqual(function(BUNDLE_SH, "fingerprint"), function(INSTALL_SH, "fingerprint"))

    def test_every_image_is_pinned_to_the_version_in_env_example(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        env = dict(re.findall(r"^(\w+)=(.*)$", (ROOT / ".env.example").read_text(encoding="utf-8"), re.MULTILINE))
        images = re.findall(r"^\s*image: (\S+)$", compose, re.MULTILINE)
        self.assertTrue(images)
        for image in images:
            with self.subTest(image=image):
                match = re.fullmatch(r"[\w./-]+:\$\{(\w+):-([\w.-]+)\}", image)
                self.assertTrue(match, f"{image} isn't pinned through a version variable")
                variable, fallback = match.groups()
                self.assertEqual(env.get(variable), fallback, f"{variable} in .env.example")

    def test_the_installer_never_pulls(self):
        self.assertNotRegex(INSTALL_SH, r"docker (image )?pull")
        self.assertIn("docker compose up -d --pull never", INSTALL_SH)


if __name__ == "__main__":
    unittest.main()