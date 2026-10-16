"""Plotly chart builders for the dashboard (steering A-2/A-4/A-5).

All charts use the single ``fec_template`` and the metric series colours from the
active theme (``theme.metric(...)``) — never the status colours as series colours
(A-2). Status colours appear only as warning markers/shading and the status donut. Each builder returns a ``plotly.graph_objects``
Figure so it can be unit-tested without Streamlit. Hover text is formatted with
``fmt`` so numbers match the cards/tables (A-6).

Charts provided:
- ``forecast_band_chart``: actual vs predicted + P5–P95 band, y-axis zoomed to
  the data range with a "y축 0에서 시작하지 않음" caption responsibility on the
  caller; out-of-interval points marked (A-5 forecast).
- ``hourly_profile_chart``: 0–23시 average bill/carbon profile (replaces the
  flat daily bars, A-0).
- ``heatmap``: module × week anomaly-count heatmap, white -> anomaly monochrome scale.
- ``phase_sync_chart``: voltage/power-factor two-row shared-x chart with the
  config thresholds (210/85) drawn as reference lines (A-4).
- ``anomaly_vs_typical_bar``: per-hour anomaly count vs the typical mean.
- ``anomaly_points_chart``: fallback scatter of anomaly events when no 5s series
  exists in artifacts (A-5 anomaly fallback).
"""

from __future__ import annotations

from typing import Any

from config import config
from src.app.ui import fmt, theme

_TPL = theme.FEC_TEMPLATE_NAME


# Lighter client-side rendering: no modebar buttons / scroll-zoom handlers, which
# trims per-chart setup time in the browser on every page switch.
PLOTLY_CONFIG: dict[str, Any] = {
    "displayModeBar": False,
    "displaylogo": False,
    "scrollZoom": False,
    "responsive": True,
}


def _ensure_template() -> None:
    """Register ``fec_template`` on first chart use (idempotent)."""

    theme.register_template()


def _empty_fig(message: str):
    import plotly.graph_objects as go

    _ensure_template()
    fig = go.Figure()
    fig.update_layout(template=_TPL)
    fig.add_annotation(
        text=message,
        showarrow=False,
        font=dict(color=theme.COLOR["muted"], size=13),
        xref="paper",
        yref="paper",
        x=0.5,
        y=0.5,
    )
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return fig


def forecast_band_chart(
    rows: list[dict[str, Any]],
    *,
    actuals: dict[str, float] | None = None,
    interval_label: str = "예측구간 P5–P95 (잠정)",
    height: int = 360,
    metric: str = "pow",
    color: str | None = None,
    show_legend: bool = True,
):
    """Actual vs predicted power + provisional interval band (A-5 forecast).

    ``rows`` are forecast rows (id, pred_pow, pred_low, pred_high). The y-axis is
    zoomed to the data range (power varies by only hundreds, A-0); the caller
    adds the "y축 0에서 시작하지 않음" caption. Out-of-interval actual points are
    marked in alert red (the only status-colour use allowed in a chart — it is a
    warning marker, A-2).
    """

    import plotly.graph_objects as go

    if not rows:
        return _empty_fig(fmt.NO_DATA)

    _ensure_template()
    fmt_fn = {"pow": fmt.fmt_pow, "bill": fmt.fmt_bill, "carbon": fmt.fmt_carbon}.get(metric, fmt.fmt_pow)
    axis_title = {"pow": "전력량(kWh)", "bill": "요금(원)", "carbon": "탄소(kgCO₂)"}.get(metric, "전력량(kWh)")
    line_color = color or theme.metric("power")
    xs = [r["id"] for r in rows]
    pow_vals = [r["pred_pow"] for r in rows]
    lows = [r.get("pred_low") for r in rows]
    highs = [r.get("pred_high") for r in rows]
    has_band = all(v is not None for v in lows) and all(v is not None for v in highs)

    fig = go.Figure()
    if has_band:
        fig.add_trace(
            go.Scatter(
                x=xs, y=highs, name="상한", mode="lines",
                line=dict(width=0, color=line_color),
                hoverinfo="skip", showlegend=False,
            )
        )
        fig.add_trace(
            go.Scatter(
                x=xs, y=lows, name=interval_label, mode="lines",
                line=dict(width=0, color=line_color),
                fill="tonexty", fillcolor=theme.tint(line_color, 0.15),
                hoverinfo="skip",
            )
        )

    fig.add_trace(
        go.Scatter(
            x=xs, y=pow_vals, name="예측값", mode="lines",
            line=dict(color=line_color, width=2, dash="dot" if actuals else "solid"),
            customdata=[fmt.fmt_full(v, metric) for v in pow_vals],
            hovertemplate="%{x}<br>예측 %{customdata}<extra></extra>",
        )
    )

    actual_pairs = []
    out_x: list[str] = []
    out_y: list[float] = []
    if actuals:
        for row in rows:
            av = actuals.get(row["id"])
            if av is None:
                continue
            actual_pairs.append((row["id"], av))
            low = row.get("pred_low")
            high = row.get("pred_high")
            if low is not None and high is not None and (av < low or av > high):
                out_x.append(row["id"])
                out_y.append(av)
    if actual_pairs:
        fig.add_trace(
            go.Scatter(
                x=[p[0] for p in actual_pairs],
                y=[p[1] for p in actual_pairs],
                name="실제값", mode="lines",
                line=dict(color=theme.COLOR["actual"], width=1.8),
                customdata=[fmt_fn(p[1]) for p in actual_pairs],
                hovertemplate="%{x}<br>실제 %{customdata}<extra></extra>",
            )
        )
    if out_x:
        fig.add_trace(
            go.Scatter(
                x=out_x, y=out_y, name="구간 이탈", mode="markers",
                marker=dict(color=theme.COLOR["alert"], size=9, symbol="x"),
                hovertemplate="%{x}<br>구간 이탈 %{y:,.2f}<extra></extra>",
            )
        )

    # Zoom the y-axis to the data range (A-0): never start at 0.
    from src.app import page_logic

    all_vals = [v for v in pow_vals if v is not None] + [p[1] for p in actual_pairs]
    if has_band:
        all_vals += [v for v in lows if v is not None] + [v for v in highs if v is not None]
    rng = page_logic.zoom_range(all_vals)  # 값 변화폭의 ±20% → 작은 차이도 보이게
    if rng:
        fig.update_yaxes(range=list(rng))
    fig.update_yaxes(tickformat=TICK_FORMAT.get(metric, ",.2f"))

    fig.update_layout(
        template=_TPL,
        height=height,
        hovermode="x unified",
        xaxis=dict(title="시점"),
        yaxis=dict(title=axis_title),
        showlegend=show_legend,
    )
    return fig


