"""CLI: prompt for a password and print its argon2 hash for ADMIN_PASSWORD_HASH."""

from __future__ import annotations

import getpass
import sys

from ducan.auth.security import hash_password


def main() -> int:
    password = getpass.getpass("Nova lozinka: ")
    if password != getpass.getpass("Ponovi lozinku: "):
        print("Lozinke se ne podudaraju.", file=sys.stderr)
        return 1
    try:
        print(hash_password(password))
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
