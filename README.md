# EPW-helpers

Small Python scripts for working with EnergyPlus weather (EPW) files.

## Scripts

| Script | What it does |
|---|---|
| `epw_to_109.py` | Converts EPW files to the TRNSYS `.109` ("user-defined") weather format. Only needed for tools that read `.109` instead of EPW. |

## Usage

```bash
python3 epw_to_109.py weather.epw [more.epw ...]
```

Each `.109` file is written next to its EPW. Existing files are not overwritten.
Requires Python 3 (standard library only).
