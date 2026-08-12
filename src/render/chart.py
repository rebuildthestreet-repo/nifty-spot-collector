"""
Render stored bars as a candlestick chart.

NEW CODE. There is no Python candlestick renderer anywhere in the source
system to extract from -- charting there is entirely React/TypeScript
(services/analytics/frontend), and Python's only role is serving JSON to it.
Confirmed by grep across both source trees for matplotlib/mplfinance/plotly:
zero hits in any .py file or requirements.txt.

This is the ONLY function the rest of the repo may import from this package.
Every other name here is a private implementation detail. That boundary is
deliberate: matplotlib is a trial-scoped choice, not a house standard, and
the owner has said the renderer will be replaced later. Swapping it for
lightweight-charts, Plotly, or a hand-written SVG writer should be a
one-file change -- rewrite this file, keep the signature, done.

    render_candles(bars, out_path, title) -> Path

`bars` is a list of dicts with keys: ts (str, "YYYY-MM-DD HH:MM:SS"), open,
high, low, close, volume (floats). This is exactly what
src.db.reader.SpotBarReader.get_spot_bars returns.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # no display needed -- must render identically headless, in a container, on any OS
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

UP_COLOR = "#26a69a"
DOWN_COLOR = "#ef5350"
WICK_WIDTH = 1.0
BODY_WIDTH = 0.7


def render_candles(bars: list[dict[str, Any]], out_path: "str | Path", title: str) -> Path:
    """Draw a candlestick chart of `bars` and write it to `out_path`.

    Bars are plotted at equally-spaced integer x-positions, not real
    timestamps -- a real time axis would draw a wide gap for every
    overnight/weekend break between trading sessions, which is standard
    practice for financial charts but would surprise anyone expecting a
    normal matplotlib date axis. X-axis labels are placed at each date
    boundary in the data instead.

    `out_path`'s extension controls the format: .svg for vector output, any
    matplotlib-supported raster extension (.png, etc.) otherwise. PNG output
    is written at 200 DPI, not matplotlib's 100 DPI default -- 100 DPI reads
    as soft once the image is embedded or scaled anywhere.

    Raises ValueError if `bars` is empty; there is nothing meaningful to
    draw, and a blank chart that LOOKS like a real result is a worse failure
    than an exception naming the empty range.
    """
    out_path = Path(out_path)
    if not bars:
        raise ValueError(
            "render_candles() got zero bars. Nothing to draw -- check the "
            "symbol/date range against what's actually stored (verify.sh "
            "reports row counts)."
        )

    fig, ax = plt.subplots(figsize=(14, 7))

    date_boundaries: list[tuple[int, str]] = []
    last_date = None
    y_min = min(bar["low"] for bar in bars)
    y_max = max(bar["high"] for bar in bars)
    for i, bar in enumerate(bars):
        date_part = str(bar["ts"])[:10]
        if date_part != last_date:
            date_boundaries.append((i, date_part))
            last_date = date_part

        open_, high, low, close = bar["open"], bar["high"], bar["low"], bar["close"]
        color = UP_COLOR if close >= open_ else DOWN_COLOR

        ax.add_line(plt.Line2D([i, i], [low, high], color=color, linewidth=WICK_WIDTH))
        body_bottom = min(open_, close)
        body_height = max(abs(close - open_), 1e-9)  # a doji (open==close) still needs a visible sliver
        ax.add_patch(
            Rectangle(
                (i - BODY_WIDTH / 2, body_bottom), BODY_WIDTH, body_height,
                facecolor=color, edgecolor=color,
            )
        )

    # add_line()/add_patch() -- unlike ax.plot()/ax.bar() -- do NOT register
    # with the Axes' autoscale, so without explicit limits here the figure
    # renders with matplotlib's default 0-1 axes and every candle silently
    # falls outside the visible area. Caught by actually looking at a
    # rendered preview, not just checking the function returned a path.
    ax.set_xlim(-1, len(bars))
    y_pad = (y_max - y_min) * 0.05 or 1.0
    ax.set_ylim(y_min - y_pad, y_max + y_pad)
    ax.set_xticks([i for i, _ in date_boundaries])
    ax.set_xticklabels([d for _, d in date_boundaries], rotation=45, ha="right")

    ax.set_title(title)
    ax.set_ylabel("Price")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    save_kwargs = {} if out_path.suffix.lower() == ".svg" else {"dpi": 200}
    fig.savefig(out_path, **save_kwargs)
    plt.close(fig)
    return out_path
