"""Progress of a run: the current target and all targets together.

Engines, dumps and the run workflow report phases, bytes and files. The
`Progress` object turns that into percentages and remaining times and shows
them in one of three ways:

- `tty`:  two lines that update in place (an interactive terminal)
- `log`:  a normal line every 30 seconds (output to a file or pipe)
- `json`: `@@progress {…}` lines for the web interface (several per second)

The overall estimate is weighted by how long each target took last time;
a target without history counts like an average one.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from typing import IO, Any

from eichhorn import output as o

PREFIX = "@@progress "
BAR_CHARS = 18


@dataclass
class TargetPlan:
    name: str
    estimate_s: float | None = None  # duration of the last run
    share_dumps: float = 0.0  # parts of a run, from the last run's timings
    share_transfer: float = 0.9
    dump_estimates: dict[str, int] = field(default_factory=dict)  # raw bytes last time


def plan_from_manifest(name: str, manifest: dict[str, Any] | None, n_dumps: int) -> TargetPlan:
    if not manifest:
        return TargetPlan(name, None, 0.2 if n_dumps else 0.0, 0.75 if n_dumps else 0.9)
    t = manifest.get("timings", {})
    total = float(t.get("total") or 0)
    dumps_s = float(t.get("dumps") or 0)
    transfer_s = float(t.get("transfer") or 0)
    if total <= 0:
        return TargetPlan(name, None, 0.2 if n_dumps else 0.0, 0.75 if n_dumps else 0.9)
    est = {
        d["name"]: int(d.get("raw_bytes") or 0)
        for d in manifest.get("dumps", [])
        if d.get("status") == "ok"
    }
    return TargetPlan(name, total, dumps_s / total, transfer_s / total, est)


class Progress:
    def __init__(self, mode: str = "none", stream: IO[str] | None = None) -> None:
        self.mode = mode  # none | tty | log | json
        self.stream = stream or sys.stdout
        self.plans: list[TargetPlan] = []
        self.index = -1
        self.started_all = 0.0
        self.started_target = 0.0
        self.phase_name = ""
        self.detail = ""
        self.n_sources = 1
        self.n_dumps = 0
        self.part = 0  # number of the current source or dump (0-based)
        self.bytes_total: int | None = None
        self.bytes_done = 0
        self.files_total: int | None = None
        self.files_done = 0
        self.samples: deque[tuple[float, int]] = deque()
        self._shown = False
        self._last_render = 0.0
        self._last_log = 0.0
        self._done_targets: list[float] = []  # actual seconds of finished targets

    # ---------------------------------------------------------------- reporting
    def start_all(self, plans: list[TargetPlan]) -> None:
        self.plans = plans
        self.started_all = time.monotonic()

    def start_target(self, name: str, n_sources: int, n_dumps: int) -> None:
        self.index = next((i for i, p in enumerate(self.plans) if p.name == name), self.index + 1)
        self.started_target = time.monotonic()
        self.n_sources = max(1, n_sources)
        self.n_dumps = n_dumps
        self.phase("start", name)

    def phase(
        self,
        name: str,
        detail: str = "",
        part: int = 0,
        bytes_total: int | None = None,
        files_total: int | None = None,
    ) -> None:
        self.phase_name = name
        self.detail = detail
        self.part = part
        self.bytes_total = bytes_total
        self.bytes_done = 0
        self.files_total = files_total
        self.files_done = 0
        self.samples.clear()
        self._update(force=True)

    def advance(self, nbytes: int = 0, files: int = 0) -> None:
        self.bytes_done += nbytes
        self.files_done += files
        self._update()

    def set_done(self, nbytes: int) -> None:
        """For tools that report a running total (rsync)."""
        self.bytes_done = nbytes
        self._update()

    def end_target(self) -> None:
        self._done_targets.append(time.monotonic() - self.started_target)
        self.phase_name = "done"
        self._update(force=True)

    def clear(self) -> None:
        """Remove the bars before other output is printed (tty mode)."""
        if self.mode == "tty" and self._shown:
            self.stream.write("\r\x1b[2K\n\x1b[2K\x1b[1A\r")
            self.stream.flush()
            self._shown = False

    def finish(self) -> None:
        self.clear()

    # ---------------------------------------------------------------- numbers
    def speed(self) -> float | None:
        if len(self.samples) < 2:
            return None
        (t0, b0), (t1, b1) = self.samples[0], self.samples[-1]
        return (b1 - b0) / (t1 - t0) if t1 > t0 else None

    def fraction_target(self) -> float:
        plan = self.plans[self.index] if 0 <= self.index < len(self.plans) else TargetPlan("")
        d, t = plan.share_dumps, plan.share_transfer
        frac_part = (self.bytes_done / self.bytes_total) if self.bytes_total else 0.0
        frac_part = min(1.0, frac_part)
        if self.phase_name in ("start", "hooks"):
            return 0.0
        if self.phase_name == "dump":
            return d * (self.part + frac_part) / max(1, self.n_dumps)
        if self.phase_name in ("listing", "transfer"):
            within = frac_part if self.phase_name == "transfer" else 0.0
            return d + t * (self.part + within) / self.n_sources
        if self.phase_name == "done":
            return 1.0
        return min(0.99, d + t + (1 - d - t) * 0.5)  # finishing, copying, pruning

    def fraction_all(self) -> float | None:
        if not self.plans:
            return None
        known = [p.estimate_s for p in self.plans if p.estimate_s]
        default = sorted(known)[len(known) // 2] if known else 60.0
        weights = [p.estimate_s or default for p in self.plans]
        total = sum(weights)
        done = sum(weights[: max(0, self.index)])
        if 0 <= self.index < len(weights):
            done += weights[self.index] * self.fraction_target()
        return min(1.0, done / total) if total else None

    def eta_transfer(self) -> float | None:
        sp = self.speed()
        if self.phase_name != "transfer" or not sp or not self.bytes_total:
            return None
        return max(0.0, (self.bytes_total - self.bytes_done) / sp)

    def eta_all(self) -> float | None:
        frac = self.fraction_all()
        elapsed = time.monotonic() - self.started_all
        if not frac or frac < 0.02 or elapsed < 5:
            return None
        return max(0.0, elapsed / frac - elapsed)

    def snapshot(self) -> dict[str, Any]:
        name = self.plans[self.index].name if 0 <= self.index < len(self.plans) else ""
        return {
            "target": name,
            "index": self.index + 1,
            "count": len(self.plans),
            "phase": self.phase_name,
            "detail": self.detail,
            "bytes_done": self.bytes_done,
            "bytes_total": self.bytes_total,
            "files_done": self.files_done,
            "files_total": self.files_total,
            "speed": self.speed(),
            "pct_target": round(self.fraction_target() * 100, 1),
            "pct_all": None
            if self.fraction_all() is None
            else round((self.fraction_all() or 0) * 100, 1),
            "eta_target": self.eta_transfer(),
            "eta_all": self.eta_all(),
        }

    # ---------------------------------------------------------------- output
    def _update(self, force: bool = False) -> None:
        now = time.monotonic()
        self.samples.append((now, self.bytes_done))
        while len(self.samples) > 2 and now - self.samples[0][0] > 10:
            self.samples.popleft()
        if self.mode == "none":
            return
        if self.mode == "json":
            if force or now - self._last_render >= 0.25:
                self._last_render = now
                self.stream.write(PREFIX + json.dumps(self.snapshot()) + "\n")
                self.stream.flush()
            return
        if self.mode == "log":
            if (force and self.phase_name == "transfer") or now - self._last_log >= 30:
                self._last_log = now
                self.stream.write(self.text_line() + "\n")
                self.stream.flush()
            return
        if force or now - self._last_render >= 0.2:
            self._last_render = now
            self._render_tty()

    def text_line(self) -> str:
        s = self.snapshot()
        parts = [f"  [{s['index']}/{s['count']}] {s['target']}: {self._phase_text(s)}"]
        parts.append(f"{s['pct_target']:.0f} %")
        if s["pct_all"] is not None:
            parts.append(f"all {s['pct_all']:.0f} %")
        if s["eta_all"]:
            parts.append(f"~{o.duration(s['eta_all'])} left")
        return ", ".join(parts)

    def _phase_text(self, s: dict[str, Any]) -> str:
        phase = s["phase"]
        if phase == "transfer":
            text = f"copying {s['detail']}"
            if s["bytes_total"]:
                text += f" {o.size(s['bytes_done'])}/{o.size(s['bytes_total'])}"
            if s["speed"]:
                text += f" at {o.size(s['speed'])}/s"
            return text
        if phase == "dump":
            return f"dump {s['detail']} {o.size(s['bytes_done'])}"
        names = {
            "start": "starting",
            "hooks": f"hooks ({s['detail']})",
            "listing": f"listing {s['detail']}",
            "finish": "checksums and index",
            "copy": f"copying to {s['detail']}",
            "prune": "pruning",
            "done": "done",
        }
        return str(names.get(phase, phase))

    def _render_tty(self) -> None:
        s = self.snapshot()
        width = shutil.get_terminal_size((100, 20)).columns - 1

        def bar(pct: float | None) -> str:
            if pct is None:
                return " " * BAR_CHARS
            full = int(BAR_CHARS * pct / 100)
            return "█" * full + "░" * (BAR_CHARS - full)

        detail = self._phase_text(s)
        eta_t = f"  ~{o.duration(s['eta_target'])}" if s["eta_target"] else ""
        line1 = (
            f"{s['target'][:14]:<14} {bar(s['pct_target'])} {s['pct_target']:5.1f} %  "
            f"{detail}{eta_t}"
        )
        line2 = ""
        if s["count"] > 1 and s["pct_all"] is not None:
            eta = f"  ~{o.duration(s['eta_all'])} left" if s["eta_all"] else ""
            line2 = (
                f"{'all ' + str(s['index']) + '/' + str(s['count']):<14} "
                f"{bar(s['pct_all'])} {s['pct_all']:5.1f} %{eta}"
            )
        self.stream.write(
            "\r\x1b[2K" + line1[:width] + "\n\x1b[2K" + o.dim(line2[:width]) + "\x1b[1A\r"
        )
        self.stream.flush()
        self._shown = True


def choose_mode(json_output: bool, quiet: bool) -> str:
    import os

    if os.environ.get("EICHHORN_PROGRESS") == "json":
        return "json"
    if json_output or quiet:
        return "none"
    if sys.stdout.isatty() and os.environ.get("TERM") != "dumb":
        return "tty"
    return "log"


NULL = Progress("none")
_current: Progress = NULL


def use(p: Progress) -> None:
    """Make `p` the progress that engines and dumps report to."""
    global _current
    _current = p


def current() -> Progress:
    return _current


def dump_estimate(name: str) -> int | None:
    p = _current
    if 0 <= p.index < len(p.plans):
        return p.plans[p.index].dump_estimates.get(name) or None
    return None
