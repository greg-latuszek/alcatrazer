"""Alcatrazer CLI entry point.

Usage:
    alcatrazer start    — set up (first run) or start the container (subsequent)
    alcatrazer test     — run bundled test suite to verify installation
    alcatrazer version  — show version

Legacy (pre-config-split) placeholders that will be retired:
    alcatrazer init, alcatrazer update
"""

import sys
import unittest
from pathlib import Path

from alcatrazer import __version__
from alcatrazer import start as start_module


def run_tests(smoke: bool = False) -> int:
    """Run the bundled test suite. Returns 0 on success, 1 on failure."""
    package_dir = Path(__file__).resolve().parent
    loader = unittest.TestLoader()
    suite = loader.discover(str(package_dir / "tests"))
    if smoke:
        # Include Docker integration tests (require Docker to be set up)
        integration_suite = loader.discover(str(package_dir / "integration_tests"))
        suite.addTests(integration_suite)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print("Alcatrazer — secure AI agent workspace")
        print()
        print("Usage:")
        print("  alcatrazer start     Set up (first run) or start the container")
        print("  alcatrazer test      Run bundled tests to verify installation")
        print("  alcatrazer version   Show version")
        print()
        print(f"Version: {__version__}")
        print("https://github.com/greg-latuszek/alcatrazer")
        return

    command = sys.argv[1]

    if command == "version":
        print(f"alcatrazer {__version__}")
    elif command == "start":
        sys.exit(start_module.cmd_start(Path.cwd()))
    elif command == "test":
        smoke = "--smoke" in sys.argv
        sys.exit(run_tests(smoke=smoke))
    elif command == "init":
        print(f"alcatrazer {__version__} — init")
        print()
        print("Interactive installer not yet implemented.")
        print("This is a placeholder release to reserve the PyPI package name.")
        sys.exit(1)
    elif command == "update":
        print(f"alcatrazer {__version__} — update")
        print()
        print("Update not yet implemented.")
        sys.exit(1)
    else:
        print(f"Unknown command: {command}")
        print("Run 'alcatrazer --help' for usage.")
        sys.exit(1)


if __name__ == "__main__":
    main()
