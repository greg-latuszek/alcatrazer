"""Alcatrazer CLI entry point.

Usage:
    alcatrazer init     — install Alcatrazer into the current git repository
    alcatrazer test     — run bundled test suite to verify installation
    alcatrazer update   — update tool files in an existing installation
    alcatrazer version  — show version
"""

import sys
import unittest

from alcatrazer import __version__


def run_tests(smoke: bool = False) -> int:
    """Run the bundled test suite. Returns 0 on success, 1 on failure."""
    from pathlib import Path

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
        print("  alcatrazer init      Install into the current git repository")
        print("  alcatrazer test      Run bundled tests to verify installation")
        print("  alcatrazer update    Update tool files in existing installation")
        print("  alcatrazer version   Show version")
        print()
        print(f"Version: {__version__}")
        print("https://github.com/greg-latuszek/alcatrazer")
        return

    command = sys.argv[1]

    if command == "version":
        print(f"alcatrazer {__version__}")
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
