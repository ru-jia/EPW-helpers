#!/usr/bin/env python3
"""Humidity metrics from EPW files, with an optional current-vs-future chart.

For each EPW it reports, for the whole year and for each month:
    - mean and max humidity ratio (g of water per kg of dry air)
    - mean and max dew point (deg C)
    - mean relative humidity (%)
    - hours too humid for the ASHRAE 55 comfort zone (humidity ratio > 12 g/kg)
    - hours with a dew point above 18 deg C (65 deg F), "muggy" by the common
      National Weather Service rule of thumb (not a formal standard)

Usage:
    # metrics for any number of EPWs
    python3 epw_humidity.py a.epw b.epw c.epw --out humidity_metrics.csv

    # also draw a chart: give the files as (current, future) pairs
    python3 epw_humidity.py boston_now.epw boston_2050.epw \\
                            phoenix_now.epw phoenix_2050.epw \\
                            --pairs --chart humidity_shift.png

Requires: numpy. The chart also needs matplotlib.
"""
import argparse
import csv
import sys
from pathlib import Path

import numpy as np

HOURS_PER_YEAR = 8760
MONTH_LETTERS = 'JFMAMJJASOND'

# Thresholds (change here if needed)
ASHRAE55_MAX_HUMIDITY_RATIO = 12.0   # g/kg, upper humidity limit of the ASHRAE 55 comfort zone
MUGGY_DEW_POINT = 18.0               # deg C (65 deg F)

# Columns in the EPW LOCATION line and in each hourly data row (0-based).
LOC_CITY = 1
EPW_MONTH = 1
EPW_DRY_BULB = 6         # deg C
EPW_DEW_POINT = 7        # deg C
EPW_REL_HUMIDITY = 8     # %
EPW_PRESSURE = 9         # Pa
EPW_FIRST_DATA_LINE = 8


def read_epw(path):
    """Return the EPW's city name and its hourly columns as numpy arrays."""
    with open(path, encoding='utf-8-sig', newline='') as stream:
        lines = list(csv.reader(stream))
    if len(lines) <= EPW_FIRST_DATA_LINE or lines[0][0] != 'LOCATION':
        raise ValueError('Not a valid EPW file')
    rows = lines[EPW_FIRST_DATA_LINE:]
    if len(rows) != HOURS_PER_YEAR:
        raise ValueError(f'Expected {HOURS_PER_YEAR} hourly rows, found {len(rows)}')

    def column(index):
        return np.array([float(row[index]) for row in rows])

    weather = {
        'month': column(EPW_MONTH).astype(int),
        'air_temp': column(EPW_DRY_BULB),
        'dew_point': column(EPW_DEW_POINT),
        'rel_humidity': column(EPW_REL_HUMIDITY),
        'pressure': column(EPW_PRESSURE),
    }
    weather['humidity_ratio'] = humidity_ratio(weather['air_temp'], weather['rel_humidity'],
                                               weather['pressure'])
    return lines[0][LOC_CITY], weather


def humidity_ratio(air_temp, rel_humidity, pressure):
    """Grams of water vapour per kg of dry air.

    Saturation vapour pressure uses the Magnus formula (accurate to well under 1%
    for normal outdoor temperatures); 621.945 is the ratio of the molar masses
    of water and dry air, times 1000 to give g/kg.
    """
    saturation_pressure = 610.94 * np.exp(17.625 * air_temp / (air_temp + 243.04))   # Pa
    vapour_pressure = rel_humidity / 100 * saturation_pressure
    return 621.945 * vapour_pressure / (pressure - vapour_pressure)


def summarize(weather, months=None):
    """Humidity metrics for the selected months (default: all year)."""
    selected = np.ones(HOURS_PER_YEAR, bool) if months is None else np.isin(weather['month'], months)
    ratio = weather['humidity_ratio'][selected]
    dew = weather['dew_point'][selected]
    return {
        'mean_humidity_ratio_g_kg': ratio.mean(),
        'max_humidity_ratio_g_kg': ratio.max(),
        'mean_dew_point_C': dew.mean(),
        'max_dew_point_C': dew.max(),
        'mean_rel_humidity_pct': weather['rel_humidity'][selected].mean(),
        'hours_above_ashrae55_humidity_limit': int((ratio > ASHRAE55_MAX_HUMIDITY_RATIO).sum()),
        'hours_dew_point_above_18C': int((dew > MUGGY_DEW_POINT).sum()),
        'hours': int(selected.sum()),
    }


