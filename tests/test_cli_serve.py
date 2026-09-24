"""Tests for the `serve` CLI command (see CONTEXT.md's Web Upload)."""

from typer.testing import CliRunner

from receipt_radar.cli import app

runner = CliRunner()


def test_serve_reexports_before_starting_the_server(tmp_path, monkeypatch):
    calls = []

    def fake_export(receipts, web_dir):
        calls.append(("export", web_dir))

    def fake_run(fastapi_app, host, port):
        calls.append(("run", host, port))

    monkeypatch.setattr("receipt_radar.cli.export_mod.export_web_data", fake_export)
    monkeypatch.setattr("receipt_radar.cli.uvicorn.run", fake_run)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    result = runner.invoke(
        app,
        [
            "serve",
            "--web-dir", str(tmp_path / "web"),
            "--host", "0.0.0.0",
            "--port", "9000",
        ],
    )

    assert result.exit_code == 0, result.output
    assert [c[0] for c in calls] == ["export", "run"]
    assert calls[0][1] == tmp_path / "web"
    assert calls[1][1:] == ("0.0.0.0", 9000)
