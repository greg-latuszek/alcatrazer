"""Alcatrazer CLI entry point.

Usage:
    alcatrazer init     — install Alcatrazer into the current git repository
    alcatrazer update   — update tool files in an existing installation
    alcatrazer version  — show version
"""

import sys

from alcatrazer import __version__


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print("Alcatrazer — secure AI agent workspace")
        print()
        print("Usage:")
        print("  alcatrazer init      Install into the current git repository")
        print("  alcatrazer update    Update tool files in existing installation")
        print("  alcatrazer version   Show version")
        print()
        print(f"Version: {__version__}")
        print("https://github.com/greg-latuszek/alcatrazer")
        return

    command = sys.argv[1]

    if command == "version":
        print(f"alcatrazer {__version__}")
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
