"""First-time setup for running on your own computer: writes a .env file.

    docker compose -f docker-compose.local.yml run --rm setup

Asks for the admin password twice, stores only its argon2 hash, generates a
random secret key and leaves the model and email settings empty.
"""

from __future__ import annotations

import getpass
import secrets
import sys
from pathlib import Path

from ducan.auth.security import hash_password

TEMPLATE = """# Created by ducan.setup_local. Keep this file private; never commit it.
SECRET_KEY={secret}
# Single quotes keep the "$" characters of the hash literal.
ADMIN_PASSWORD_HASH='{password_hash}'
DATABASE_URL=sqlite:///data/ducan.sqlite3
COOKIE_SECURE=false

# Optional, fill in later:
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=
ANTHROPIC_REFUSAL_FALLBACK=default
ANTHROPIC_EFFORT=low
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=
SMTP_STARTTLS=true
OWNER_EMAIL=
PUBLIC_BASE_URL=
CHAT_ALLOWED_ORIGINS=
"""


def write_env(path: Path, password: str) -> None:
    """Write a new .env; refuses to overwrite an existing one."""
    if path.exists():
        raise FileExistsError(f"{path} već postoji; ništa nije promijenjeno.")
    path.write_text(
        TEMPLATE.format(secret=secrets.token_urlsafe(48), password_hash=hash_password(password)),
        encoding="utf-8",
    )


def main() -> int:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else ".env")
    if target.exists():
        print(f"{target} već postoji. Ako želiš novu lozinku, obriši ga pa pokreni ponovno.")
        return 1
    print("Odaberi lozinku za ulaz u Dućan AI (najmanje 14 znakova).")
    password = getpass.getpass("Lozinka: ")
    if password != getpass.getpass("Ponovi lozinku: "):
        print("Lozinke se ne podudaraju.", file=sys.stderr)
        return 1
    try:
        write_env(target, password)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    print("Gotovo. Sljedeći korak: docker compose -f docker-compose.local.yml up -d --build")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