def hourly_profile_chart(hour_rows: list[dict[str, Any]], *, height: int = 320, actual: list[float] | None = None):
    """0–23시 average bill/carbon profile line chart (A-0 replacement for bars).

    ``hour_rows`` = ``[{"hour": int, "bill": float, "carbon": float}, ...]``.
    Bill and carbon share the x (hour of day) with a secondary y-axis.
    """

    import plotly.graph_objects as go

    if not hour_rows:
        return _empty_fig(fmt.NO_DATA)

    _ensure_template()
    if "pow" in hour_rows[0]:
        # 요금·탄소는 전력량 × 고정 계수라 모양이 같다 → 전력량 하나로 예측 vs 실제 비교
        from src.app import page_logic

        hours = [r["hour"] for r in hour_rows]
        pred = [r["pow"] for r in hour_rows]
        fig = go.Figure()
        if actual:
            fig.add_trace(go.Scatter(
                x=hours, y=actual, name="실제(5월 평균)", mode="lines+markers",
                line=dict(color=theme.COLOR["actual"], width=2), marker=dict(size=5),
                customdata=[fmt.fmt_pow(v) for v in actual],
                hovertemplate="%{x}시<br>실제 %{customdata} kWh<extra></extra>",
            ))
        fig.add_trace(go.Scatter(
            x=hours, y=pred, name="예측(평균)", mode="lines+markers",
            line=dict(color=theme.COLOR["accent"], width=2), marker=dict(size=5),
            customdata=[fmt.fmt_pow(v) for v in pred],
            hovertemplate="%{x}시<br>예측 %{customdata} kWh<extra></extra>",
        ))
        rng = page_logic.zoom_range(pred + (actual or []))
        fig.update_layout(
            template=_TPL, height=height, hovermode="x unified",
            xaxis=dict(title="시(0~23)", dtick=2), yaxis=dict(title="시간당 전력량(kWh)", range=list(rng) if rng else None),
        )
        return fig
    hours = [r["hour"] for r in hour_rows]
    bills = [r["bill"] for r in hour_rows]
    carbons = [r["carbon"] for r in hour_rows]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=hours, y=bills, name="요금(시간대 평균)", mode="lines+markers",
            line=dict(color=theme.metric("bill"), width=2),
            customdata=[fmt.fmt_bill(v) for v in bills],
            hovertemplate="%{x}시<br>요금 %{customdata}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=hours, y=carbons, name="탄소(시간대 평균)", mode="lines+markers",
            line=dict(color=theme.metric("carbon"), width=2, dash="dot"),
            yaxis="y2",
            customdata=[fmt.fmt_carbon(v) for v in carbons],
            hovertemplate="%{x}시<br>탄소 %{customdata}<extra></extra>",
        )
    )
    fig.update_layout(
        template=_TPL,
        height=height,
        xaxis=dict(title="시(0~23)", dtick=2),
        yaxis=dict(title="요금(원)"),
        yaxis2=dict(title="탄소(kgCO₂)", overlaying="y", side="right", showgrid=False),
    )
    return fig


def _iso_week_label(day: str) -> str:
    """Return a 'YYYY-Www' ISO-week label for a 'YYYY-MM-DD' day string."""

    from datetime import date

    try:
        y, m, d = (int(x) for x in day[:10].split("-"))
        iso = date(y, m, d).isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    except (ValueError, IndexError):
        return day


