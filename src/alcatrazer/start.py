"""The `alcatrazer start` command — primary entry point for daily work.

Steps 3a-3d so far: route on `.alcatrazer/` presence; in the first-time
branch verify we are at a git repo root; helpers to read + prompt for the
promotion identity; wizard that collects the coding-environment answers
(languages, OS packages, startup commands). Step 3e will orchestrate all
these helpers into the first-time flow. Steps 3f-3k fill in the rest.
Step 4 handles subsequent-run.
"""

import subprocess
import sys
from pathlib import Path


def cmd_start(project_dir: Path) -> int:
    """Route to first-time setup or subsequent-run based on project state."""
    if not (project_dir / ".alcatrazer").exists():
        return _first_time_setup(project_dir)
    return _subsequent_run(project_dir)


def _first_time_setup(project_dir: Path) -> int:
    if not (project_dir / ".git").exists():
        print("alcatrazer must be run from a git repository root.", file=sys.stderr)
        return 1
    print("No alcatrazer setup found in this repository.")
    print("First-time setup flow is not yet implemented.")
    return 0


def read_git_identity(project_dir: Path) -> tuple[str | None, str | None]:
    """Return (name, email) from git config — local first, global fallback, per field."""

    def get(scope: str, key: str) -> str | None:
        result = subprocess.run(
            ["git", "config", f"--{scope}", "--get", key],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        value = result.stdout.strip()
        return value if result.returncode == 0 and value else None

    name = get("local", "user.name") or get("global", "user.name")
    email = get("local", "user.email") or get("global", "user.email")
    return name, email


def ask_promotion_identity(project_dir: Path) -> tuple[str, str]:
    """Prompt the user for the promotion identity, offering the detected one as default."""
    name, email = read_git_identity(project_dir)
    if name and email:
        print(f"Detected git identity: {name} <{email}>")
        answer = input("Use for promoted commits? [Y/n] ").strip().lower()
        if answer in ("", "y", "yes"):
            return name, email

    print("Please enter the identity to use for promoted commits.")
    entered_name = input("Name: ").strip()
    entered_email = input("Email: ").strip()
    return entered_name, entered_email


SUPPORTED_LANGUAGES: dict[str, dict] = {
    "python": {
        "default_manager": "pip",
        "managers": ("pip", "uv", "poetry", "pipenv"),
    },
    "node": {
        "default_manager": "npm",
        "managers": ("npm", "pnpm", "yarn"),
    },
    "rust": {
        "default_manager": "cargo",
        "managers": ("cargo",),
    },
    "go": {
        "default_manager": "go",
        "managers": ("go",),
    },
}


def _ask_version(language: str) -> str:
    while True:
        version = input(f"  Version for {language}: ").strip()
        if not version:
            print("    Version is required.")
            continue
        if version.lower() == "latest":
            print('    Pin a concrete version — "latest" is not allowed.')
            continue
        return version


def _ask_manager(language: str) -> str | None:
    """Return the chosen manager, or None when it is the language default."""
    lang = SUPPORTED_LANGUAGES[language]
    managers = lang["managers"]
    default = lang["default_manager"]
    if len(managers) == 1:
        return None
    options = " / ".join(f"[{m}]" if m == default else m for m in managers)
    while True:
        choice = input(f"  Package manager for {language}? {options}: ").strip()
        if not choice:
            return None
        if choice in managers:
            return None if choice == default else choice
        print(f"    Unknown manager '{choice}' for {language}.")


def ask_languages() -> dict[str, dict]:
    """Ask which languages the project uses; collect version + manager for each."""
    supported = ", ".join(SUPPORTED_LANGUAGES)
    while True:
        print(f"What languages does this project use? (supported: {supported})")
        raw = input("Languages (comma-separated): ").strip()
        if not raw:
            print("  At least one language is required.")
            continue
        names = [p.strip().lower() for p in raw.split(",") if p.strip()]
        unknown = [n for n in names if n not in SUPPORTED_LANGUAGES]
        if unknown:
            print(f"  Unknown languages: {', '.join(unknown)}")
            continue
        break

    result: dict[str, dict] = {}
    for name in names:
        version = _ask_version(name)
        manager = _ask_manager(name)
        entry: dict = {"version": version}
        if manager is not None:
            entry["manager"] = manager
        result[name] = entry
    return result


def ask_os_packages() -> list[str]:
    """Optional list of apt-get packages. Accepts comma or whitespace separated."""
    raw = input("Any system packages needed? (e.g. libpq-dev ffmpeg; empty for none): ").strip()
    if not raw:
        return []
    return [p for p in raw.replace(",", " ").split() if p]


def ask_startup_commands() -> list[str]:
    """Commands to run after container start — one per line, empty line ends input."""
    print("Commands to run after container start (one per line, empty line to finish):")
    commands: list[str] = []
    while True:
        cmd = input("> ").strip()
        if not cmd:
            break
        commands.append(cmd)
    return commands


def ask_coding_environment() -> dict:
    """Collect the full coding-environment.toml structure interactively.

    Sections are returned in canonical TOML order (os, languages, startup);
    empty optional sections are omitted.
    """
    langs = ask_languages()
    os_pkgs = ask_os_packages()
    startup = ask_startup_commands()

    result: dict = {}
    if os_pkgs:
        result["os"] = {"packages": os_pkgs}
    if langs:
        result["languages"] = langs
    if startup:
        result["startup"] = {"commands": startup}
    return result


def _subsequent_run(project_dir: Path) -> int:
    print("Alcatrazer setup detected.")
    print("Subsequent-run logic is not yet implemented.")
    return 0
