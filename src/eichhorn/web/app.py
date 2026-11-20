"""The web interface (spec 9): FastAPI, server-rendered Jinja2 pages, a little JavaScript."""

from __future__ import annotations

import hashlib
import hmac
import json
import tempfile
import time
import zipfile
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

from eichhorn import __version__, repository
from eichhorn import notes as annotations_store
from eichhorn import output as o
from eichhorn.config import Config, load_config
from eichhorn.content import ContentMissing, Resolver
from eichhorn.errors import EichhornError
from eichhorn.history import diff, versions_of
from eichhorn.stats import repo_stats, snapshot_stats, target_series
from eichhorn.stores.localfs import LocalRepository
from eichhorn.summary import totals
from eichhorn.verify import last_report
from eichhorn.web import charts
from eichhorn.web.jobs import JobManager

HERE = Path(__file__).parent
COOKIE = "eichhorn_token"
TS = "%Y-%m-%dT%H-%M-%S"


def create_app(
    config_dir: Path | None, token: str, allowed_hosts: set[str] | None = None
) -> FastAPI:
    app = FastAPI(title="Eichhorn", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    env = templates.env
    env.filters["size"] = o.size
    env.filters["duration"] = lambda s: o.duration(float(s or 0))
    env.filters["age"] = o.age
    env.filters["snapdate"] = lambda s: f"{s[:10]} {s[11:].replace('-', ':')}"
    env.filters["urlq"] = lambda s: quote(str(s), safe="")
    env.filters["ns"] = lambda ns: (
        datetime.fromtimestamp(ns / 1e9).strftime("%Y-%m-%d %H:%M") if ns else ""
    )
    env.globals["version"] = __version__
    csrf_token = hmac.new(token.encode(), b"csrf", hashlib.sha256).hexdigest()
    jobs = JobManager(config_dir)
    cache: dict[tuple[str, str], tuple[tuple[str, ...], Resolver]] = {}

    # ---------------------------------------------------------------- helpers
    def cfg() -> Config:
        c, _ = load_config(config_dir, strict=False)
        return c

    def connected(c: Config) -> list[LocalRepository]:
        return repository.connected(c)

    def repo_by_name(c: Config, name: str) -> LocalRepository:
        for r in connected(c):
            if r.name == name:
                return r
        raise HTTPException(404, f"repository {name} is not connected")

    def resolver(c: Config, target: str, repo_name: str) -> Resolver:
        if target not in c.targets:
            # targets removed from the config can still be browsed on the drive
            pass
        repo = repo_by_name(c, repo_name)
        snaps = tuple(repo.snapshots(target))
        key = (str(repo.root), target)
        hit = cache.get(key)
        if hit and hit[0] == snaps:
            return hit[1]
        r = Resolver(repo, target)
        if not r.snapshots:
            raise HTTPException(404, f"{target} has no snapshots in {repo_name}")
        cache[key] = (snaps, r)
        return r

    def page(request: Request, name: str, **kw: Any) -> HTMLResponse:
        c = kw.pop("c", None) or cfg()
        return templates.TemplateResponse(
            request,
            name,
            {"csrf": csrf_token, "repos": connected(c), "cfg": c, "jobs": jobs.recent(5), **kw},
        )

    async def form(request: Request) -> dict[str, str]:
        data = await request.form()
        if not hmac.compare_digest(str(data.get("csrf", "")), csrf_token):
            raise HTTPException(403, "invalid form token; reload the page")
        return {k: str(v) for k, v in data.items()}

    # ---------------------------------------------------------------- security
    @app.middleware("http")
    async def guard(request: Request, call_next: Any) -> Response:
        host = request.headers.get("host", "")
        if allowed_hosts is not None and host not in allowed_hosts:
            return PlainTextResponse("unknown host", status_code=400)
        if request.url.path.startswith("/static/"):
            return await call_next(request)  # type: ignore[no-any-return]
        given = request.query_params.get("token")
        if given is not None and hmac.compare_digest(given, token):
            resp = RedirectResponse(url=request.url.path, status_code=303)
            resp.set_cookie(COOKIE, token, httponly=True, samesite="strict")
            return resp
        if not hmac.compare_digest(request.cookies.get(COOKIE, ""), token):
            return templates.TemplateResponse(request, "login.html", {}, status_code=401)
        response: Response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "script-src 'self'"
        )
        return response

    # ---------------------------------------------------------------- dashboard
    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        c = cfg()
        live = {ref.id: repository.locate(ref) for ref in c.repositories}
        cards = []
        for name, t in sorted(c.targets.items()):
            rows = []
            for ref in c.repositories:
                if t.repositories and ref.name not in t.repositories:
                    continue
                repo = live.get(ref.id)
                row: dict[str, Any] = {"repo": ref.name, "connected": repo is not None}
                if repo is not None:
                    latest = repo.latest(name)
                    if latest:
                        m = repo.manifest(name, latest)
                        row.update(
                            snapshot=latest,
                            status=m["status"],
                            finished=m["finished"],
                            size=totals(m)["bytes"],
                            count=len(repo.snapshots(name)),
                        )
                        rep = last_report(Resolver(repo, name))
                        if rep:
                            row["verify"] = "ok" if rep.get("ok") else "problems"
                    row["unfinished"] = bool(repo.incomplete(name))
                rows.append(row)
            cards.append({"name": name, "target": t, "rows": rows})
        stats = {r.name: (r.total_bytes() - r.free_bytes(), r.total_bytes()) for r in connected(c)}
        return page(request, "dashboard.html", c=c, cards=cards, disk=stats, meter=charts.meter)

    # ---------------------------------------------------------------- target
    @app.get("/t/{target}", response_class=HTMLResponse)
    def target_page(request: Request, target: str, repo: str | None = None) -> HTMLResponse:
        c = cfg()
        repos = [r for r in connected(c) if r.snapshots(target)]
        chosen = next((r for r in repos if r.name == repo), repos[0] if repos else None)
        series = target_series(chosen, target) if chosen else []
        notes = annotations_store.load(chosen, target) if chosen else {}
        rows = []
        for s in reversed(series):
            m = chosen.manifest(target, s["snapshot"]) if chosen else {}
            pinned, label, note = annotations_store.effective(m, notes)
            rows.append({**s, "pinned": pinned, "label": label, "note": note})
        labels = [s["snapshot"] for s in series]
        chart_size = charts.bars(
            labels,
            [("size", [s["logical_bytes"] for s in series], "c1")],
            "bytes",
            "Size of each snapshot",
        )
        chart_stored = charts.bars(
            labels,
            [("stored", [s["stored_bytes"] for s in series], "c2")],
            "bytes",
            "New data stored per run",
        )
        chart_changes = charts.bars(
            labels,
            [
                ("new", [s["new"] for s in series], "c-new"),
                ("changed", [s["changed"] for s in series], "c-changed"),
                ("deleted", [s["deleted"] for s in series], "c-deleted"),
            ],
            "",
            "Changed files per run",
        )
        chart_time = charts.bars(
            labels, [("duration", [s["duration_s"] for s in series], "c3")], "s", "Duration per run"
        )
        return page(
            request,
            "target.html",
            c=c,
            target=target,
            t=c.targets.get(target),
            repos_with=repos,
            chosen=chosen,
            rows=rows,
            chart_size=chart_size,
            chart_stored=chart_stored,
            chart_changes=chart_changes,
            chart_time=chart_time,
        )

    @app.get("/t/{target}/{repo}/diff", response_class=HTMLResponse)
    def diff_page(request: Request, target: str, repo: str, a: str, b: str) -> HTMLResponse:
        c = cfg()
        r = resolver(c, target, repo)
        d = diff(r, r.resolve_name(a), r.resolve_name(b))
        return page(request, "diff.html", c=c, target=target, repo=repo, d=d)

    @app.get("/t/{target}/{repo}/at")
    def at_date(target: str, repo: str, date: str) -> RedirectResponse:
        r = resolver(cfg(), target, repo)
        try:
            limit = datetime.strptime(date, "%Y-%m-%d").strftime("%Y-%m-%d") + "T23-59-59"
        except ValueError as exc:
            raise HTTPException(400, "date must be YYYY-MM-DD") from exc
        older = [s for s in r.snapshots if s <= limit]
        snap = older[-1] if older else r.snapshots[0]
        return RedirectResponse(f"/t/{quote(target)}/{quote(repo)}/{snap}/browse", 303)

    # ---------------------------------------------------------------- snapshot
    @app.get("/t/{target}/{repo}/{snap}", response_class=HTMLResponse)
    def snapshot_page(request: Request, target: str, repo: str, snap: str) -> HTMLResponse:
        c = cfg()
        r = resolver(c, target, repo)
        snap = r.resolve_name(snap)
        st = snapshot_stats(r, snap)
        timings = {k: v for k, v in st["timings"].items() if k != "total" and v}
        chart_time = charts.bars(
            list(timings), [("seconds", list(timings.values()), "c3")], "s", "Time per phase"
        )
        return page(
            request,
            "snapshot.html",
            c=c,
            target=target,
            repo=repo,
            snap=snap,
            st=st,
            chart_time=chart_time,
            snapshots=r.snapshots,
        )

    @app.get("/t/{target}/{repo}/{snap}/browse", response_class=HTMLResponse)
    def browse(
        request: Request, target: str, repo: str, snap: str, path: str = "files", q: str = ""
    ) -> HTMLResponse:
        c = cfg()
        r = resolver(c, target, repo)
        snap = r.resolve_name(snap)
        index = r.index(snap)
        path = path.strip("/") or "files"
        items = []
        if q:
            needle = q.lower()
            hits = [
                e
                for p, e in index.items()
                if e.type != "dir" and needle in p.rsplit("/", 1)[-1].lower()
            ]
            entries = sorted(hits, key=lambda e: e.p)[:300]
        else:
            if path not in index and not any(p.startswith(path + "/") for p in index):
                raise HTTPException(404, f"{path} is not in this snapshot")
            prefix = path + "/"
            entries = sorted(
                (
                    e
                    for p, e in index.items()
                    if p.startswith(prefix) and "/" not in p[len(prefix) :]
                ),
                key=lambda e: (e.type != "dir", e.p.lower()),
            )
            if path == "files" and "dumps" in index:
                entries.append(index["dumps"])
        for e in entries:
            where = None
            if e.type == "file" and not e.here:
                try:
                    where = r.holder(snap, e)[0]
                except ContentMissing:
                    where = "missing"
            items.append({"e": e, "name": e.p.rsplit("/", 1)[-1], "stored_in": where})
        crumbs = []
        acc = []
        for part in path.split("/"):
            acc.append(part)
            crumbs.append((part, "/".join(acc)))
        return page(
            request,
            "browse.html",
            c=c,
            target=target,
            repo=repo,
            snap=snap,
            path=path,
            items=items,
            crumbs=crumbs,
            q=q,
            snapshots=r.snapshots,
        )

    @app.get("/t/{target}/{repo}/{snap}/versions", response_class=HTMLResponse)
    def versions(request: Request, target: str, repo: str, snap: str, p: str) -> HTMLResponse:
        c = cfg()
        r = resolver(c, target, repo)
        return page(
            request,
            "versions.html",
            c=c,
            target=target,
            repo=repo,
            snap=snap,
            p=p,
            versions=versions_of(r, p),
        )

    @app.get("/t/{target}/{repo}/{snap}/download")
    def download(target: str, repo: str, snap: str, p: str) -> Response:
        r = resolver(cfg(), target, repo)
        snap = r.resolve_name(snap)
        index = r.index(snap)
        e = index.get(p)
        if e is None:
            raise HTTPException(404, "not in this snapshot")
        name = p.rsplit("/", 1)[-1]
        if e.type == "file":
            try:
                chunks: Iterator[bytes] = r.chunks(snap, e)
                r.locate(snap, e)
            except ContentMissing as exc:
                raise HTTPException(410, str(exc)) from exc
            return StreamingResponse(
                chunks,
                media_type="application/octet-stream",
                headers={
                    "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
                    "Content-Length": str(e.size),
                },
            )
        if e.type != "dir":
            raise HTTPException(400, "only files and folders can be downloaded")
        tmp = tempfile.NamedTemporaryFile(prefix="eichhorn-", suffix=".zip", delete=False)  # noqa: SIM115
        tmp.close()
        base = p.rsplit("/", 1)[0] if "/" in p else ""
        with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for q_, f in sorted(index.items()):
                if f.type != "file" or not q_.startswith(p + "/"):
                    continue
                arc = q_[len(base) + 1 :] if base else q_
                info = zipfile.ZipInfo(
                    arc,
                    time.localtime(f.mtime_ns / 1e9)[:6]
                    if f.mtime_ns > 315532800 * 10**9
                    else (1980, 1, 1, 0, 0, 0),
                )
                info.compress_type = zipfile.ZIP_DEFLATED
                with zf.open(info, "w", force_zip64=True) as out:
                    for chunk in r.chunks(snap, f):
                        out.write(chunk)
        return FileResponse(
            tmp.name,
            filename=f"{name}.zip",
            media_type="application/zip",
            background=BackgroundTask(Path(tmp.name).unlink, missing_ok=True),
        )

    @app.post("/t/{target}/{repo}/{snap}/pin")
    async def pin(request: Request, target: str, repo: str, snap: str) -> RedirectResponse:
        data = await form(request)
        r = resolver(cfg(), target, repo)
        snap = r.resolve_name(snap)
        annotations_store.set_pin(
            r.repo, target, snap, data.get("pinned") == "1", data.get("note") or None
        )
        return RedirectResponse(data.get("back") or f"/t/{quote(target)}", 303)

    @app.post("/t/{target}/{repo}/{snap}/label")
    async def label(request: Request, target: str, repo: str, snap: str) -> RedirectResponse:
        data = await form(request)
        r = resolver(cfg(), target, repo)
        snap = r.resolve_name(snap)
        annotations_store.set_label(r.repo, target, snap, data.get("label", "").strip())
        return RedirectResponse(data.get("back") or f"/t/{quote(target)}", 303)

    # ---------------------------------------------------------------- repositories, config
    @app.get("/repos", response_class=HTMLResponse)
    def repos_page(request: Request) -> HTMLResponse:
        c = cfg()
        rows = []
        for ref in c.repositories:
            repo = repository.locate(ref)
            if repo is None:
                rows.append({"name": ref.name, "connected": False})
                continue
            st = repo_stats(repo, c)
            used = st["used_by_targets"]
            chart = charts.bars(
                list(used), [("stored", list(used.values()), "c2")], "bytes", "Space per target"
            )
            rows.append(
                {
                    "name": ref.name,
                    "connected": True,
                    "st": st,
                    "chart": chart,
                    "meter": charts.meter(st["used_bytes"], st["total_bytes"]),
                }
            )
        return page(request, "repos.html", c=c, rows=rows)

    @app.get("/config", response_class=HTMLResponse)
    def config_page(request: Request) -> HTMLResponse:
        c = cfg()
        _, problems = load_config(config_dir, strict=False)
        from eichhorn.yamlload import dump

        items = []
        for name, t in sorted(c.targets.items()):
            items.append(
                {
                    "name": name,
                    "file": f"{t.file} (targets: {name})" if t.inline else t.file,
                    "text": dump(t.raw) if t.inline else t.file.read_text(encoding="utf-8"),
                    "expanded": dump({k: v for k, v in t.expanded.items() if v is not None})
                    if t.recipe
                    else None,
                }
            )
        return page(request, "config.html", c=c, items=items, problems=problems, config_dir=c.dir)

    # ---------------------------------------------------------------- jobs
    def start(title: str, args: list[str]) -> RedirectResponse:
        job = jobs.start(title, args)
        return RedirectResponse(f"/jobs/{job.id}", 303)

    @app.post("/jobs/run")
    async def job_run(request: Request) -> RedirectResponse:
        d = await form(request)
        args = ["run"]
        args += ["--all"] if d.get("target") == "__all__" else [d["target"]]
        if d.get("repo") == "__all__":
            args.append("--all-repos")
        elif d.get("repo"):
            args += ["--repo", d["repo"]]
        if d.get("label"):
            args += ["--label", d["label"]]
        if d.get("pin"):
            args.append("--pin")
        what = "all targets" if d.get("target") == "__all__" else d["target"]
        return start(f"Backup of {what}", args)

    @app.post("/jobs/verify")
    async def job_verify(request: Request) -> RedirectResponse:
        d = await form(request)
        args = ["verify", d["target"]]
        if d.get("repo"):
            args += ["--repo", d["repo"]]
        if d.get("all_snapshots"):
            args.append("--all-snapshots")
        if d.get("against_source"):
            args.append("--against-source")
        return start(f"Verify {d['target']}", args)

    @app.post("/jobs/sync")
    async def job_sync(request: Request) -> RedirectResponse:
        d = await form(request)
        args = ["repo", "sync", d["src"], d["dst"]]
        if d.get("both"):
            args.append("--both")
        return start(f"Sync {d['src']} → {d['dst']}", args)

    @app.post("/jobs/restore")
    async def job_restore(request: Request) -> RedirectResponse:
        d = await form(request)
        if not d.get("dest"):
            raise HTTPException(400, "choose a destination folder")
        args = ["restore", d["target"], d["snapshot"]]
        if d.get("path"):
            args.append(d["path"])
        args += ["--to", d["dest"], "--repo", d["repo"]]
        if d.get("force"):
            args.append("--force")
        return start(f"Restore {d['target']} {d['snapshot']}", args)

    @app.post("/jobs/doctor")
    async def job_doctor(request: Request) -> RedirectResponse:
        d = await form(request)
        return start(
            f"Doctor {d.get('target') or 'all'}",
            ["doctor", *([d["target"]] if d.get("target") else [])],
        )

    @app.post("/jobs/prune")
    async def job_prune(request: Request) -> RedirectResponse:
        d = await form(request)
        args = ["prune", d["target"]]
        if d.get("dry_run"):
            args.append("--dry-run")
        return start(f"Prune {d['target']}", args)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(request: Request, job_id: str) -> HTMLResponse:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job (the server may have been restarted)")
        return page(request, "job.html", job=job)

    @app.get("/jobs/{job_id}/events")
    def job_events(job_id: str) -> StreamingResponse:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job")

        def stream() -> Iterator[str]:
            sent = 0
            seen_progress = -1
            while True:
                with job.cond:
                    while (
                        sent >= len(job.lines)
                        and job.running
                        and job.progress_version == seen_progress
                    ):
                        job.cond.wait(timeout=15)
                        if (
                            sent >= len(job.lines)
                            and job.running
                            and job.progress_version == seen_progress
                        ):
                            break
                    new = job.lines[sent:]
                    done = not job.running
                    report = job.progress if job.progress_version != seen_progress else None
                    seen_progress = job.progress_version
                for line in new:
                    yield "data: " + line.replace("\r", "") + "\n\n"
                sent += len(new)
                if report is not None:
                    yield "event: progress\ndata: " + json.dumps(report) + "\n\n"
                if not new and report is None and not done:
                    yield ": keep-alive\n\n"
                if done and sent >= len(job.lines):
                    yield f"event: done\ndata: {job.state}\n\n"
                    return

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    @app.exception_handler(EichhornError)
    def eichhorn_error(request: Request, exc: EichhornError) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "error.html", {"message": str(exc)}, status_code=400
        )

    app.state.jobs = jobs
    return app