def heatmap(
    modules: list[str],
    days: list[str],
    matrix: list[list[float]],
    *,
    height: int | None = None,
):
    """Module × week anomaly-count heatmap, white -> anomaly monochrome scale (A-4)."""

    import plotly.graph_objects as go

    if not modules or not days:
        return _empty_fig(fmt.NO_DATA)

    _ensure_template()

    # Aggregate day columns into ISO-week columns (x축 주차, 일 단위 금지).
    week_labels: list[str] = []
    week_index: dict[str, int] = {}
    for day in days:
        wk = _iso_week_label(day)
        if wk not in week_index:
            week_index[wk] = len(week_labels)
            week_labels.append(wk)
    weekly = [[0.0 for _ in week_labels] for _ in modules]
    for r, _mod in enumerate(modules):
        for c, day in enumerate(days):
            weekly[r][week_index[_iso_week_label(day)]] += matrix[r][c]

    fig = go.Figure(
        go.Heatmap(
            z=weekly,
            x=week_labels,
            y=modules,
            colorscale=theme.ANOMALY_COLORSCALE,
            hovertemplate="%{y}<br>%{x}<br>이상 %{z}건<extra></extra>",
            colorbar=dict(title="이상 건수"),
        )
    )
    auto_h = max(300, 26 * len(modules) + 120)
    fig.update_layout(
        template=_TPL,
        height=height or auto_h,
        xaxis=dict(title="주차"),
        yaxis=dict(title="설비"),
    )
    return fig


def _shade_below(fig, values: list[float], threshold: float, *, row: int) -> None:
    """Shade the region below a threshold with the alert tint (warning shading)."""

    lo = min(values + [threshold])
    pad = (max(values + [threshold]) - lo) * 0.1 or 1.0
    fig.add_hrect(
        y0=lo - pad, y1=threshold, fillcolor=theme.tint("alert"),
        line_width=0, layer="below", row=row, col=1,
    )


def phase_sync_chart(
    series: dict[str, list[dict[str, Any]]],
    *,
    voltage_threshold: float | None = None,
    power_factor_threshold: float | None = None,
    height: int = 420,
):
    """Two-row voltage / power-factor chart sharing x, with threshold lines (A-4).

    ``series`` = ``{"voltage": [{"x":..,"value":..}], "power_factor": [...]}``.
    Thresholds default to config (210 V / 85 %). Returns an empty figure when
    neither series has points.
    """

    from plotly.subplots import make_subplots

    v_thr = config.VOLTAGE_THRESHOLD if voltage_threshold is None else voltage_threshold
    pf_thr = (
        config.POWER_FACTOR_THRESHOLD
        if power_factor_threshold is None
        else power_factor_threshold
    )
    volt = series.get("voltage", []) if series else []
    pf = series.get("power_factor", []) if series else []
    if not volt and not pf:
        return _empty_fig(fmt.NO_DATA)

    _ensure_template()
    import plotly.graph_objects as go

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
        subplot_titles=("전압 (3상 판정 임계 210)", "역률 (3상 판정 임계 85)"),
    )
    if volt:
        fig.add_trace(
            go.Scatter(
                x=[p["x"] for p in volt], y=[p["value"] for p in volt],
                name="전압", mode="lines+markers",
                line=dict(color=theme.phase("R"), width=1.6),
            ),
            row=1, col=1,
        )
        _shade_below(fig, [p["value"] for p in volt], v_thr, row=1)
        fig.add_hline(
            y=v_thr, line=dict(color=theme.COLOR["muted"], dash="dash"),
            annotation_text=f"임계 {v_thr:g}", row=1, col=1,
        )
    if pf:
        fig.add_trace(
            go.Scatter(
                x=[p["x"] for p in pf], y=[p["value"] for p in pf],
                name="역률", mode="lines+markers",
                line=dict(color=theme.phase("S"), width=1.6),
            ),
            row=2, col=1,
        )
        _shade_below(fig, [p["value"] for p in pf], pf_thr, row=2)
        fig.add_hline(
            y=pf_thr, line=dict(color=theme.COLOR["muted"], dash="dash"),
            annotation_text=f"임계 {pf_thr:g}", row=2, col=1,
        )
    # Shared x on the bottom row; per-row y titles (전압 / 역률, 척도).
    fig.update_xaxes(title_text="시각", row=2, col=1)
    fig.update_yaxes(title_text="전압(척도)", row=1, col=1)
    fig.update_yaxes(title_text="역률(척도)", row=2, col=1)
    fig.update_layout(template=_TPL, height=height, hovermode="x unified")
    return fig


def anomaly_points_chart(events: list[dict[str, Any]], *, height: int = 340):
    """Fallback scatter of anomaly events when no 5s series exists (A-5)."""

    import plotly.graph_objects as go

    if not events:
        return _empty_fig(fmt.NO_DATA)

    _ensure_template()
    by_type: dict[str, dict[str, list]] = {}
    for ev in events:
        bucket = by_type.setdefault(ev["type"], {"x": [], "y": []})
        bucket["x"].append(ev["localtime"])
        bucket["y"].append(ev["value"])

    fig = go.Figure()
    palette = [theme.phase("R"), theme.phase("S"), theme.phase("T")]
    for i, (etype, pts) in enumerate(sorted(by_type.items())):
        fig.add_trace(
            go.Scatter(
                x=pts["x"], y=pts["y"], name=etype, mode="markers",
                marker=dict(color=palette[i % len(palette)], size=7, opacity=0.75),
            )
        )
    fig.update_layout(
        template=_TPL,
        height=height,
        xaxis=dict(title="시각"),
        yaxis=dict(title="값(척도)"),
    )
    return fig


