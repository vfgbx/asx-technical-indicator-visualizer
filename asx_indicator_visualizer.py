#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
asx_indicator_visualizer.py

Interactive technical-indicator visualiser for ASX daily OHLCV data.

This script loads multiple ASX stock CSV files, computes custom price/volume
"energy scores", trend speed, and an effort-vs-price-impact ratio, then lets the
user select a symbol and inspect a multi-panel Matplotlib chart.

The visual layout is designed to compare:
1. Price movement
2. Price energy score
3. Volume energy score
4. Average compound speed
5. Past effort versus current effort

The tool is experimental and intended for research, visual analysis, and
portfolio demonstration. It is not financial advice.
"""

from __future__ import annotations

import argparse
import glob
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Dict, Tuple

import matplotlib
matplotlib.use("TkAgg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.widgets import Slider


DEFAULT_CSV_DIR = "/Users/richman/量化交易文件/1day股票数据(每日更新)"
DEFAULT_CSV_GLOB = "*.csv"
DEFAULT_PRICE_COL = "average"
DEFAULT_EMA_MIN = 380
DEFAULT_EMA_MAX = 390
DEFAULT_VOLUME_EMA_MIN = 150
DEFAULT_VOLUME_EMA_MAX = 200
DEFAULT_SCORE_EMA_FAST = 380
DEFAULT_SCORE_EMA_SLOW = 390
DEFAULT_SPEED_WINDOW = 200
DEFAULT_EFFORT_WINDOW = 50
DEFAULT_TIMEZONE = "Australia/Sydney"
DEFAULT_WORKERS = None

REQUIRED_COLUMNS = {
    "date", "open", "high", "low", "close", "average", "volume", "barcount",
}


def safe_symbol_from_path(path: str) -> str:
    """Extract a stock symbol from a CSV filename.

    Examples:
    - `BHP.csv` -> `BHP`
    - `BHP_ASX_1day_20150101_20260101.csv` -> `BHP`
    """
    base = os.path.basename(path)
    name = os.path.splitext(base)[0]
    return name.split("_")[0].upper()


def load_one_csv(path: str) -> pd.DataFrame:
    """Load and validate one OHLCV CSV file.

    Expected columns:
        date, open, high, low, close, average, volume, barcount

    Dates are parsed as UTC and numeric columns are safely coerced. Incomplete
    rows are removed before the dataframe is sorted by time.
    """
    df = pd.read_csv(path)
    df.columns = [column.strip().lower() for column in df.columns]

    if not REQUIRED_COLUMNS.issubset(df.columns):
        missing = sorted(list(REQUIRED_COLUMNS - set(df.columns)))
        raise ValueError(f"{path} missing columns: {missing}")

    parsed_dates = pd.to_datetime(df["date"], errors="coerce", utc=True)
    df = df.loc[parsed_dates.notna()].copy()
    df["date"] = parsed_dates.loc[parsed_dates.notna()].copy()

    numeric_cols = ["open", "high", "low", "close", "average", "volume", "barcount"]
    for column in numeric_cols:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    valid_rows = np.ones(len(df), dtype=bool)
    for column in numeric_cols:
        valid_rows &= df[column].notna().to_numpy()

    df = df.loc[valid_rows].copy()
    return df.sort_values("date").reset_index(drop=True)


def first_point_after_bear_cross(ema_fast: np.ndarray, ema_slow: np.ndarray) -> np.ndarray:
    """Return True at the first point after a bearish fast-below-slow cross.

    Bearish crossover formula:
        fast[t-1] >= slow[t-1]
        fast[t]   <  slow[t]
    """
    fast = np.asarray(ema_fast, dtype=float)
    slow = np.asarray(ema_slow, dtype=float)
    valid = np.isfinite(fast) & np.isfinite(slow)
    cross = np.zeros_like(valid, dtype=bool)
    cross[1:] = valid[1:] & valid[:-1] & (fast[:-1] >= slow[:-1]) & (fast[1:] < slow[1:])
    return cross


def first_point_after_bull_cross(ema_fast: np.ndarray, ema_slow: np.ndarray) -> np.ndarray:
    """Return True at the first point after a bullish fast-above-slow cross.

    Bullish crossover formula:
        fast[t-1] <= slow[t-1]
        fast[t]   >  slow[t]
    """
    fast = np.asarray(ema_fast, dtype=float)
    slow = np.asarray(ema_slow, dtype=float)
    valid = np.isfinite(fast) & np.isfinite(slow)
    cross = np.zeros_like(valid, dtype=bool)
    cross[1:] = valid[1:] & valid[:-1] & (fast[:-1] <= slow[:-1]) & (fast[1:] > slow[1:])
    return cross


def compute_energy_score_from_series(series: pd.Series, ema_min: int, ema_max: int) -> pd.Series:
    """Compute an EMA-spacing energy score.

    Formula:
        score[t] = Σ_p [(EMA_p[t] - EMA_(p+1)[t]) / |EMA_(p+1)[t]|] × ln(p + 1)

    where p goes from ema_min to ema_max - 1.

    Intuition:
    - Shorter EMAs above longer EMAs usually produce a positive score.
    - Shorter EMAs below longer EMAs usually produce a negative score.
    - The logarithmic weight gives larger spans slightly more influence without
      letting one span dominate.
    """
    values = pd.Series(series.values, index=series.index, dtype=float)
    spans = list(range(ema_min, ema_max + 1))

    ema_data = {
        span: values.ewm(span=span, adjust=False, min_periods=span).mean().to_numpy()
        for span in spans
    }
    ema_df = pd.DataFrame(ema_data, index=values.index)
    valid = ema_df.notna().all(axis=1).to_numpy()

    score = np.full(len(values), np.nan, dtype=float)
    idx = np.where(valid)[0]

    if len(idx) > 0:
        acc = np.zeros(len(idx), dtype=np.float64)
        eps = 1e-12
        for span in range(ema_min, ema_max):
            short_ema = ema_df.loc[idx, span].to_numpy()
            long_ema = ema_df.loc[idx, span + 1].to_numpy()
            relative_gap = (short_ema - long_ema) / (np.abs(long_ema) + eps)
            acc += relative_gap * np.log(span + 1.0)
        score[idx] = acc

    return pd.Series(score, index=values.index)


def compute_average_compound_speed(df: pd.DataFrame, window: int, price_col: str) -> pd.DataFrame:
    """Compute average compound speed over a lookback window.

    Formula:
        speed[t] = ((price[t] / price[t-window]) ** (1/window) - 1) × 100

    This converts total price change over the window into an average per-period
    compound percentage rate.
    """
    if window < 1:
        raise ValueError("window must be >= 1")

    df = df.copy()
    price = pd.Series(df[price_col].values, index=df.index, dtype=float)
    previous_price = price.shift(window)

    price_arr = price.to_numpy(dtype=float)
    prev_arr = previous_price.to_numpy(dtype=float)
    speed = np.full(len(df), np.nan, dtype=float)

    valid = np.isfinite(price_arr) & np.isfinite(prev_arr) & (price_arr > 0) & (prev_arr > 0)
    speed[valid] = ((price_arr[valid] / prev_arr[valid]) ** (1.0 / window) - 1.0) * 100.0

    df[f"avg_speed_{window}"] = speed
    return df


def compute_effort_price_impact_ratio(df: pd.DataFrame, window: int) -> pd.DataFrame:
    """Compute a past-effort versus current-effort price-impact ratio.

    Step 1:
        abs_move_pct[t] = |close[t] / open[t] - 1| × 100
    Step 2:
        current_effort_per_1pct[t] = volume[t] / abs_move_pct[t]
    Step 3:
        past_avg_effort_per_1pct[t]
          = sum(volume[t-window:t-1]) / sum(abs_move_pct[t-window:t-1])
    Step 4:
        effort_relative[t]
          = (past_avg_effort_per_1pct[t] / current_effort_per_1pct[t] - 1) × 100

    Positive values mean the current bar moved price with less volume per 1%
    than the recent past. Negative values mean the current bar required more
    volume per 1% movement than the recent past.
    """
    if window < 1:
        raise ValueError("window must be >= 1")

    df = df.copy()
    eps = 1e-12

    open_arr = df["open"].to_numpy(dtype=float)
    close_arr = df["close"].to_numpy(dtype=float)
    volume_arr = df["volume"].to_numpy(dtype=float)

    abs_move_pct = np.full(len(df), np.nan, dtype=float)
    valid_price = np.isfinite(open_arr) & np.isfinite(close_arr) & (open_arr > 0)
    abs_move_pct[valid_price] = np.abs(close_arr[valid_price] / open_arr[valid_price] - 1.0) * 100.0

    current_effort = np.full(len(df), np.nan, dtype=float)
    valid_current = np.isfinite(volume_arr) & np.isfinite(abs_move_pct) & (volume_arr >= 0) & (abs_move_pct > eps)
    current_effort[valid_current] = volume_arr[valid_current] / abs_move_pct[valid_current]

    volume_series = pd.Series(volume_arr, index=df.index, dtype=float)
    abs_move_series = pd.Series(abs_move_pct, index=df.index, dtype=float)

    past_volume_sum = volume_series.shift(1).rolling(window=window, min_periods=window).sum()
    past_abs_move_sum = abs_move_series.shift(1).rolling(window=window, min_periods=window).sum()

    past_effort = np.full(len(df), np.nan, dtype=float)
    past_volume_arr = past_volume_sum.to_numpy(dtype=float)
    past_abs_move_arr = past_abs_move_sum.to_numpy(dtype=float)
    valid_past = np.isfinite(past_volume_arr) & np.isfinite(past_abs_move_arr) & (past_volume_arr >= 0) & (past_abs_move_arr > eps)
    past_effort[valid_past] = past_volume_arr[valid_past] / past_abs_move_arr[valid_past]

    effort_relative = np.full(len(df), np.nan, dtype=float)
    valid_relative = np.isfinite(current_effort) & np.isfinite(past_effort) & (current_effort > eps)
    effort_relative[valid_relative] = (past_effort[valid_relative] / current_effort[valid_relative] - 1.0) * 100.0

    direction = np.full(len(df), np.nan, dtype=float)
    valid_direction = np.isfinite(open_arr) & np.isfinite(close_arr)
    direction[valid_direction & (close_arr > open_arr)] = 1.0
    direction[valid_direction & (close_arr < open_arr)] = -1.0
    direction[valid_direction & (close_arr == open_arr)] = 0.0

    df["abs_oc_move_pct"] = abs_move_pct
    df["current_effort_per_1pct"] = current_effort
    df[f"past_avg_effort_per_1pct_{window}"] = past_effort
    df[f"effort_price_relative_pct_{window}"] = effort_relative
    df["effort_direction"] = direction
    return df


def compute_indicators(df: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    """Compute all indicators required by the dashboard."""
    df = df.copy()

    price = pd.Series(df[args.price_col].values, index=df.index, dtype=float)
    df["score"] = compute_energy_score_from_series(price, args.ema_min, args.ema_max).to_numpy()

    volume = pd.Series(df["volume"].values, index=df.index, dtype=float)
    df["volume_score"] = compute_energy_score_from_series(volume, args.volume_ema_min, args.volume_ema_max).to_numpy()

    score_s = pd.Series(df["score"].values, index=df.index, dtype=float)
    df["score_ema_fast"] = score_s.ewm(span=args.score_ema_fast, adjust=False, min_periods=args.score_ema_fast).mean().to_numpy()
    df["score_ema_slow"] = score_s.ewm(span=args.score_ema_slow, adjust=False, min_periods=args.score_ema_slow).mean().to_numpy()

    df = compute_average_compound_speed(df, args.speed_window, args.price_col)
    df = compute_effort_price_impact_ratio(df, args.effort_window)
    return df


def plot_symbol_with_sliders(symbol: str, df: pd.DataFrame, args: argparse.Namespace) -> None:
    """Display the multi-panel interactive chart for one stock symbol."""
    dfp = df.copy()
    dates = dfp["date"].dt.tz_convert(args.timezone)
    score_series = dfp["score"].dropna()

    if score_series.empty:
        print(f"[{symbol}] score is all NaN. More history is required.")
        return

    speed_col = f"avg_speed_{args.speed_window}"
    effort_col = f"effort_price_relative_pct_{args.effort_window}"

    score_min = float(score_series.min())
    score_max = float(score_series.max())
    low_init = float(score_series.quantile(0.2))
    high_init = float(score_series.quantile(0.8))

    fig = plt.figure(figsize=(16, 14))
    grid = fig.add_gridspec(nrows=5, ncols=1, height_ratios=[3.0, 2.0, 1.5, 1.6, 1.6])

    ax_price = fig.add_subplot(grid[0, 0])
    ax_score = fig.add_subplot(grid[1, 0], sharex=ax_price)
    ax_volume_score = fig.add_subplot(grid[2, 0], sharex=ax_price)
    ax_speed = fig.add_subplot(grid[3, 0], sharex=ax_price)
    ax_effort = fig.add_subplot(grid[4, 0], sharex=ax_price)

    ax_price.plot(dates, dfp[args.price_col].values, linewidth=1.2)
    ax_price.set_title(f"{symbol} | {args.price_col} / Price Score / Volume Score / Avg Speed / Past Effort vs Current Effort")
    ax_price.set_ylabel(args.price_col)
    ax_price.grid(True, alpha=0.2)

    ax_score.plot(dates, dfp["score"].values, linewidth=1.1, label=f"Price score EMA({args.ema_min}~{args.ema_max})")
    ax_score.plot(dates, dfp["score_ema_fast"].values, linewidth=1.2, label=f"Price Score EMA({args.score_ema_fast})")
    ax_score.plot(dates, dfp["score_ema_slow"].values, linewidth=1.2, label=f"Price Score EMA({args.score_ema_slow})")
    ax_score.axhline(0, linewidth=1.0, alpha=0.4)
    ax_score.set_ylabel("Price score")
    ax_score.grid(True, alpha=0.2)
    ax_score.legend(loc="upper left")

    bear_idx = np.where(first_point_after_bear_cross(dfp["score_ema_fast"].values, dfp["score_ema_slow"].values))[0]
    bull_idx = np.where(first_point_after_bull_cross(dfp["score_ema_fast"].values, dfp["score_ema_slow"].values))[0]

    if bear_idx.size > 0:
        x_bear = matplotlib.dates.date2num(dates.iloc[bear_idx].to_numpy())
        for ax, y_col, size in [(ax_price, args.price_col, 90), (ax_score, "score", 70)]:
            ax.scatter(x_bear, dfp.loc[bear_idx, y_col].to_numpy(), s=size, marker="v", c="black", edgecolors="black", linewidths=0.5, zorder=7)

    if bull_idx.size > 0:
        x_bull = matplotlib.dates.date2num(dates.iloc[bull_idx].to_numpy())
        for ax, y_col, size in [(ax_price, args.price_col, 90), (ax_score, "score", 80)]:
            ax.scatter(x_bull, dfp.loc[bull_idx, y_col].to_numpy(), s=size, marker="^", c="green", edgecolors="black", linewidths=0.5, zorder=7)

    ax_volume_score.plot(dates, dfp["volume_score"].values, linewidth=1.2, color="orange", label=f"Volume score EMA({args.volume_ema_min}~{args.volume_ema_max})")
    ax_volume_score.axhline(0, linewidth=1.0, alpha=0.4)
    ax_volume_score.set_ylabel("Volume score")
    ax_volume_score.grid(True, alpha=0.2)
    ax_volume_score.legend(loc="upper left")

    if bear_idx.size > 0:
        ax_volume_score.scatter(matplotlib.dates.date2num(dates.iloc[bear_idx].to_numpy()), dfp.loc[bear_idx, "volume_score"].to_numpy(), s=70, marker="v", c="black", edgecolors="black", linewidths=0.5, zorder=7)
    if bull_idx.size > 0:
        ax_volume_score.scatter(matplotlib.dates.date2num(dates.iloc[bull_idx].to_numpy()), dfp.loc[bull_idx, "volume_score"].to_numpy(), s=80, marker="^", c="green", edgecolors="black", linewidths=0.5, zorder=7)

    ax_speed.plot(dates, dfp[speed_col].values, linewidth=1.2, color="purple", label=f"Avg compound speed over past {args.speed_window} periods")
    ax_speed.axhline(0, linewidth=1.0, alpha=0.4)
    ax_speed.set_ylabel("Avg speed %")
    ax_speed.grid(True, alpha=0.2)
    ax_speed.legend(loc="upper left")

    x_num = matplotlib.dates.date2num(dates.to_numpy())
    effort_values = dfp[effort_col].to_numpy(dtype=float)
    open_arr = dfp["open"].to_numpy(dtype=float)
    close_arr = dfp["close"].to_numpy(dtype=float)
    bar_colors = np.full(len(dfp), "gray", dtype=object)
    bar_colors[np.isfinite(open_arr) & np.isfinite(close_arr) & (close_arr > open_arr)] = "green"
    bar_colors[np.isfinite(open_arr) & np.isfinite(close_arr) & (close_arr < open_arr)] = "red"
    valid_effort = np.isfinite(effort_values)

    if len(x_num) >= 2:
        diffs = np.diff(x_num)
        diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
        bar_width = float(np.median(diffs) * 0.8) if len(diffs) > 0 else 0.8
    else:
        bar_width = 0.8

    ax_effort.bar(x_num[valid_effort], effort_values[valid_effort], width=bar_width, color=bar_colors[valid_effort], alpha=0.75, align="center", label=f"Past effort per 1% / current effort per 1% - 1, window={args.effort_window}")
    ax_effort.axhline(0, linewidth=1.0, alpha=0.4)
    ax_effort.set_ylabel("Past / Current - 1 (%)")
    ax_effort.grid(True, alpha=0.2)
    ax_effort.legend(loc="upper left")

    band_scatter_price = ax_price.scatter([], [], s=60, marker="o", c="red", edgecolors="black", linewidths=0.5)
    band_scatter_score = ax_score.scatter([], [], s=40, marker="o", c="red", edgecolors="black", linewidths=0.5)
    band_scatter_volume_score = ax_volume_score.scatter([], [], s=40, marker="o", c="red", edgecolors="black", linewidths=0.5)
    band_fill = ax_score.axhspan(low_init, high_init, alpha=0.12)

    slider_low_ax = fig.add_axes([0.12, 0.04, 0.76, 0.018])
    slider_high_ax = fig.add_axes([0.12, 0.01, 0.76, 0.018])
    low_slider = Slider(slider_low_ax, "Low threshold (show price score >=)", score_min, score_max, valinit=low_init)
    high_slider = Slider(slider_high_ax, "High threshold (show price score <=)", score_min, score_max, valinit=high_init)

    def update(_=None) -> None:
        nonlocal band_fill
        low_threshold = float(low_slider.val)
        high_threshold = float(high_slider.val)
        if low_threshold > high_threshold:
            low_threshold, high_threshold = high_threshold, low_threshold

        score = dfp["score"].to_numpy(dtype=float)
        volume_score = dfp["volume_score"].to_numpy(dtype=float)
        price_arr = dfp[args.price_col].to_numpy(dtype=float)
        mask = np.isfinite(score) & (score >= low_threshold) & (score <= high_threshold)
        x_selected = dates[mask].to_numpy()

        if len(x_selected) > 0:
            x_selected_num = matplotlib.dates.date2num(x_selected)
            band_scatter_price.set_offsets(np.column_stack([x_selected_num, price_arr[mask]]))
            band_scatter_score.set_offsets(np.column_stack([x_selected_num, score[mask]]))
            band_scatter_volume_score.set_offsets(np.column_stack([x_selected_num, volume_score[mask]]))
        else:
            empty = np.empty((0, 2))
            band_scatter_price.set_offsets(empty)
            band_scatter_score.set_offsets(empty)
            band_scatter_volume_score.set_offsets(empty)

        band_fill.remove()
        band_fill = ax_score.axhspan(low_threshold, high_threshold, alpha=0.12)
        fig.canvas.draw_idle()

    low_slider.on_changed(update)
    high_slider.on_changed(update)
    update()

    plt.tight_layout(rect=[0, 0.08, 1, 1])
    plt.show()


def process_one_file(path: str, args: argparse.Namespace) -> Tuple[str, pd.DataFrame]:
    """Load one CSV file and compute all indicators for it."""
    symbol = safe_symbol_from_path(path)
    df = load_one_csv(path)
    return symbol, compute_indicators(df, args)


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description="Interactive ASX technical-indicator visualiser.")
    parser.add_argument("--csv-dir", default=DEFAULT_CSV_DIR, help="Directory containing OHLCV CSV files.")
    parser.add_argument("--csv-glob", default=DEFAULT_CSV_GLOB, help="CSV filename pattern.")
    parser.add_argument("--price-col", default=DEFAULT_PRICE_COL, help="Price column used for price score and speed.")
    parser.add_argument("--ema-min", type=int, default=DEFAULT_EMA_MIN, help="Minimum EMA span for price energy score.")
    parser.add_argument("--ema-max", type=int, default=DEFAULT_EMA_MAX, help="Maximum EMA span for price energy score.")
    parser.add_argument("--volume-ema-min", type=int, default=DEFAULT_VOLUME_EMA_MIN, help="Minimum EMA span for volume score.")
    parser.add_argument("--volume-ema-max", type=int, default=DEFAULT_VOLUME_EMA_MAX, help="Maximum EMA span for volume score.")
    parser.add_argument("--score-ema-fast", type=int, default=DEFAULT_SCORE_EMA_FAST, help="Fast EMA span applied to price score.")
    parser.add_argument("--score-ema-slow", type=int, default=DEFAULT_SCORE_EMA_SLOW, help="Slow EMA span applied to price score.")
    parser.add_argument("--speed-window", type=int, default=DEFAULT_SPEED_WINDOW, help="Lookback window for average compound speed.")
    parser.add_argument("--effort-window", type=int, default=DEFAULT_EFFORT_WINDOW, help="Lookback window for effort/price impact.")
    parser.add_argument("--timezone", default=DEFAULT_TIMEZONE, help="Timezone used for plotting dates.")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="Number of parallel workers. Defaults to CPU count.")
    return parser.parse_args()


def main() -> None:
    """Load stock files, compute indicators, and enter interactive plotting mode."""
    args = parse_args()
    paths = sorted(glob.glob(os.path.join(args.csv_dir, args.csv_glob)))
    if not paths:
        raise FileNotFoundError(f"No CSV files found in {args.csv_dir} with pattern {args.csv_glob}")

    workers = args.workers if (args.workers is not None and args.workers > 0) else (os.cpu_count() or 1)
    print(f"[INFO] Scanning {len(paths)} CSV files with {workers} workers.")
    print(f"[INFO] Price column: {args.price_col}")
    print(f"[INFO] Price score EMA range: {args.ema_min}~{args.ema_max}")
    print(f"[INFO] Volume score EMA range: {args.volume_ema_min}~{args.volume_ema_max}")
    print(f"[INFO] Score fast/slow EMA: {args.score_ema_fast}/{args.score_ema_slow}")
    print(f"[INFO] Speed window: {args.speed_window}")
    print(f"[INFO] Effort window: {args.effort_window}")

    data_map: Dict[str, pd.DataFrame] = {}
    with ProcessPoolExecutor(max_workers=workers) as executor:
        future_map = {executor.submit(process_one_file, path, args): path for path in paths}
        for future in as_completed(future_map):
            path = future_map[future]
            try:
                symbol, df = future.result()
                data_map[symbol] = df
            except Exception as exc:
                print(f"[SKIP] {path}: {type(exc).__name__}: {exc}")

    if not data_map:
        print("[INFO] No valid files loaded.")
        return

    print("\nLoaded symbols:")
    print(", ".join(sorted(data_map.keys())))

    while True:
        symbol = input("\nEnter symbol to plot, or 'q' to quit: ").strip().upper()
        if symbol.lower() in {"q", "quit", "exit"}:
            break
        if symbol not in data_map:
            print(f"Symbol {symbol!r} not found. Try again.")
            continue
        plot_symbol_with_sliders(symbol, data_map[symbol], args)


if __name__ == "__main__":
    main()
