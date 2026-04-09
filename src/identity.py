"""
Random agent identity generation.

Generates realistic-looking human names and email addresses for use as
the git identity inside the Alcatraz workspace. Agents see this identity
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