def donut(segments: list[dict[str, Any]], *, height: int = 220):
    """Status-summary donut (go.Pie hole=0.68) — overview status summary (v2).

    ``segments`` = ``[{"label": str, "count": int}, ...]``. Uses status colours
    when the label is a known status (the one chart where status colour is the
    semantic, like the warning marker); unknown labels fall back to the accent.
    """

    import plotly.graph_objects as go

    if not segments:
        return _empty_fig(fmt.NO_DATA)

    _ensure_template()
    labels = [s["label"] for s in segments]
    values = [s["count"] for s in segments]
    colors = [theme.STATUS_COLOR.get(lbl, theme.COLOR["accent"]) for lbl in labels]
    fig = go.Figure(
        go.Pie(
            labels=labels,
            values=values,
            hole=0.68,
            marker=dict(colors=colors),
            textinfo="label+value",
            hovertemplate="%{label}<br>%{value}개<extra></extra>",
            sort=False,
        )
    )
    fig.update_layout(
        template=_TPL, height=height, showlegend=False,
        margin=dict(l=8, r=8, t=8, b=8),
    )
    return fig


def horizontal_share_bar(items: list[dict[str, Any]], *, height: int = 260, value_key: str = "value"):
    """Horizontal bar of per-module shares (overview 설비별 비중, v2).

    ``items`` = ``[{"label": str, value_key: float, "text": str}, ...]`` already
    sorted by the caller. Anomaly colour (A-2: no status colour in charts).
    """

    import plotly.graph_objects as go

    if not items:
        return _empty_fig(fmt.NO_DATA)

    _ensure_template()
    labels = [i["label"] for i in items]
    values = [i[value_key] for i in items]
    texts = [i.get("text", "") for i in items]
    fig = go.Figure(
        go.Bar(
            x=values,
            y=labels,
            orientation="h",
            marker_color=theme.metric("anomaly"),
            text=texts,
            textposition="outside",
            hovertemplate="%{y}<br>%{text}<extra></extra>",
        )
    )
    fig.update_layout(
        template=_TPL, height=height,
        margin=dict(l=8, r=24, t=8, b=36),
        yaxis=dict(autorange="reversed", showgrid=False, title="설비"),
        xaxis=dict(showgrid=True, title="이상 건수"),
    )
    return fig


def anomaly_vs_typical_bar(hours: list[int], counts: list[int], *, typical: float):
    """Per-hour anomaly count vs the typical mean reference line (A-5)."""

    import plotly.graph_objects as go

    if not hours:
        return _empty_fig(fmt.NO_DATA)

    _ensure_template()
    fig = go.Figure(
        go.Bar(
            x=hours, y=counts, name="시간당 이상 건수",
            marker_color=theme.metric("anomaly"),
            hovertemplate="%{x}시<br>이상 %{y}건<extra></extra>",
        )
    )
    fig.add_hline(
        y=typical, line=dict(color=theme.COLOR["muted"], dash="dash"),
        annotation_text=f"평소 평균 {typical:.1f}",
    )
    fig.update_layout(
        template=_TPL,
        height=300,
        xaxis=dict(title="시(0~23)", dtick=2),
        yaxis=dict(title="이상 건수"),
    )
    return fig


# ---------------------------------------------------------------------------
# v3 charts: period bars, per-module trend, module comparison, load heatmap,
# anomaly timeline
# ---------------------------------------------------------------------------
# y축 눈금: 요금은 원 단위 정수, 전력량·탄소는 소수 2자리 (169.04k 같은 SI 접두어 금지)
TICK_FORMAT = {"bill": ",.0f", "pow": ",.2f", "carbon": ",.2f"}


