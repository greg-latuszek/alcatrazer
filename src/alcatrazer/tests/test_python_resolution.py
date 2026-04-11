"""
Tests for src/resolve_python.sh — four-tier Python 3.11+ resolution.

Each test creates fake binaries in a temp fakebin/ directory and runs
resolve_python.sh with a controlled PATH (fakebin + /usr/bin:/bin)
and simulated stdin input for interactive prompts.
"""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

# tests/ is at src/alcatrazer/tests/ — project root is 3 levels up
PROJECT_DIR = Path(__file__).resolve().parent.parent.parent.parent
RESOLVE_SCRIPT = str(PROJECT_DIR / "src" / "alcatrazer" / "scripts" / "resolve_python.sh")
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def write_script(path: Path, content: str):
    """Write an executable bash script."""
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def fake_python(version: str, tomllib_ok: bool = True) -> str:
    """Return bash script content for a fake python3 binary."""
    tomllib_line = (
        "exit 0"
        if tomllib_ok
        else (
            'if echo "$2" | grep -q "tomllib"; then\n'
            '    echo "ModuleNotFoundError" >&2; exit 1\n'
            "fi\n"
            "exit 0"
        )
    )
    return (
        "#!/usr/bin/env bash\n"
        'if [ "${1:-}" = "--version" ]; then\n'
        f'    echo "Python {version}"\n'
        'elif [ "${1:-}" = "-c" ]; then\n'
        f"    {tomllib_line}\n"
        "fi\n"
    )


def broken_python() -> str:
    """Bash script that shadows python3 — always fails."""
    return "#!/bin/bash\nexit 127\n"


def fake_mise() -> str:
    """Bash script for a fake mise that 'installs' python3 next to itself."""
    return (
        "#!/usr/bin/env bash\n"
        'case "$1" in\n'
        "    use)\n"
        '        FAKEBIN="$(dirname "$0")"\n'
        "        cat > \"${FAKEBIN}/python3\" << 'PYEOF'\n"
        "#!/usr/bin/env bash\n"
        'if [ "${1:-}" = "--version" ]; then\n'
        '    echo "Python 3.11.9"\n'
        'elif [ "${1:-}" = "-c" ]; then\n'
        "    exit 0\n"
        "fi\n"
        "PYEOF\n"
        '        chmod +x "${FAKEBIN}/python3"\n'
        '        echo "mise: installing python@3.11..."\n'
        "        ;;\n"
        "    which)\n"
        '        FAKEBIN="$(dirname "$0")"\n'
        '        if [ -x "${FAKEBIN}/python3" ]; then\n'
        '            echo "${FAKEBIN}/python3"\n'
        "        else\n"
        "            exit 1\n"
        "        fi\n"
        "        ;;\n"
        "esac\n"
    )


