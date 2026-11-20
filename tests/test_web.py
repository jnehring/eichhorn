"""Milestone 6: the web interface and `eichhorn stats`."""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from eichhorn.stores.localfs import LocalRepository
from eichhorn.web.app import create_app

from .conftest import touch_later

Cli = Callable[..., int]
TOKEN = "test-token"
CSRF = hmac.new(TOKEN.encode(), b"csrf", hashlib.sha256).hexdigest()


@pytest.fixture
def site(tmp_path: Path, cli: Cli) -> tuple[TestClient, str]:
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_text("alpha")
    (src / "sub" / "b.txt").write_text("beta")
    repo = tmp_path / "drive" / "eichhorn"
    assert cli("repo", "init", str(repo), "--name", "A", "--storage", "sparse") == 0
    t = tmp_path / "cfg" / "targets"
    t.mkdir(parents=True)
    (t / "docs.yaml").write_text(
        f"description: My docs\nsources:\n  - name: docs\n    path: '{src}'\n"
    )
    assert cli("run", "docs") == 0
    time.sleep(1.05)
    touch_later(src / "a.txt", "alpha 2")
    assert cli("run", "docs", "--label", "second") == 0
    client = TestClient(create_app(tmp_path / "cfg", TOKEN, {"testserver"}))
    r = client.get(f"/?token={TOKEN}", follow_redirects=False)
    assert r.status_code == 303 and "eichhorn_token" in r.headers["set-cookie"]
    snap = LocalRepository.open(repo).snapshots("docs")[-1]
    return client, snap


def test_needs_token_and_known_host(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "cfg", TOKEN, {"testserver"}))
    r = client.get("/")
    assert r.status_code == 401 and "eichhorn serve" in r.text
    assert client.get("/?token=wrong").status_code == 401
    other = TestClient(create_app(tmp_path / "cfg", TOKEN, {"localhost:1"}))
    assert other.get(f"/?token={TOKEN}").status_code == 400  # DNS rebinding guard


def test_pages(site: tuple[TestClient, str]) -> None:
    client, snap = site
    r = client.get("/")
    assert r.status_code == 200 and "docs" in r.text and "My docs" in r.text
    assert "Content-Security-Policy" in r.headers
    r = client.get("/t/docs")
    assert r.status_code == 200 and "<svg" in r.text and "second" in r.text
    r = client.get(f"/t/docs/A/{snap}")
    assert r.status_code == 200 and "Time per phase" in r.text and "Largest files" in r.text
    assert client.get("/repos").status_code == 200
    r = client.get("/config")
    assert r.status_code == 200 and "My docs" in r.text
    first = client.get("/t/docs").text
    assert "Diff" in first


def test_browse_point_in_time(site: tuple[TestClient, str]) -> None:
    client, snap = site
    r = client.get(f"/t/docs/A/{snap}/browse?path=files/docs")
    assert r.status_code == 200
    assert "a.txt" in r.text and "sub" in r.text
    r = client.get(f"/t/docs/A/{snap}/browse?path=files/docs/sub")
    assert "b.txt" in r.text and "stored in" in r.text  # sparse: content in the first snapshot
    r = client.get(f"/t/docs/A/{snap}/browse?q=b.tx")
    assert "files/docs/sub/b.txt" in r.text
    r = client.get(f"/t/docs/A/{snap}/download?p=files/docs/sub/b.txt")
    assert r.status_code == 200 and r.content == b"beta"
    r = client.get(f"/t/docs/A/{snap}/download?p=files/docs")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(z.namelist()) == ["docs/a.txt", "docs/sub/b.txt"]
    assert z.read("docs/a.txt") == b"alpha 2"
    r = client.get(f"/t/docs/A/{snap}/versions?p=files/docs/a.txt")
    assert r.text.count("download") >= 2
    r = client.get("/t/docs/A/at?date=2000-01-01", follow_redirects=False)
    assert r.status_code == 303 and "/browse" in r.headers["location"]
    assert client.get(f"/t/docs/A/{snap}/browse?path=files/nope").status_code == 404


def test_diff_page(site: tuple[TestClient, str]) -> None:
    client, snap = site
    first = client.get("/t/docs").text.split("<option selected>")[1].split("<")[0]
    r = client.get(f"/t/docs/A/diff?a={first}&b={snap}")
    assert r.status_code == 200 and "files/docs/a.txt" in r.text


def test_pin_and_label_need_csrf(site: tuple[TestClient, str]) -> None:
    client, snap = site
    r = client.post(f"/t/docs/A/{snap}/pin", data={"pinned": "1"})
    assert r.status_code == 403
    r = client.post(
        f"/t/docs/A/{snap}/pin", data={"pinned": "1", "csrf": CSRF}, follow_redirects=False
    )
    assert r.status_code == 303
    r = client.post(
        f"/t/docs/A/{snap}/label", data={"label": "renamed", "csrf": CSRF}, follow_redirects=False
    )
    text = client.get("/t/docs").text
    assert "📌 unpin" in text and 'value="renamed"' in text


def test_job_runs_backup_with_live_log(site: tuple[TestClient, str]) -> None:
    client, _ = site
    r = client.post(
        "/jobs/run",
        data={"target": "docs", "csrf": CSRF, "label": "from web"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    job_id = r.headers["location"].rsplit("/", 1)[-1]
    jobs = client.app.state.jobs  # type: ignore[attr-defined]
    for _ in range(300):
        if not jobs.get(job_id).running:
            break
        time.sleep(0.1)
    job = jobs.get(job_id)
    assert job.state == "ok", "\n".join(job.lines)
    events = client.get(f"/jobs/{job_id}/events").text
    assert "docs → A" in events and "event: done" in events and "data: ok" in events
    assert "from web" in client.get("/t/docs").text
    assert client.get(f"/jobs/{job_id}").status_code == 200


def test_stats_cli(
    site: tuple[TestClient, str], cli: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    _, snap = site
    capsys.readouterr()
    assert cli("--json", "stats") == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["snapshots"] == {"docs": 2}
    assert cli("--json", "stats", "docs") == 0
    assert len(json.loads(capsys.readouterr().out)) == 2
    assert cli("--json", "stats", "docs", snap) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["files"] == 2 and st["label"] == "second"
    assert cli("stats", "docs", "latest") == 0