def period_bar_chart(buckets: list[dict[str, Any]], *, height: int = 220, color: str | None = None,
                     axis_title: str = "전력량(kWh)", fmt_fn=None, show_actual: bool = False,
                     tickformat: str = ",.2f"):
    """일/주 예측 전력량 점+선 차트 (이름은 호환을 위해 유지).

    일별 합계 차이가 작아 0부터 시작하는 막대는 모두 같아 보인다. 막대 y축을 자르면
    왜곡이므로 점+선으로 바꾸고 완전 구간 값만으로 y축을 확대한다. 부분 구간은
    범위 밖이면 그리지 않는다. 최대·최소 라벨과 평균선을 표시한다.
    """

    import plotly.graph_objects as go

    from src.app import page_logic

    full = [b for b in buckets if not b["partial"] and b.get("pred_pow") is not None]
    if not full:
        full = [b for b in buckets if b.get("pred_pow") is not None]
    if not full:
        return _empty_fig(fmt.NO_DATA)
    _ensure_template()
    zoom_vals = [b["pred_pow"] for b in full]
    act_full = [b for b in full if show_actual and b.get("actual_pow") is not None]
    zoom_vals += [b["actual_pow"] for b in act_full]
    rng = page_logic.zoom_range(zoom_vals)
    lo, hi = rng
    part = [b for b in buckets if b["partial"] and b not in full and b.get("pred_pow") is not None and lo <= b["pred_pow"] <= hi]

    ff = fmt_fn or fmt.fmt_pow

    def _cd(bs):
        return [[ff(b.get("pred_pow")), b["n"]] for b in bs]

    hover = "%{x}<br>예측 %{customdata[0]}<br>%{customdata[1]}시간 합계<extra></extra>"
    accent = color or theme.COLOR["accent"]
    ys = [b["pred_pow"] for b in full]
    mean = sum(ys) / len(ys)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=[b["label"] for b in full], y=ys, mode="lines+markers",
        line=dict(color=accent, width=2), marker=dict(size=5, color=accent),
        customdata=_cd(full), hovertemplate=hover, name="예측",
    ))
    if part:
        fig.add_trace(go.Scatter(
            x=[b["label"] for b in part], y=[b["pred_pow"] for b in part], mode="markers",
            marker=dict(size=8, color="rgba(0,0,0,0)", line=dict(color=theme.token("partial"), width=2)),
            customdata=_cd(part), hovertemplate=hover, name="부분 구간",
        ))
    if act_full:
        fig.add_trace(go.Scatter(
            x=[b["label"] for b in act_full], y=[b["actual_pow"] for b in act_full], mode="lines+markers",
            line=dict(color=theme.COLOR["muted"], width=2, dash="dash"), marker=dict(size=6, color=theme.COLOR["muted"]),
            customdata=[[ff(b["actual_pow"])] for b in act_full],
            hovertemplate="%{x}<br>실제 %{customdata[0]}<extra></extra>", name="실제",
        ))
    fig.add_hline(
        y=mean, line=dict(color=theme.COLOR["muted"], width=1, dash="dot"),
        annotation_text=f"평균 {ff(mean)}", annotation_position="top left",
        annotation_font=dict(size=11, color=theme.COLOR["muted"]),
    )
    imax = max(range(len(full)), key=lambda k: ys[k])
    imin = min(range(len(full)), key=lambda k: ys[k])
    for k, pos in ((imax, "top center"), (imin, "bottom center")):
        fig.add_trace(go.Scatter(
            x=[full[k]["label"]], y=[ys[k]], mode="text", text=[ff(ys[k])],
            textposition=pos, textfont=dict(size=11, color=theme.COLOR["text"]),
            hoverinfo="skip", showlegend=False,
        ))
    order = [b["label"] for b in sorted(full + part, key=lambda b: b["key"])]
    fig.update_layout(
        template=_TPL, height=height, showlegend=bool(act_full),
        yaxis=dict(title=axis_title, range=[lo, hi], tickformat=tickformat),
        xaxis=dict(title="", type="category", categoryorder="array", categoryarray=order),
    )
    return fig


def period_total_bar(buckets: list[dict[str, Any]], *, height: int = 220, color: str | None = None,
                     axis_title: str = "전력량(kWh)", fmt_fn=None, show_actual: bool = False,
                     tickformat: str = ",.2f"):
    """구간이 1~2개뿐일 때(월 단위 등) 합계 막대. 0부터 시작하므로 축 확대 없음."""

    import plotly.graph_objects as go

    bs = [b for b in buckets if b.get("pred_pow") is not None]
    if not bs:
        return _empty_fig(fmt.NO_DATA)
    _ensure_template()
    ff = fmt_fn or fmt.fmt_pow
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=[b["label"] for b in bs], y=[b["pred_pow"] for b in bs], name="예측",
        marker=dict(color=color or theme.COLOR["accent"]), width=0.35,
        text=[ff(b["pred_pow"]) for b in bs], textposition="outside",
        customdata=[[b["n"]] for b in bs],
        hovertemplate="%{x}<br>예측 %{text}<br>%{customdata[0]}시간 합계<extra></extra>",
    ))
    act = [b for b in bs if show_actual and b.get("actual_pow") is not None]
    if act:
        fig.add_trace(go.Bar(
            x=[b["label"] for b in act], y=[b["actual_pow"] for b in act], name="실제",
            marker=dict(color=theme.COLOR["muted"]), width=0.35,
            text=[ff(b["actual_pow"]) for b in act], textposition="outside",
            hovertemplate="%{x}<br>실제 %{text}<extra></extra>",
        ))
    top = max([b["pred_pow"] for b in bs] + [b["actual_pow"] for b in act])
    fig.update_layout(
        template=_TPL, height=height, showlegend=bool(act), barmode="group",
        yaxis=dict(title=axis_title, range=[0, top * 1.25], tickformat=tickformat),
        xaxis=dict(title="", type="category"),
    )
    return fig


def module_trend_chart(series: dict[str, tuple[list, list[float]]], *, height: int = 300):
    """Lines of mean hourly power per module (max 5 modules)."""

    import plotly.graph_objects as go

    if not series:
        return _empty_fig(fmt.NO_DATA)
    _ensure_template()
    palette = [
        theme.COLOR["accent"], theme.metric("power"), theme.metric("bill"),
        theme.metric("accuracy"), theme.metric("info"),
    ]
    fig = go.Figure()
    for i, (module, (xs, ys)) in enumerate(series.items()):
        fig.add_trace(
            go.Scatter(
                x=xs, y=ys, name=module, mode="lines",
                line=dict(color=palette[i % len(palette)], width=1.8),
                customdata=[fmt.fmt_pow(v) for v in ys],
                hovertemplate="%{x}<br>" + module + " %{customdata}<extra></extra>",
            )
        )
    fig.update_layout(
        template=_TPL, height=height, hovermode="x unified",
        yaxis=dict(title="평균 시간당 전력(kWh)"),
    )
    return fig


