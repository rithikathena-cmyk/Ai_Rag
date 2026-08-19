"""services/agents/report_agent.py::generate_report() — the stored file_path
must always be resolvable regardless of which process/cwd later reads it
back (routers/reports.py's download_report() does a bare
FileResponse(path=row.file_path, ...), which resolves a relative path
against whatever cwd *that* process happens to have — not necessarily the
one generate_report() ran under).

Live-verified gap this covers: three real report rows had a *relative*
file_path ("report_storage\\<id>.xlsx") stored, written under a process
launched from backend/ (cwd == backend/, so the relative path happened to
resolve). The backend was later restarted from the repo root instead (cwd ==
repo root), and every download of those reports 500'd with "File at path
report_storage\\<id>.xlsx does not exist" — the physical files were never
missing, only unresolvable from the new cwd. Storing str(path.resolve())
instead of str(path) makes the stored reference immune to this regardless
of what settings.report_dir is configured as (relative or absolute) at
generation time.
"""

from pathlib import Path

from app.core.config import settings
from app.services.agents import report_agent


class _FakeSession:
    def __init__(self):
        self.added: list = []

    def add(self, obj) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        pass

    def refresh(self, obj) -> None:
        pass


def test_generated_report_file_path_is_absolute(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "report_dir", str(tmp_path / "reports"))
    row = report_agent.generate_report(
        _FakeSession(), title="Test Report", fmt="csv", columns=["a"], rows=[["1"]],
    )
    assert Path(row.file_path).is_absolute()
    assert Path(row.file_path).exists()


def test_generated_report_file_path_resolves_even_with_a_relative_report_dir(tmp_path, monkeypatch):
    # Regression case for the exact gap found live: report_dir itself was
    # relative at generation time (an env var override, a different launch
    # cwd, ...) — the stored file_path must still come out absolute.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "report_dir", "relative_reports_dir")
    row = report_agent.generate_report(
        _FakeSession(), title="Test Report", fmt="csv", columns=["a"], rows=[["1"]],
    )
    assert Path(row.file_path).is_absolute()
    assert Path(row.file_path).exists()


def test_generated_report_file_path_readable_from_a_different_cwd(tmp_path, monkeypatch):
    # The actual failure mode: generate under one cwd, then read back the
    # stored path after the process cwd has changed to something else
    # entirely — simulating a later process (e.g. a restarted backend)
    # launched from a different working directory.
    reports_dir = tmp_path / "gen_here" / "report_storage"
    monkeypatch.setattr(settings, "report_dir", str(reports_dir))
    row = report_agent.generate_report(
        _FakeSession(), title="Test Report", fmt="csv", columns=["a"], rows=[["1"]],
    )

    elsewhere = tmp_path / "somewhere_else"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert Path(row.file_path).exists()
