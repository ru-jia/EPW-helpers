# EPW-helpers

Small, readable Python scripts that take EnergyPlus weather (EPW) files directly as input.

| Script | What it does | Needs |
|---|---|---|
| `epw_utci.py` | Hours in each UTCI thermal-stress category (10 categories), per month and per year, for four exposure cases: sun/shade × wind/no wind. Same method as the CBE Clima Tool. | `pythermalcomfort` |
| `epw_humidity.py` | Humidity metrics (humidity ratio, dew point, RH, hours above the ASHRAE 55 humidity limit, hours with dew point > 18 °C), per month and per year. Optional current-vs-future chart. | `numpy` (+ `matplotlib` for the chart) |
| `epw_to_109.py` | Converts EPW to the TRNSYS `.109` ("user-defined") weather format. Only needed for tools that read `.109` instead of EPW. | standard library only |

All scripts accept any number of EPW files (one full, non-leap hourly year each) and keep going if one file fails, listing the failures at the end.

## Install

```bash
pip install pythermalcomfort numpy matplotlib
```

## Examples

```bash
# UTCI stress hours for several files
python3 epw_utci.py boston.epw phoenix.epw --out utci_hours.csv

# Humidity metrics, plus a chart comparing current and future files (given as pairs)
python3 epw_humidity.py boston_now.epw boston_2050.epw phoenix_now.epw phoenix_2050.epw \
    --pairs --chart humidity_shift.png --labels Boston Phoenix

# Convert to .109
python3 epw_to_109.py weather.epw
```

Outputs are CSV in long format (one row per file / case / month / category), ready for Excel pivot tables or plotting.
Settings such as thresholds and the UTCI exposure assumptions are listed at the top of each script.