def module_compare_bar(items: list[dict[str, Any]], selected: set[str], *, height: int = 300,
                       axis_max: float | None = None, axis_title: str | None = None, value_fmt=None):
    """Horizontal bars of mean hourly power per module; selected = accent, others grey."""

    import plotly.graph_objects as go

    if not items:
        return _empty_fig(fmt.NO_DATA)
    _ensure_template()
    ordered = list(reversed(items))
    import math

    peak = max(r["mean"] for r in items) or 1.0
    # 막대가 끝까지 차지 않도록 축 최대값 = 최대×2.5 이상인 1·2·5·10 계열 값 (예: 3 → 10)
    if axis_max is None:
        raw = peak * 2.5
        mag = 10 ** math.floor(math.log10(raw))
        axis_max = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
    vf = value_fmt or fmt.fmt_pow
    fig = go.Figure(
        go.Bar(
            x=[r["mean"] for r in ordered],
            text=[f"{vf(r['mean'])} · {r['share']:.1f}%" for r in ordered],
            textposition="outside", textfont=dict(size=11),
            cliponaxis=False,
            y=[r["module"] for r in ordered],
            orientation="h",
            marker=dict(
                color=[theme.COLOR["accent"] if r["module"] in selected else theme.token("bar_gray") for r in ordered]
            ),
            customdata=[[vf(r["mean"]), f"{r['share']:.1f}%"] for r in ordered],
            hovertemplate="%{y}<br>%{customdata[0]} (비중 %{customdata[1]})<extra></extra>",
        )
    )
    fig.update_layout(
        template=_TPL, height=height, showlegend=False, bargap=0.4,
        xaxis=dict(title=axis_title or f"평균 시간당 전력(kWh) · 축 최대 {fmt.fmt_pow(axis_max)}",
                   showgrid=True, range=[0, axis_max]),
        yaxis=dict(showgrid=False, type="category", dtick=1),
        margin=dict(l=110, r=16, t=8, b=34),
    )
    return fig


def load_pattern_heatmap(pattern: dict[str, Any], night: list[int], *, height: int = 260):
    """Weekday × hour mean-power heatmap; night hours shaded with a grey overlay."""

    import plotly.graph_objects as go

    matrix = pattern["matrix"]
    if not any(v is not None for row in matrix for v in row):
        return _empty_fig(fmt.NO_DATA)
    _ensure_template()
    fig = go.Figure(
        go.Heatmap(
            z=matrix, x=pattern["hours"], y=pattern["days"],
            colorscale=[[0.0, theme.token("heat_low")], [0.5, theme.COLOR["accent"]], [1.0, theme.token("heat_high")]],
            xgap=2, ygap=2, showscale=False,
            customdata=[[fmt.fmt_pow(v) for v in row] for row in matrix],
            hovertemplate="%{y} %{x}시<br>평균 %{customdata}<extra></extra>",
        )
    )
    for h in night:
        fig.add_vrect(
            x0=h - 0.5, x1=h + 0.5, fillcolor=theme.token("faint"), opacity=0.25, line_width=0, layer="above"
        )
    fig.update_layout(
        template=_TPL, height=height,
        xaxis=dict(title="시(0~23) · 회색 = 야간 시간대", dtick=2, showgrid=False),
        yaxis=dict(autorange="reversed", showgrid=False),
    )
    return fig


_WEEKDAY_KR = ("월", "화", "수", "목", "금", "토", "일")


def _axis_ref(index: int, axis: str) -> str:
    """``'x'``/``'y'`` domain ref for subplot ``index`` (1-based): ``'x'``/``'x2'``..."""

    return axis if index == 1 else f"{axis}{index}"


def _by_type_text(by_type: dict[str, Any], type_labels: dict[str, str] | None) -> str:
    labels = type_labels or {}
    parts = [f"{labels.get(t, t)} {int(c)}" for t, c in sorted(by_type.items(), key=lambda kv: -kv[1])]
    return " / ".join(parts)


