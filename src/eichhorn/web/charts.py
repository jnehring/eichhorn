"""Small server-rendered SVG charts (no JavaScript library needed)."""

from __future__ import annotations

from html import escape

from eichhorn.output import size as human_size

W, H, PAD_L, PAD_B, PAD_T = 640, 180, 64, 28, 12


def _fmt(v: float, unit: str) -> str:
    if unit == "bytes":
        return human_size(v)
    if unit == "s":
        return f"{v:.0f} s" if v >= 10 else f"{v:.1f} s"
    return f"{v:,.0f}"


def bars(
    labels: list[str], series: list[tuple[str, list[float], str]], unit: str = "", title: str = ""
) -> str:
    """Stacked bar chart. series: (name, values, css class)."""
    n = len(labels)
    if n == 0:
        return '<p class="muted">No data yet.</p>'
    totals = [sum(s[1][i] for s in series) for i in range(n)]
    top = max(totals) or 1.0
    plot_w = W - PAD_L - 8
    plot_h = H - PAD_B - PAD_T
    step = plot_w / n
    bw = max(2.0, min(36.0, step * 0.7))
    out = [
        f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="{escape(title)}">',
        f'<line class="axis" x1="{PAD_L}" y1="{H - PAD_B}" x2="{W - 8}" y2="{H - PAD_B}"/>',
    ]
    for frac in (0.5, 1.0):
        y = H - PAD_B - plot_h * frac
        out.append(f'<line class="grid" x1="{PAD_L}" y1="{y:.1f}" x2="{W - 8}" y2="{y:.1f}"/>')
        out.append(
            f'<text class="tick" x="{PAD_L - 6}" y="{y + 4:.1f}" text-anchor="end">'
            f"{escape(_fmt(top * frac, unit))}</text>"
        )
    for i, label in enumerate(labels):
        x = PAD_L + step * i + (step - bw) / 2
        y = H - PAD_B
        tip = [label] + [f"{name}: {_fmt(vals[i], unit)}" for name, vals, _ in series]
        out.append(f"<g><title>{escape(' · '.join(tip))}</title>")
        for _name, vals, css in series:
            h = plot_h * (vals[i] / top)
            if h <= 0:
                continue
            y -= h
            out.append(
                f'<rect class="{css}" x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" '
                f'height="{h:.1f}" rx="1.5"/>'
            )
        out.append("</g>")
    for i in (0, n - 1) if n > 1 else (0,):
        x = PAD_L + step * i + step / 2
        anchor = "start" if i == 0 and n > 1 else "end" if n > 1 else "middle"
        out.append(
            f'<text class="tick" x="{x:.1f}" y="{H - 8}" text-anchor="{anchor}">'
            f"{escape(labels[i][:10])}</text>"
        )
    out.append("</svg>")
    if len(series) > 1:
        legend = "".join(
            f'<span class="key"><i class="{css}"></i>{escape(name)}</span>'
            for name, _, css in series
        )
        out.append(f'<div class="legend">{legend}</div>')
    return "".join(out)


def meter(used: float, total: float) -> str:
    frac = used / total if total else 0.0
    return (
        f'<div class="meter" role="img" aria-label="{frac:.0%} used">'
        f'<div style="width:{frac * 100:.1f}%"></div></div>'
    )
