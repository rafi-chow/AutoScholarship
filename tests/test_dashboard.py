from streamlit.testing.v1 import AppTest


def test_dashboard_import_weekly_and_export_controls(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCHOLARSHIP_DB_PATH", str(tmp_path / "dashboard.db"))

    app = AppTest.from_file("src/dashboard.py", default_timeout=10).run()

    assert len(app.exception) == 0
    button_labels = {button.label for button in app.button}
    assert {
        "Extract and import",
        "Fetch, extract, and import",
        "Parse, rank, and import",
        "Export CSV tracker",
        "Export weekly action list",
        "Run Autopilot Now",
    }.issubset(button_labels)
    assert any("Top 5 high-fit scholarships" in markdown.value for markdown in app.markdown)