def anomaly_daily_small_multiples(
    daily,
    surge,
    *,
    modules: list[str],
    surge_factor: float,
    accent_color: str,
    alert_color: str,
    muted_color: str,
    type_labels: dict[str, str] | None = None,
    height: int = 420,
):
    """Per-module daily-count bar stack, one row per module (§2.1).

    ``daily`` / ``surge`` are ``page_logic.daily_counts`` / ``surge_days``
    output (all modules). Surge days render in ``alert_color`` with a small ▲
    marker; a grey dotted line marks each module's own median×factor threshold.
    Rows share the x-axis; only the bottom row carries the rangeslider, default
    range the last 30 days (slider itself spans the full period).
    """

    from datetime import timedelta
    from plotly.subplots import make_subplots
    import plotly.graph_objects as go

    if not modules or daily is None or len(daily) == 0:
        return _empty_fig(fmt.NO_DATA)

    _ensure_template()
    n = len(modules)
    fig = make_subplots(rows=n, cols=1, shared_xaxes=True, vertical_spacing=0.06)

    surge_keys: set[tuple[str, Any]] = set()
    if surge is not None and len(surge) > 0:
        surge_keys = {(r.module, r.day) for r in surge.itertuples()}

    all_days: list = []
    for i, module in enumerate(modules, start=1):
        sub = daily[daily["module"] == module].sort_values("day")
        days = list(sub["day"])
        counts = list(sub["count"])
        by_types = list(sub["by_type"])
        all_days.extend(days)
        median = float(sub["count"].median()) if len(sub) else 0.0
        threshold = median * float(surge_factor)
        is_surge = [(module, d) in surge_keys for d in days]
        colors = [alert_color if s else accent_color for s in is_surge]
        hover_text = [
            f"{d.strftime('%m-%d')} ({_WEEKDAY_KR[d.weekday()]}) · {module} · {int(c)}건"
            + (f" ({_by_type_text(bt, type_labels)})" if bt else "")
            for d, c, bt in zip(days, counts, by_types)
        ]
        # customdata[0] = hover text, customdata[1] = ISO date, customdata[2] =
        # module name — [1]/[2] let the caller map a click back to (day, module)
        # without depending on the x-axis value's exact serialised form.
        customdata = [[hover_text[j], days[j].isoformat(), module] for j in range(len(days))]
        fig.add_trace(
            go.Bar(
                x=days, y=counts, marker_color=colors,
                customdata=customdata, hovertemplate="%{customdata[0]}<extra></extra>",
                showlegend=False,
            ),
            row=i, col=1,
        )
        surge_x = [d for d, s in zip(days, is_surge) if s]
        surge_y = [c for c, s in zip(counts, is_surge) if s]
        if surge_x:
            top = max(counts) if counts else 1
            fig.add_trace(
                go.Scatter(
                    x=surge_x, y=[c + top * 0.08 + 0.5 for c in surge_y], mode="text",
                    text=["▲"] * len(surge_x), textfont=dict(color=alert_color, size=12),
                    showlegend=False, hoverinfo="skip",
                ),
                row=i, col=1,
            )
        if median > 0 and days:
            fig.add_shape(
                type="line", x0=min(days), x1=max(days), y0=threshold, y1=threshold,
                line=dict(color=muted_color, dash="dot", width=1),
                row=i, col=1,
            )
        avg = float(sub["count"].mean()) if len(sub) else 0.0
        fig.add_annotation(
            text=module, xref=f"{_axis_ref(i, 'x')} domain", yref=f"{_axis_ref(i, 'y')} domain",
            x=0.0, y=1.18, xanchor="left", showarrow=False, font=dict(size=12, color=theme.COLOR["text"]),
        )
        fig.add_annotation(
            text=f"일평균 {fmt.fmt_count_per_day(avg)}",
            xref=f"{_axis_ref(i, 'x')} domain", yref=f"{_axis_ref(i, 'y')} domain",
            x=1.0, y=1.18, xanchor="right", showarrow=False, font=dict(size=11, color=muted_color),
        )

    fig.update_xaxes(matches="x")
    if all_days:
        max_day = max(all_days)
        min_day = min(all_days)
        default_start = max(min_day, max_day - timedelta(days=29))
        fig.update_xaxes(range=[default_start, max_day])
        fig.update_xaxes(
            rangeslider=dict(visible=True, thickness=0.06, range=[min_day, max_day]),
            row=n, col=1,
        )
    fig.update_layout(template=_TPL, height=height, showlegend=False, margin=dict(l=16, r=16, t=28, b=16))
    fig.update_yaxes(showgrid=False)
    return fig


