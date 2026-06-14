# ASX Technical Indicator Visualizer

An interactive Python visualisation tool for exploring ASX stock price behaviour using custom technical indicators, EMA-based energy scores, volume-pressure analysis, compound speed, and effort-versus-price-impact relationships.

This repository demonstrates a research workflow for turning raw OHLCV stock data into interpretable multi-panel charts. The tool was used to generate visual examples for Australian stocks such as **BPT**, **BHP**, and **CBA**.

The project is intended for research, learning, and portfolio demonstration only. It is not financial advice.

---

## Project Structure

```text
asx-technical-indicator-visualizer/
├── README.md
├── requirements.txt
├── scripts/
│   └── asx_indicator_visualizer.py
├── examples/
│   ├── BPT_Image.png
│   ├── BHP_Image.png
│   └── CBA_Image.png
└── outputs/
    └── .gitkeep
```

The `examples/` folder can contain the generated charts, such as the BPT, BHP, and CBA images. Large raw market data files should usually be excluded from GitHub unless they are small sample files.

---

## What This Project Does

The script loads ASX OHLCV CSV files, computes several custom indicators, and displays an interactive multi-panel chart for any selected stock symbol.

The chart contains five panels:

1. Price chart
2. Price energy score and its fast/slow EMA lines
3. Volume energy score
4. Average compound speed
5. Past effort versus current effort

It also includes two interactive sliders that allow the user to highlight historical points where the price score falls inside a selected range.

---

## Example Outputs

Example charts generated from this workflow include:

```text
BPT_Image.png
BHP_Image.png
CBA_Image.png
```

These charts show how the same indicator framework behaves across different Australian stocks.

The visual layout helps compare:

- price trend direction,
- price score trend,
- volume score pressure,
- average price speed,
- effort required to move price,
- bullish and bearish score crossovers.

---

## Input Data Format

The script expects CSV files containing daily OHLCV data.

Required columns:

```text
date
open
high
low
close
average
volume
barcount
```

The default price column used by the indicators is:

```text
average
```

The `average` column is used because it can represent a smoother trading-price reference than only close price.

---

## Main Script

```text
scripts/asx_indicator_visualizer.py
```

The script can scan a folder of CSV files, compute indicators in parallel, and then let the user type a symbol interactively.

Example terminal flow:

```text
Loaded symbols:
BHP, BPT, CBA, ...

Enter symbol to plot, or 'q' to quit:
```

If the user enters:

```text
BHP
```

the script opens the interactive chart for BHP.

---

## Indicator 1: EMA Energy Score

The core indicator is an EMA-spacing energy score.

For a given series, such as price or volume, the script calculates EMAs over a span range:

```text
EMA_MIN, EMA_MIN + 1, ..., EMA_MAX
```

Then it compares neighbouring EMAs.

For each EMA span $p$, the contribution is:

$$
\frac{\mathrm{EMA}_p(t) - \mathrm{EMA}_{p+1}(t)}
{|\mathrm{EMA}_{p+1}(t)|}
\times \ln(p + 1)
$$

The full score is:

$$

\mathrm{score}(t)

=

\sum_{p=\mathrm{EMA}_{\min}}^{\mathrm{EMA}_{\max}-1}

\left[

\frac{\mathrm{EMA}_p(t) - \mathrm{EMA}_{p+1}(t)}

{|\mathrm{EMA}_{p+1}(t)|}

\times \ln(p + 1)

\right]

