"""First-run guard: SEC_USER_AGENT must be set to a real name and contact email. No network."""

import pytest

from harness import cli, edgar
from harness.config import REPO_ROOT, SEC_USER_AGENT_PLACEHOLDER, ConfigError, sec_user_agent


def _env_example_placeholder() -> str:
    for line in (REPO_ROOT / ".env.example").read_text().splitlines():
        if line.startswith("SEC_USER_AGENT="):
            return line.split("=", 1)[1].strip().strip('"')
    raise AssertionError("SEC_USER_AGENT missing from .env.example")


def test_placeholder_matches_env_example():
    assert SEC_USER_AGENT_PLACEHOLDER == _env_example_placeholder()


def test_unset_rejected(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(ConfigError, match="not set"):
        sec_user_agent()


@pytest.mark.parametrize("value", [
    f'"{SEC_USER_AGENT_PLACEHOLDER}"',   # as copied from .env.example, quotes included
    SEC_USER_AGENT_PLACEHOLDER,
    "Jane Doe jane@example.com",
])
def test_placeholder_rejected(monkeypatch, value):
    monkeypatch.setenv("SEC_USER_AGENT", value)
    with pytest.raises(ConfigError, match="placeholder"):
        sec_user_agent()


def test_value_without_email_rejected(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Jane Doe")
    with pytest.raises(ConfigError, match="no contact email"):
        sec_user_agent()


def test_valid_value_accepted(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", '"Jane Doe jane@yourdomain.com"')
    assert sec_user_agent() == "Jane Doe jane@yourdomain.com"


def test_run_mock_without_user_agent_exits_2_with_one_line(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: False)  # ignore a developer's .env
    monkeypatch.setattr(edgar, "DATA_DIR", tmp_path)                 # empty cache: first fetch must go out

    def no_network(*a, **k):
        raise AssertionError("network call made before the SEC_USER_AGENT check")

    monkeypatch.setattr(edgar.httpx, "get", no_network)

    rc = cli.main(["run", "--mock"])

    err = capsys.readouterr().err
    assert rc == 2
    assert err.startswith("error: SEC_USER_AGENT is not set.")
    assert len(err.strip().splitlines()) == 1
    assert "Traceback" not in err