def anomaly_hour_heatmap_with_sidebar(
    matrix: dict[str, Any],
    *,
    night_hours: list[int],
    accent_color: str,
    low_color: str,
    faint_color: str,
    height: int = 360,
):
    """24h × date(or week) heatmap + a per-hour total bar sidebar (§2.2).

    ``matrix`` is ``page_logic.hour_by_day_matrix`` output. Colour caps at
    ``matrix['p95']``; 0-count cells use ``low_color`` (not pure white) so an
    all-zero row is still visibly a grid. Night hours (``night_hours``) get a
    shaded strip left of the row labels.
    """

    from plotly.subplots import make_subplots
    import plotly.graph_objects as go

    rows = matrix.get("rows") or []
    cols = matrix.get("cols") or []
    if not rows or not cols:
        return _empty_fig(fmt.NO_DATA)

    _ensure_template()
    z = matrix["matrix"]
    p95 = matrix.get("p95") or 1
    col_iso = [c.isoformat() if hasattr(c, "isoformat") else str(c) for c in cols]
    col_labels = [c.strftime("%m-%d") if hasattr(c, "strftime") else str(c) for c in cols]
    row_totals = [sum(row) for row in z]
    # customdata[row][col] = that cell's date (ISO) so a click can be mapped
    # back to a date without re-parsing the formatted x label.
    customdata = [[col_iso[c] for c in range(len(cols))] for _ in rows]

    fig = make_subplots(
        rows=1, cols=2, column_widths=[0.8, 0.2], shared_yaxes=True, horizontal_spacing=0.02,
    )
    fig.add_trace(
        go.Heatmap(
            z=z, x=col_labels, y=rows, zmin=0, zmax=max(p95, 1),
            xgap=1, ygap=1,
            colorscale=[[0.0, low_color], [1.0, accent_color]],
            colorbar=dict(title=f"≥ {p95}건", len=0.8),
            customdata=customdata,
            hovertemplate="%{y}시 · %{x}<br>%{z}건<extra></extra>",
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Bar(
            x=row_totals, y=rows, orientation="h", marker_color=accent_color,
            hovertemplate="%{y}시 합계 %{x}건<extra></extra>", showlegend=False,
        ),
        row=1, col=2,
    )
    for h in night_hours:
        fig.add_shape(
            type="rect", xref="paper", yref="y", x0=-0.06, x1=0.0, y0=h - 0.5, y1=h + 0.5,
            fillcolor=faint_color, opacity=0.35, line_width=0, layer="below",
        )
    fig.update_yaxes(autorange="reversed", dtick=2, title_text="시(0~23)", row=1, col=1)
    fig.update_yaxes(autorange="reversed", row=1, col=2)
    fig.update_xaxes(title_text=matrix.get("unit", "일"), row=1, col=1)
    fig.update_xaxes(title_text="합계", row=1, col=2)
    fig.update_layout(template=_TPL, height=height, margin=dict(l=52, r=16, t=24, b=34))
    return fig


def anomaly_24h_stacked_bar(
    profile: list[dict[str, Any]],
    usual: list[float],
    *,
    type_colors: dict[str, str],
    type_labels: dict[str, str] | None = None,
    muted_color: str | None = None,
    height: int = 300,
):
    """24h stacked bar (per-type) + the all-period 'usual' hourly average line (§2.3)."""

    import plotly.graph_objects as go

    if not profile:
        return _empty_fig(fmt.NO_DATA)

    _ensure_template()
    labels = type_labels or {}
    hours = [p["hour"] for p in profile]
    types: list[str] = []
    for p in profile:
        for t in p.get("by_type", {}):
            if t not in types:
                types.append(t)

    fig = go.Figure()
    for t in types:
        ys = [p.get("by_type", {}).get(t, 0) for p in profile]
        fig.add_trace(
            go.Bar(
                x=hours, y=ys, name=labels.get(t, t),
                marker_color=type_colors.get(t, theme.COLOR["accent"]),
                hovertemplate="%{x}시<br>" + labels.get(t, t) + " %{y}건<extra></extra>",
            )
        )
    if usual:
        fig.add_trace(
            go.Scatter(
                x=hours, y=usual[: len(hours)], name="평소", mode="lines",
                line=dict(color=muted_color or theme.COLOR["muted"], dash="dot", width=1.6),
                hovertemplate="%{x}시<br>평소 평균 %{y:.1f}건<extra></extra>",
            )
        )
    fig.update_layout(
        template=_TPL, height=height, barmode="stack",
        xaxis=dict(title="시(0~23)", dtick=2), yaxis=dict(title="건수"),
    )
    return fig


_TYPE_SYMBOLS = ["circle", "square", "triangle-up", "diamond", "x"]


def anomaly_timeline(points: list[dict[str, Any]], type_labels: dict[str, str], *, height: int = 300):
    """Detection timeline: x = day, y = module, marker shape = type, size = count."""

    import plotly.graph_objects as go

    if not points:
        return _empty_fig(fmt.NO_DATA)
    _ensure_template()
    types = sorted({p["type"] for p in points})
    colors = [theme.metric("anomaly"), theme.metric("accuracy"), theme.metric("info"), theme.metric("bill"), theme.metric("carbon")]
    max_count = max(p["count"] for p in points) or 1
    fig = go.Figure()
    for i, t in enumerate(types):
        pts = [p for p in points if p["type"] == t]
        fig.add_trace(
            go.Scatter(
                x=[p["day"] for p in pts], y=[p["module"] for p in pts], mode="markers",
                name=type_labels.get(t, t),
                marker=dict(
                    symbol=_TYPE_SYMBOLS[i % len(_TYPE_SYMBOLS)], color=colors[i % len(colors)],
                    size=[6 + 14 * (p["count"] / max_count) for p in pts], opacity=0.75,
                ),
                customdata=[fmt.fmt_count(p["count"]) for p in pts],
                hovertemplate="%{x}<br>%{y}<br>" + type_labels.get(t, t) + " %{customdata}건<extra></extra>",
            )
        )
    fig.update_layout(
        template=_TPL, height=height,
        yaxis=dict(type="category", showgrid=True, title=""), xaxis=dict(title=""),
        margin=dict(l=110, r=16, t=24, b=34),
    )
    return fig



def load_series_chart(series: dict[str, Any], *, height: int = 260):
    """부하 패턴 시계열(최근 1시간/24시간/일별) 선 그래프 + 평균선. y축은 값 범위로 확대."""

    import plotly.graph_objects as go

    from src.app import page_logic

    _ensure_template()
    fig = go.Figure(go.Scatter(
        x=series["x"], y=series["y"], mode="lines+markers" if len(series["y"]) <= 40 else "lines",
        line=dict(color=theme.COLOR["accent"], width=2), marker=dict(size=4),
        hovertemplate="%{x}<br>%{y:,.3f} " + series["unit"] + "<extra></extra>", name=series["title"],
    ))
    fig.add_hline(y=series["mean"], line=dict(color=theme.COLOR["muted"], width=1, dash="dot"),
                  annotation_text=f"평균 {series['mean']:,.3f}", annotation_position="top left",
                  annotation_font=dict(size=11, color=theme.COLOR["muted"]))
    rng = page_logic.zoom_range(series["y"])
    fig.update_layout(template=_TPL, height=height, showlegend=False,
                      yaxis=dict(title=f"{series['title']}({series['unit']})", range=list(rng) if rng else None),
                      xaxis=dict(title="", type="category", nticks=8))
    return fig