class ResolutionTestBase(unittest.TestCase):
    """Base class with helpers for running resolve_python.sh."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.alcatraz_dir = os.path.join(self.tmpdir, "alcatrazer")
        self.fakebin = os.path.join(self.tmpdir, "fakebin")
        os.makedirs(self.alcatraz_dir)
        os.makedirs(self.fakebin)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @property
    def python_file(self) -> Path:
        return Path(self.alcatraz_dir) / "python"

    def run_resolve(self, stdin_text: str = "", extra_env: dict | None = None):
        """Run resolve_python.sh with fakebin-first PATH."""
        env = {
            "PATH": f"{self.fakebin}:{SYSTEM_PATH}",
            "HOME": os.path.join(self.tmpdir, "home"),
        }
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [RESOLVE_SCRIPT, "--alcatraz-dir", self.alcatraz_dir],
            input=stdin_text,
            capture_output=True,
            text=True,
            env=env,
        )


class TestTier1SystemPython(ResolutionTestBase):
    """Tier 1: detect python3 3.11+ on PATH."""

    def test_detects_system_python(self):
        write_script(Path(self.fakebin) / "python3", fake_python("3.12.0"))
        result = self.run_resolve()
        self.assertEqual(result.returncode, 0)
        self.assertTrue(self.python_file.is_symlink())
        resolved = os.readlink(self.python_file)
        self.assertEqual(resolved, os.path.join(self.fakebin, "python3"))

    def test_output_mentions_version(self):
        write_script(Path(self.fakebin) / "python3", fake_python("3.12.0"))
        result = self.run_resolve()
        self.assertIn("3.12", result.stdout)


class TestTier2MiseInstall(ResolutionTestBase):
    """Tier 2: mise available, no python3 — install via mise."""

    def test_installs_python_via_mise(self):
        write_script(Path(self.fakebin) / "python3", broken_python())
        write_script(Path(self.fakebin) / "mise", fake_mise())
        result = self.run_resolve(stdin_text="y\n")
        self.assertEqual(result.returncode, 0)
        self.assertTrue(self.python_file.exists())

    def test_output_mentions_mise(self):
        write_script(Path(self.fakebin) / "python3", broken_python())
        write_script(Path(self.fakebin) / "mise", fake_mise())
        result = self.run_resolve(stdin_text="y\n")
        self.assertIn("mise", result.stdout.lower())


class TestTier3MiseBootstrap(ResolutionTestBase):
    """Tier 3: no python3, no mise — install mise then Python."""

    def setUp(self):
        super().setUp()
        self.home_dir = os.path.join(self.tmpdir, "home")
        self.local_bin = os.path.join(self.home_dir, ".local", "bin")
        os.makedirs(self.local_bin, exist_ok=True)

        # Create a mise helper script that will be "installed" by fake curl
        self.mise_helper = os.path.join(self.tmpdir, "mise_helper.sh")
        write_script(Path(self.mise_helper), fake_mise())

        # Fake curl: outputs a shell script that copies mise_helper into place
        mise_target = os.path.join(self.local_bin, "mise")
        curl_script = (
            "#!/usr/bin/env bash\n"
            f"cat << INSTALLER\n"
            f"#!/bin/sh\n"
            f'mkdir -p "{self.local_bin}"\n'
            f'cp "{self.mise_helper}" "{mise_target}"\n'
            f'chmod +x "{mise_target}"\n'
            f"INSTALLER\n"
        )
        write_script(Path(self.fakebin) / "curl", curl_script)
        write_script(Path(self.fakebin) / "python3", broken_python())

    def test_bootstraps_mise_then_python(self):
        result = self.run_resolve(stdin_text="y\ny\n")
        self.assertEqual(result.returncode, 0)
        self.assertTrue(self.python_file.exists())

    def test_output_mentions_mise_install(self):
        result = self.run_resolve(stdin_text="y\ny\n")
        self.assertRegex(result.stdout, r"(?i)install.*mise")


class TestTier4ManualPath(ResolutionTestBase):
    """Tier 4: user provides manual path."""

    def test_accepts_manual_path(self):
        write_script(Path(self.fakebin) / "python3", broken_python())
        custom_python = os.path.join(self.tmpdir, "my-python")
        write_script(Path(custom_python), fake_python("3.13.0"))

        # "n" declines mise bootstrap, then provide the path
        result = self.run_resolve(stdin_text=f"n\n{custom_python}\n")
        self.assertEqual(result.returncode, 0)
        resolved = os.readlink(self.python_file)
        self.assertEqual(resolved, custom_python)


class TestCaching(ResolutionTestBase):
    """Test that previously resolved path is reused."""

    def test_reuses_cached_python(self):
        custom_python = os.path.join(self.fakebin, "mypython")
        write_script(Path(custom_python), fake_python("3.11.9"))
        os.symlink(custom_python, self.python_file)

        result = self.run_resolve()
        self.assertEqual(result.returncode, 0)
        self.assertRegex(result.stdout, r"(?i)reus|already")


class TestRejection(ResolutionTestBase):
    """Test rejection of invalid Python versions."""

    def test_rejects_python_below_311(self):
        write_script(
            Path(self.fakebin) / "python3",
            fake_python("3.10.4", tomllib_ok=False),
        )
        # "n" declines mise, empty string declines manual path
        result = self.run_resolve(stdin_text="n\n\n")
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
