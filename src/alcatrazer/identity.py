"""
Random agent identity and workspace directory name generation.

Generates realistic-looking human names, email addresses, and generic
directory names for use inside the Alcatraz workspace. Agents see these
instead of anything that hints at Alcatrazer.
"""

import random
from pathlib import Path

FIRST_NAMES = [
    "James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael",
    "Linda", "David", "Elizabeth", "William", "Barbara", "Richard", "Susan",
    "Joseph", "Jessica", "Thomas", "Sarah", "Christopher", "Karen",
    "Charles", "Lisa", "Daniel", "Nancy", "Matthew", "Betty", "Anthony",
    "Margaret", "Mark", "Sandra", "Donald", "Ashley", "Steven", "Kimberly",
    "Paul", "Emily", "Andrew", "Donna", "Joshua", "Michelle", "Kenneth",
    "Carol", "Kevin", "Amanda", "Brian", "Dorothy", "George", "Melissa",
    "Timothy", "Deborah",
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
    "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
    "Lee", "Perez", "Thompson", "White", "Harris", "Sanchez", "Clark",
    "Ramirez", "Lewis", "Robinson", "Walker", "Young", "Allen", "King",
    "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores", "Green",
    "Adams", "Nelson", "Baker", "Hall", "Rivera", "Campbell", "Mitchell",
    "Carter", "Roberts",
]

EMAIL_DOMAINS = [
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com",
    "protonmail.com", "aol.com", "mail.com", "zoho.com", "fastmail.com",
    "yandex.com", "gmx.com", "tutanota.com", "live.com", "msn.com",
    "me.com", "inbox.com", "pm.me", "hey.com", "duck.com",
]


def generate_identity(seed: int | None = None) -> tuple[str, str]:
    """Generate a random (name, email) tuple.

    If seed is provided, the result is deterministic (for testing).
    """
    rng = random.Random(seed)

    first = rng.choice(FIRST_NAMES)
    last = rng.choice(LAST_NAMES)
    name = f"{first} {last}"

    domain = rng.choice(EMAIL_DOMAINS)
    digits_count = rng.randint(0, 2)
    digits = "".join(str(rng.randint(0, 9)) for _ in range(digits_count))

    pattern = rng.choice(["full", "initial", "initial_underscore"])
    if pattern == "full":
        local = f"{first.lower()}.{last.lower()}{digits}"
    elif pattern == "initial":
        local = f"{first[0].lower()}{last.lower()}{digits}"
    else:
        local = f"{first[0].lower()}_{last.lower()}{digits}"

    email = f"{local}@{domain}"
    return name, email


def store_identity(alcatraz_dir: str, name: str, email: str) -> None:
    """Write agent identity to alcatraz_dir/agent-identity."""
    path = Path(alcatraz_dir) / "agent-identity"
    path.write_text(f"{name}\n{email}\n")


def load_identity(alcatraz_dir: str) -> tuple[str, str] | None:
    """Read agent identity from alcatraz_dir/agent-identity.

    Returns (name, email) or None if the file doesn't exist.
    """
    path = Path(alcatraz_dir) / "agent-identity"
    if not path.exists():
        return None
    lines = path.read_text().strip().split("\n")
    return lines[0], lines[1]


def ensure_identity(alcatraz_dir: str) -> tuple[str, str]:
    """Return existing identity or generate and store a new one."""
    existing = load_identity(alcatraz_dir)
    if existing is not None:
        return existing
    name, email = generate_identity()
    store_identity(alcatraz_dir, name, email)
    return name, email


# ── Workspace directory name generation ──────────────────────────────

WORKSPACE_WORDS = [
    "devspace", "codework", "project", "sandbox", "buildenv", "workspace",
    "codebase", "devenv", "runspace", "toolbox", "devkit", "workbench",
    "codelab", "devbox", "buildkit", "taskenv", "codespace", "devroot",
    "workdir", "appenv", "runtime", "buildbox", "coderun", "devwork",
    "taskbox", "appspace", "runkit", "codedev", "workenv", "devtask",
]


def generate_workspace_dir_name(seed: int | None = None) -> str:
    """Generate a random hidden directory name: .{word}-{4hex}."""
    rng = random.Random(seed)
    word = rng.choice(WORKSPACE_WORDS)
    hex4 = f"{rng.randint(0, 0xFFFF):04x}"
    return f".{word}-{hex4}"


def generate_workspace_choices(repo_root: str) -> list[str]:
    """Generate 3 unique workspace directory name choices.

    Skips names that collide with existing directories in repo_root.
    """
    root = Path(repo_root)
    choices = set()
    while len(choices) < 3:
        name = generate_workspace_dir_name()
        if name not in choices and not (root / name).exists():
            choices.add(name)
    return sorted(choices)


def store_workspace_dir(alcatraz_dir: str, dirname: str) -> None:
    """Write workspace directory name to alcatraz_dir/workspace-dir."""
    path = Path(alcatraz_dir) / "workspace-dir"
    path.write_text(f"{dirname}\n")


def load_workspace_dir(alcatraz_dir: str) -> str | None:
    """Read workspace directory name from alcatraz_dir/workspace-dir.

    Returns the directory name or None if the file doesn't exist.
    """
    path = Path(alcatraz_dir) / "workspace-dir"
    if not path.exists():
        return None
    return path.read_text().strip()


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <alcatrazer-dir>", file=sys.stderr)
        sys.exit(1)
    name, email = ensure_identity(sys.argv[1])
    print(name)
    print(email)
