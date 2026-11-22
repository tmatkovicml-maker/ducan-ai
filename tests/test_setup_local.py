"""Local setup writes a usable .env and never overwrites one."""

from __future__ import annotations

import pytest
from dotenv import dotenv_values

from ducan.auth.security import verify_password
from ducan.config import EnvSettings
from ducan.setup_local import write_env


def test_write_env_roundtrip(tmp_path):
    path = tmp_path / ".env"
    write_env(path, "dugacka-lozinka-za-test")
    values = dotenv_values(path)
    assert values["ADMIN_PASSWORD_HASH"].startswith("$argon2")
    assert verify_password("dugacka-lozinka-za-test", values["ADMIN_PASSWORD_HASH"])
    env = EnvSettings(_env_file=str(path))  # type: ignore[call-arg]
    assert env.cookie_secure is False and len(env.secret_key) >= 32
    with pytest.raises(FileExistsError):
        write_env(path, "dugacka-lozinka-za-test")


def test_short_password_refused(tmp_path):
    with pytest.raises(ValueError):
        write_env(tmp_path / ".env", "kratka")
