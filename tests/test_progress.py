"""Progress bars: the computation, the three output modes, rsync parsing, CLI and web."""

from __future__ import annotations

import io
import json
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from eichhorn.progress import PREFIX, Progress, TargetPlan, plan_from_manifest

Cli = Callable[..., int]


def test_fractions_follow_the_phases() -> None:
    p = Progress("none")
    p.start_all([TargetPlan("a", 100, 0.2, 0.7), TargetPlan("b", 300, 0.0, 0.9)])
    p.start_target("a", n_sources=2, n_dumps=1)
    assert p.fraction_target() == 0.0
    p.phase("dump", "db", part=0, bytes_total=1000)
    p.advance(500)
    assert p.fraction_target() == pytest.approx(0.1)  # half of the dump share
    p.phase("transfer", "files", part=1, bytes_total=200)
    p.advance(100)
    assert p.fraction_target() == pytest.approx(0.2 + 0.7 * 1.5 / 2)
    p.end_target()
    assert p.fraction_all() == pytest.approx(100 / 400)  # weighted by last durations
    p.start_target("b", 1, 0)
    p.phase("transfer", "x", bytes_total=10)
    p.advance(10)
    assert p.fraction_all() == pytest.approx((100 + 300 * 0.9) / 400)


def test_targets_without_history_count_as_average() -> None:
    p = Progress("none")
    p.start_all([TargetPlan("a", 100), TargetPlan("b", None), TargetPlan("c", 300)])
    p.start_target("b", 1, 0)
    p.end_target()
    assert p.fraction_all() == pytest.approx((100 + 300) / (100 + 300 + 300))


def test_plan_from_manifest() -> None:
    m = {
        "timings": {"total": 100, "dumps": 30, "transfer": 60},
        "dumps": [{"name": "db", "status": "ok", "raw_bytes": 5000}],
    }
    plan = plan_from_manifest("t", m, 1)
    assert (plan.estimate_s, plan.share_dumps, plan.share_transfer) == (100, 0.3, 0.6)
    assert plan.dump_estimates == {"db": 5000}
    assert plan_from_manifest("t", None, 0).estimate_s is None


def test_output_modes() -> None:
    out = io.StringIO()
    p = Progress("json", out)
    p.start_all([TargetPlan("a", 10)])
    p.start_target("a", 1, 0)
    p.phase("transfer", "files", bytes_total=100)
    lines = [ln for ln in out.getvalue().splitlines() if ln.startswith(PREFIX)]
    report = json.loads(lines[-1][len(PREFIX) :])
    assert report["phase"] == "transfer" and report["bytes_total"] == 100

    out = io.StringIO()
    p = Progress("tty", out)
    p.start_all([TargetPlan("a", 10), TargetPlan("b", 10)])
    p.start_target("a", 1, 0)
    p.phase("transfer", "files", bytes_total=100)
    p._last_render = 0.0  # the display updates at most five times a second
    p.advance(50)
    text = out.getvalue()
    assert "█" in text and "\x1b[2K" in text and "all 1/2" in text
    p.clear()
    assert out.getvalue().endswith("\x1b[1A\r")

    out = io.StringIO()
    p = Progress("log", out)
    p.start_all([TargetPlan("a", 10)])
    p.start_target("a", 1, 0)
    p.phase("transfer", "files", bytes_total=100)  # a log line when copying starts
    assert "[1/1] a: copying files" in out.getvalue()


def test_rsync_progress_lines_are_read() -> None:
    from eichhorn.engines.rsync import _read_with_progress

    data = (
        b"         32,768   0%    0.00kB/s    0:00:00\r"
        b"      1,048,576  50%    1.00MB/s    0:00:01\r"
        b"      2,097,152 100%    2.00MB/s    0:00:01 (xfr#2, to-chk=0/3)\n"
        b"\nTotal bytes received: 2,100,000\n"
    )
    p = Progress("none")
    p.phase("transfer", "x", bytes_total=2_097_152)
    text = _read_with_progress(io.BytesIO(data), p)
    assert p.bytes_done == 2_097_152
    assert "Total bytes received" in text


def test_run_reports_progress(
    tmp_path: Path, cli: Cli, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.bin").write_bytes(b"x" * 3000)
    (src / "b.bin").write_bytes(b"y" * 2000)
    assert cli("repo", "init", str(tmp_path / "drive" / "eichhorn"), "--name", "A") == 0
    t = tmp_path / "cfg" / "targets"
    t.mkdir(parents=True)
    (t / "docs.yaml").write_text(f"sources:\n  - name: docs\n    path: '{src}'\n")
    monkeypatch.setenv("EICHHORN_PROGRESS", "json")
    capsys.readouterr()
    assert cli("run", "docs") == 0
    reports = [
        json.loads(ln[len(PREFIX) :])
        for ln in capsys.readouterr().out.splitlines()
        if ln.startswith(PREFIX)
    ]
    phases = [r["phase"] for r in reports]
    assert "listing" in phases and "transfer" in phases and phases[-1] == "done"
    transfer = [r for r in reports if r["phase"] == "transfer"]
    assert transfer[0]["bytes_total"] == 5000 and transfer[0]["files_total"] == 2
    assert reports[-1]["pct_target"] == 100.0 and reports[-1]["pct_all"] == 100.0


def test_web_job_sends_progress_events(tmp_path: Path, cli: Cli) -> None:
    pytest.importorskip("fastapi")
    import hashlib
    import hmac

    from fastapi.testclient import TestClient

    from eichhorn.web.app import create_app

    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("alpha")
    assert cli("repo", "init", str(tmp_path / "drive" / "eichhorn"), "--name", "A") == 0
    t = tmp_path / "cfg" / "targets"
    t.mkdir(parents=True)
    (t / "docs.yaml").write_text(f"sources:\n  - name: docs\n    path: '{src}'\n")
    client = TestClient(create_app(tmp_path / "cfg", "tok", {"testserver"}))
    client.get("/?token=tok")
    csrf = hmac.new(b"tok", b"csrf", hashlib.sha256).hexdigest()
    r = client.post("/jobs/run", data={"target": "docs", "csrf": csrf}, follow_redirects=False)
    job_id = r.headers["location"].rsplit("/", 1)[-1]
    jobs = client.app.state.jobs  # type: ignore[attr-defined]
    for _ in range(300):
        if not jobs.get(job_id).running:
            break
        time.sleep(0.1)
    job = jobs.get(job_id)
    assert job.state == "ok", "\n".join(job.lines)
    assert not any(line.startswith(PREFIX) for line in job.lines)  # not shown in the log
    assert job.progress and job.progress["phase"] == "done"
    events = client.get(f"/jobs/{job_id}/events").text
    assert "event: progress" in events
    assert "data-progress" in client.get(f"/jobs/{job_id}").text
