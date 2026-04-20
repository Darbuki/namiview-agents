"""Import + CLI smoke tests.

The CLI no longer prints "scaffolding" — it now requires env vars and
real clients. We exercise argparse wiring and env-var checks without
actually talking to Kubernetes or the cluster.
"""

from __future__ import annotations

import pytest


def test_import() -> None:
    import namiview_triage

    assert namiview_triage.__name__ == "namiview_triage"


def test_cli_exits_with_2_when_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from namiview_triage.__main__ import main

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        main(["investigate the thing"])
    assert exc_info.value.code == 2


def test_cli_argparse_help_does_not_crash(capsys) -> None:
    from namiview_triage.__main__ import main

    with pytest.raises(SystemExit):
        main(["--help"])
    captured = capsys.readouterr()
    assert "namiview-triage" in captured.out
