"""Tests for install-desktop.sh, the generator behind the launcher the user
actually clicks.

Nothing else in this suite covers it: the shim is shell, the desktop entry is
freedesktop data, and the icon is a file in a theme directory, so every one of
them can be wrong without a single Python or Rust test noticing. Two failures
did exactly that before this script existed. The launcher pointed at a copy of
the binary instead of the checkout, so it ran a build the user thought they had
replaced; and its icon resolved to the generic player glyph because the name in
the entry matched nothing installed. Both were invisible until someone looked
at a taskbar.

The script is run against a throwaway HOME, so the real ~/.local is never
touched, and with the build skipped - the assertions are about what gets
written, not about compiling.
"""

import hashlib
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "install-desktop.sh"


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_installer(home: pathlib.Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, HOME=str(home), EMBER_INSTALL_SKIP_BUILD="1")
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )


@unittest.skipIf(shutil.which("bash") is None, "bash is required to run the installer")
class InstallerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = pathlib.Path(tempfile.mkdtemp(prefix="ember-installer-"))
        cls.result = run_installer(cls.home)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home, ignore_errors=True)

    def setUp(self):
        self.assertEqual(
            self.result.returncode,
            0,
            f"installer failed:\n{self.result.stdout}\n{self.result.stderr}",
        )
        self.shim = self.home / ".local/bin/ember"
        self.desktop = self.home / ".local/share/applications/io.ember.Player.desktop"
        self.icon_svg = (
            self.home / ".local/share/icons/hicolor/scalable/apps/io.ember.Player.svg"
        )

    def test_shim_is_valid_shell_and_runs_the_checkout_binary(self):
        self.assertTrue(self.shim.is_file(), "no launcher written")
        self.assertTrue(os.access(self.shim, os.X_OK), "launcher is not executable")
        checked = subprocess.run(
            ["sh", "-n", str(self.shim)], capture_output=True, text=True
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)

        script = self.shim.read_text()
        binary = str(ROOT / "desktop-rust/target/release/ember-rust")
        # A copy of the binary is what made the old launcher lie: `bash run.sh`
        # rebuilt the checkout while the icon started whatever was copied.
        self.assertIn(binary, script, "launcher must exec the checkout build")
        self.assertNotIn(str(self.home), script, "launcher must not freeze the HOME it was written in")
        for step in ("desktop-env.sh", "ember_ensure_venv", "ember_export_cookies"):
            self.assertIn(step, script, f"launcher skips {step}, which run.sh does not")

    def test_desktop_entry_names_the_launcher_and_the_app_class(self):
        self.assertTrue(self.desktop.is_file(), "no desktop entry written")
        fields = {}
        for line in self.desktop.read_text().splitlines():
            if "=" in line and not line.startswith("["):
                key, _, value = line.partition("=")
                fields[key.strip()] = value.strip().strip('"')

        self.assertEqual(fields.get("Exec"), str(self.shim))
        self.assertEqual(fields.get("Icon"), "io.ember.Player")
        self.assertTrue(self.icon_svg.is_file(), "the entry names an icon nothing installed")
        self.assertEqual(
            self.icon_svg.read_bytes(), (ROOT / "packaging/ember.svg").read_bytes()
        )

        # The taskbar groups a window with its entry by WM class, which GTK
        # derives from the application id. If one of the two is renamed the
        # launcher still starts, and the running app simply appears as an
        # unnamed window with the wrong icon - the exact silence that hid the
        # stale bundle for a release.
        source = (ROOT / "desktop-rust/src/app.rs").read_text()
        marker = '.application_id("'
        start = source.index(marker) + len(marker)
        app_id = source[start : source.index('"', start)]
        self.assertEqual(fields.get("StartupWMClass"), app_id)

    def test_entry_validates_when_the_validator_is_available(self):
        if shutil.which("desktop-file-validate") is None:
            self.skipTest("desktop-file-validate is not installed")
        self.assertIn("Desktop entry validated.", self.result.stdout)

    def test_rerunning_is_the_same_install(self):
        before = {path: sha256(path) for path in (self.shim, self.desktop, self.icon_svg)}
        again = run_installer(self.home)
        self.assertEqual(again.returncode, 0, again.stderr)
        for path, digest in before.items():
            self.assertEqual(sha256(path), digest, f"re-running changed {path.name}")

    def test_no_stray_files_are_left_in_the_home(self):
        # A generator that leaves .tmp files beside its output makes the next
        # reader of ~/.local/share/applications guess which one is live.
        applications = self.home / ".local/share/applications"
        self.assertEqual(
            sorted(p.name for p in applications.glob("*.desktop")),
            ["io.ember.Player.desktop"],
        )
        leftovers = [p.name for p in applications.iterdir() if p.suffix == ".tmp"]
        leftovers += [p.name for p in (self.home / ".local/bin").iterdir() if p.suffix == ".tmp"]
        self.assertEqual(leftovers, [], "installer left temporary files behind")


if __name__ == "__main__":
    unittest.main()