$$
```

### Intuition

If shorter EMAs are above longer EMAs:

```text
EMA_p > EMA_(p+1)
```

then the score tends to be positive.

This usually means upward trend pressure.

If shorter EMAs are below longer EMAs:

```text
EMA_p < EMA_(p+1)
```

then the score tends to be negative.

This usually means downward trend pressure.

The logarithmic weight:

```text
ln(p + 1)
```

gives larger EMA spans slightly more influence while avoiding excessive domination by a single span.

---

## Price Energy Score

The price energy score applies the EMA energy formula to the selected price column.

Default settings:

```text
PRICE_COL = average
EMA_MIN = 380
EMA_MAX = 390
```

This means the script calculates a long-range price trend-energy score using neighbouring EMAs between 380 and 390 periods.

The price score panel includes:

```text
Price score EMA(380~390)
Price Score EMA(380)
Price Score EMA(390)
```

The fast and slow score EMAs are used to identify crossovers.

---

## Bullish and Bearish Crossovers

The script marks two types of crossover events.

### Bullish crossover

A bullish crossover is detected when:

```text
fast[t-1] <= slow[t-1]
fast[t]   >  slow[t]
```

This is plotted as a green upward triangle.

Conceptually, it means the faster score line has moved above the slower score line.

### Bearish crossover

A bearish crossover is detected when:

```text
fast[t-1] >= slow[t-1]
fast[t]   <  slow[t]
```

This is plotted as a black downward triangle.

Conceptually, it means the faster score line has moved below the slower score line.

The same crossover markers are projected onto the price chart, price-score panel, and volume-score panel.

---

## Volume Energy Score

The volume score uses the same EMA energy formula, but applies it to the volume series instead of price.

Default settings:

```text
VOLUME_EMA_MIN = 150
VOLUME_EMA_MAX = 200
```

Formula:

```text
volume_score[t] =
Σ from p = 150 to 199
[
    (Volume_EMA_p[t] - Volume_EMA_(p+1)[t])
    / |Volume_EMA_(p+1)[t]|
    × ln(p + 1)
]
```

### Intuition

The volume score attempts to show whether shorter-term volume pressure is stronger or weaker than longer-term volume pressure.

Positive values may suggest increasing volume pressure.

Negative values may suggest fading volume pressure.

This can be visually compared against price-score changes and price movement.

---

## Average Compound Speed

The average compound speed measures how quickly price has changed over a lookback window.

Default setting:

```text
SPEED_ACCEL_DAYS = 200
```

Formula:

```text
speed[t] =
((price[t] / price[t-window]) ** (1 / window) - 1) × 100
```

For a 200-period window:

```text
speed[t] =
((price[t] / price[t-200]) ** (1 / 200) - 1) × 100
```

### Intuition

This converts the total movement over the lookback window into an average per-period compound percentage speed.

- Positive speed: price has been rising on average.
- Negative speed: price has been falling on average.
- Near-zero speed: price has been moving sideways overall.

This is different from simple percentage change because it expresses movement as a compounded average rate.

---

## Effort vs Price Impact Indicator

This indicator compares current trading effort against recent historical effort.

The idea is:

```text
How much volume is needed to move price by 1%?
```

A bar where price moves a lot on relatively low volume may indicate a different type of market behaviour compared with a bar where large volume produces little price movement.

---

## Step 1: Absolute Open-to-Close Move

For each bar:

```text
abs_move_pct[t] = |close[t] / open[t] - 1| × 100
```

This measures the absolute percentage movement between open and close.

---

## Step 2: Current Effort per 1% Move

```text
current_effort_per_1pct[t] = volume[t] / abs_move_pct[t]
```

This estimates how much volume was required for each 1% of open-to-close price movement on the current bar.

---

## Step 3: Past Average Effort per 1% Move

Using the previous `window` bars:

```text
past_avg_effort_per_1pct[t] =
sum(volume[t-window : t-1]) / sum(abs_move_pct[t-window : t-1])
```

Default setting:

```text
EFFORT_PRICE_WINDOW = 50
```

This measures the recent historical volume required for each 1% of price movement.

---

## Step 4: Relative Effort Ratio

The final indicator is:

```text
effort_relative[t] =
(past_avg_effort_per_1pct[t] / current_effort_per_1pct[t] - 1) × 100
```

### Interpretation

If the value is positive:

```text
past effort per 1% > current effort per 1%
```

then the current bar moved price with less effort than the recent past.

If the value is negative:

```text
past effort per 1% < current effort per 1%
```

then the current bar required more effort than the recent past.

In the chart:

```text
green bars = close > open
red bars   = close < open
gray bars  = close == open
```

This makes it easier to compare direction and efficiency.

---

## Interactive Sliders

The chart includes two sliders:

```text
Low threshold
High threshold
```

These sliders select a range of price-score values.

All points where:

```text
low_threshold <= price_score <= high_threshold
```

are highlighted on:

1. the price chart,
2. the price-score panel,
3. the volume-score panel.

This helps visually inspect what happened historically when the price score entered a specific range.

---

## Chart Layout

The generated chart has five panels:

```text
Panel 1: Price
Panel 2: Price score + fast/slow score EMAs
Panel 3: Volume score
Panel 4: Average compound speed
Panel 5: Effort vs price impact
```

The title format is:

```text
SYMBOL | average / Price Score / Volume Score / Avg Speed / Past Effort vs Current Effort
```

Example:

```text
BHP | average / Price Score / Volume Score / Avg Speed / Past Effort vs Current Effort
```

---

## Installation

Install the required packages:

```bash
pip install pandas numpy matplotlib
```

Tkinter is required by Matplotlib's `TkAgg` backend. On many systems it is already available. If not, install it through your operating system's package manager.

---

## Example Usage

Run with default settings:

```bash
python scripts/asx_indicator_visualizer.py
```

Run with a custom CSV directory:

```bash
python scripts/asx_indicator_visualizer.py \
  --csv-dir /path/to/asx_daily_csvs
```

Run with custom indicator parameters:

```bash
python scripts/asx_indicator_visualizer.py \
  --csv-dir /path/to/asx_daily_csvs \
  --ema-min 380 \
  --ema-max 390 \
  --volume-ema-min 150 \
  --volume-ema-max 200 \
  --score-ema-fast 380 \
  --score-ema-slow 390 \
  --speed-window 200 \
  --effort-window 50
```

---

## Example Chart Files

The repository can include example outputs:

```text
examples/BPT_Image.png
examples/BHP_Image.png
examples/CBA_Image.png
```

These files demonstrate how the visualiser presents different stocks.

### BPT Example

The BPT chart shows a long historical price series with price-score, volume-score, speed, and effort indicators. It demonstrates how score crossovers can be mapped onto both price and indicator panels.

### BHP Example

The BHP chart shows a stronger long-term price recovery and highlights how the price-score fast/slow lines can move across different trend regimes.

### CBA Example

The CBA chart shows multiple highlighted price-score ranges and demonstrates how slider-selected score zones can reveal repeated historical patterns.

---

## Practical Notes

The script currently expects local CSV files. It does not download market data by itself.

If you want to generate input files automatically, this visualiser can be used together with a separate ASX data downloader pipeline.

Before running the script on another machine, update or pass:

```text
--csv-dir
--csv-glob
--price-col
```

---

## Limitations

1. The indicators are experimental.
2. The visual output is for research and interpretation only.
3. The script does not execute trades.
4. The script does not perform statistical validation.
5. The indicator settings may overfit specific historical periods.
6. Large EMA windows require long historical datasets.
7. The tool depends on clean OHLCV data.

---

## Disclaimer

This project is for educational, research, and portfolio demonstration purposes only.

It is not financial advice, investment advice, trading advice, or a recommendation to buy or sell any security.

Use at your own risk.