def write_metrics(results, out_path):
    """Write one row per file and period (year, 1-12) to a CSV."""
    metric_names = list(summarize(results[0][2]).keys())
    with out_path.open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['file', 'location', 'period'] + metric_names)
        for path, city, weather in results:
            for period in ['year'] + list(range(1, 13)):
                metrics = summarize(weather, None if period == 'year' else [period])
                writer.writerow([path.name, city, period]
                                + [round(metrics[name], 2) for name in metric_names])
    print(f'Wrote {out_path}')


def draw_chart(pairs, out_path):
    """Current-vs-future humidity chart. pairs = [(label, current_weather, future_weather)].

    Top row: monthly mean humidity ratio per location, same y-axis everywhere.
    Bottom row: hours per year above the dew point and ASHRAE 55 thresholds.
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    colors = {'current': '#9AA5B1', 'future': '#0E7490'}
    labels = [label for label, _, _ in pairs]
    n = len(pairs)

    fig = plt.figure(figsize=(max(8, 3.2 * n), 8.2))
    grid = fig.add_gridspec(2, n, height_ratios=[1.15, 1], hspace=0.5, wspace=0.12)

    # Top row: one small chart per location
    monthly = {}
    for i, (label, current, future) in enumerate(pairs):
        ax = fig.add_subplot(grid[0, i])
        monthly[label] = {}
        for name, weather in [('current', current), ('future', future)]:
            values = [summarize(weather, [m])['mean_humidity_ratio_g_kg'] for m in range(1, 13)]
            ax.plot(range(12), values, color=colors[name], lw=2, marker='o', ms=3.5,
                    label=name.capitalize())
        ax.axhspan(ASHRAE55_MAX_HUMIDITY_RATIO, 22, color='#F1F5F9', zorder=0)
        ax.axhline(ASHRAE55_MAX_HUMIDITY_RATIO, color='#64748B', lw=0.8, ls='--')
        mean_now = summarize(current)['mean_humidity_ratio_g_kg']
        mean_future = summarize(future)['mean_humidity_ratio_g_kg']
        ax.set_title(f'{label}\nmean {mean_now:.1f} → {mean_future:.1f} g/kg '
                     f'({mean_future / mean_now - 1:+.0%})', loc='left', fontsize=9.5)
        ax.set_ylim(0, 22)
        ax.set_xticks(range(12))
        ax.set_xticklabels(MONTH_LETTERS)
        ax.grid(axis='y', color='#e5e7eb', lw=0.6)
        for side in ['top', 'right']:
            ax.spines[side].set_visible(False)
        if i == 0:
            ax.set_ylabel('Monthly mean humidity ratio (g/kg)')
            ax.text(0.3, 21.3, f'above {ASHRAE55_MAX_HUMIDITY_RATIO:g} g/kg: too humid for\n'
                    'ASHRAE 55 comfort zone', fontsize=7.5, color='#475569', va='top')
        else:
            ax.set_yticklabels([])
        if i == n - 1:
            ax.legend(frameon=False, loc='upper right')

    # Bottom row: hour counts, same x-axis on both panels
    rows = np.arange(n)[::-1]
    counts = {
        'dew': [(summarize(c)['hours_dew_point_above_18C'], summarize(f)['hours_dew_point_above_18C'])
                for _, c, f in pairs],
        'ashrae': [(summarize(c)['hours_above_ashrae55_humidity_limit'],
                    summarize(f)['hours_above_ashrae55_humidity_limit']) for _, c, f in pairs],
    }
    x_max = max(max(pair) for values in counts.values() for pair in values) * 1.3 + 100
    split = max(1, round(n * 0.6))

    ax = fig.add_subplot(grid[1, :split] if n > 1 else grid[1, 0])
    for row, (before, after) in zip(rows, counts['dew']):
        ax.plot([before, after], [row, row], color='#CBD5E1', lw=3, zorder=1)
        ax.scatter([before], [row], s=60, color=colors['current'], zorder=2)
        ax.scatter([after], [row], s=60, color=colors['future'], zorder=2)
        ax.text(after + x_max * 0.01, row, f'{before:,} → {after:,} h  ({after - before:+,} h)',
                va='center', fontsize=8.5)
    ax.set_yticks(rows)
    ax.set_yticklabels(labels)
    ax.set_xlim(0, x_max)
    ax.set_title('Hours per year with dew point above 18 °C (65 °F)', loc='left', fontsize=11)
    ax.grid(axis='x', color='#e5e7eb', lw=0.6)
    ax.set_axisbelow(True)
    for side in ['top', 'right']:
        ax.spines[side].set_visible(False)

    if n > 1:
        ax2 = fig.add_subplot(grid[1, split:])
        bar = 0.38
        for j, name in enumerate(['current', 'future']):
            values = [pair[j] for pair in counts['ashrae']]
            ax2.barh(rows + (0.5 - j) * bar, values, bar - 0.04, color=colors[name])
        ax2.set_yticks(rows)
        ax2.set_yticklabels(labels)
        ax2.set_xlim(0, x_max)
        ax2.set_title(f'Hours per year above {ASHRAE55_MAX_HUMIDITY_RATIO:g} g/kg (ASHRAE 55 limit)',
                      loc='left', fontsize=11)
        ax2.grid(axis='x', color='#e5e7eb', lw=0.6)
        ax2.set_axisbelow(True)
        for side in ['top', 'right']:
            ax2.spines[side].set_visible(False)

    fig.suptitle('Humidity shift, current vs future: same scales for every location',
                 x=0.01, ha='left', fontsize=13)
    fig.text(0.01, 0.005, 'Source: hourly EPW dew point, relative humidity and station pressure. '
             'Humidity ratio via Magnus formula.', fontsize=7.5, color='#64748B')
    fig.savefig(out_path, dpi=200, bbox_inches='tight')
    print(f'Wrote {out_path}')


def main():
    parser = argparse.ArgumentParser(description='Humidity metrics (and an optional chart) from EPW files.')
    parser.add_argument('epw', nargs='+', type=Path, help='one or more .epw files')
    parser.add_argument('--out', type=Path, default=Path('humidity_metrics.csv'),
                        help='metrics CSV to write (default: humidity_metrics.csv)')
    parser.add_argument('--pairs', action='store_true',
                        help='treat the files as (current, future) pairs, in order')
    parser.add_argument('--chart', type=Path, help='PNG to write; requires --pairs')
    parser.add_argument('--labels', nargs='+',
                        help='one label per pair for the chart (default: city name from the EPW)')
    args = parser.parse_args()

    if args.chart and not args.pairs:
        parser.error('--chart needs --pairs (files given as current, future, current, future, ...)')
    if args.pairs and len(args.epw) % 2:
        parser.error('--pairs needs an even number of files')

    results, failures = [], []
    for path in args.epw:
        try:
            city, weather = read_epw(path)
            results.append((path, city, weather))
        except (OSError, ValueError, IndexError) as error:
            failures.append(f'{path}: {error}')

    if failures:
        print(f'{len(failures)} of {len(args.epw)} file(s) could not be read:', file=sys.stderr)
        for message in failures:
            print(f'  {message}', file=sys.stderr)
        sys.exit(1)

    write_metrics(results, args.out)

    if args.chart:
        pairs = []
        for k in range(0, len(results), 2):
            (_, city, current), (_, _, future) = results[k], results[k + 1]
            label = args.labels[k // 2] if args.labels and k // 2 < len(args.labels) else city
            pairs.append((label, current, future))
        draw_chart(pairs, args.chart)


if __name__ == '__main__':
    main()
