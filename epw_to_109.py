#!/usr/bin/env python3
"""Convert EPW weather files to the TRNSYS .109 weather format.

The .109 format is TRNSYS's "user-defined" weather format (read by TRNSYS
Type 109).

Usage:
    python3 epw_to_109.py weather.epw [more.epw ...]

Each output file is written next to its input with the .109 extension.

Input requirements:
    - one full, non-leap year of hourly data (8760 rows, Jan 1 to Dec 31)

"""
import argparse
import csv
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

HOURS_PER_YEAR = 8760

# Position of each header line in the EPW file (0-based).
EPW_LOCATION_LINE = 0
EPW_DATA_PERIODS_LINE = 7
EPW_FIRST_DATA_LINE = 8

# Columns in the EPW LOCATION line.
LOC_LATITUDE = 6
LOC_LONGITUDE = 7
LOC_TIME_ZONE = 8

# Columns in each hourly EPW data row.
EPW_MONTH = 1
EPW_DAY = 2
EPW_HOUR = 3              # EPW hours run 1-24 (hour 1 = 00:00-01:00)
EPW_DRY_BULB = 6          # deg C
EPW_REL_HUMIDITY = 8      # %
EPW_PRESSURE = 9          # Pa
EPW_GLOBAL_HORIZ = 13     # Wh/m2
EPW_DIFFUSE_HORIZ = 15    # Wh/m2
EPW_WIND_DIRECTION = 20   # degrees
EPW_WIND_SPEED = 21       # m/s
EPW_TOTAL_SKY_COVER = 22  # tenths (0-10)
EPW_PRECIPITATION = 33    # mm

# Variables written to the .109 file, in the order the reader expects them.
OUTPUT_VARIABLES = ['IBEAM_H', 'IDIFF_H', 'IGLOB_H', 'TAMB', 'WSPEED',
                    'RHUM', 'WDIR', 'CCOVER', 'PAMB', 'PRECIP']

# Radiation and precipitation are totals over the previous hour, so their
# TRNSYS sampling flag is -1 (averaged over the past interval). The other
# variables are instantaneous readings at the time stamp (flag 0).
AVERAGED_VARIABLES = {'IBEAM_H', 'IDIFF_H', 'IGLOB_H', 'PRECIP'}


def read_checked_value(row, column, low, high, label):
    """Read one number from a CSV row and check it lies between low and high.

    EPW marks missing values with codes like 99.9 or 9999, so the ranges
    below are chosen to reject those codes instead of passing them through.
    """
    value = float(row[column])
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'Missing or invalid {label}: {value}')
    return value


def read_epw(source):
    """Read an EPW file and return its LOCATION line and its hourly rows."""
    if source.suffix.lower() != '.epw':
        raise ValueError('Input must be an .epw file')

    with source.open(encoding='utf-8-sig', newline='') as stream:
        lines = list(csv.reader(stream))

    if len(lines) <= EPW_FIRST_DATA_LINE:
        raise ValueError('File is too short to be an EPW (missing header or data)')

    location = lines[EPW_LOCATION_LINE]
    data_periods = lines[EPW_DATA_PERIODS_LINE]
    rows = lines[EPW_FIRST_DATA_LINE:]

    if location[0] != 'LOCATION' or data_periods[0] != 'DATA PERIODS':
        raise ValueError('Invalid EPW header')

    one_data_period = int(data_periods[1]) == 1
    hourly_records = int(data_periods[2]) == 1
    if not (one_data_period and hourly_records and len(rows) == HOURS_PER_YEAR):
        raise ValueError('Requires one complete, non-leap year of 8760 hourly records')

    return location, rows


def build_109_header(location):
    """Build the .109 header lines (site information and variable list)."""
    latitude = read_checked_value(location, LOC_LATITUDE, -90, 90, 'latitude')
    longitude = read_checked_value(location, LOC_LONGITUDE, -180, 180, 'longitude')
    time_zone = read_checked_value(location, LOC_TIME_ZONE, -12, 14, 'time zone')

    # EPW uses east-positive longitude; the TRNSYS reader expects west-positive.
    west_positive_longitude = -longitude

    header = [
        '<userdefined> ! Converted from EPW',
        f'<longitude> {west_positive_longitude:9.4f}',
        f'<latitude>  {latitude:9.4f}',
        f'<gmt> {time_zone:g}',
        '<interval> 1',
        '<firsttime> 1',
    ]

    # Data column 1 is the hour counter, so the first variable is in column 2.
    for column, name in enumerate(OUTPUT_VARIABLES, start=2):
        sampling = -1 if name in AVERAGED_VARIABLES else 0
        header.append(f'<var> {name} <col> {column} <interp> 0 <add> 0 '
                      f'<mult> 1 <samp> {sampling}')

    header.append('<data>')
    return header


def check_timestamp(row, hour_of_year):
    """Check that a row is the expected hour, so the year is in order."""
    # 2001 is used only as a reference non-leap year to step through the calendar.
    expected = datetime(2001, 1, 1) + timedelta(hours=hour_of_year - 1)
    expected_stamp = (expected.month, expected.day, expected.hour + 1)
    actual_stamp = (int(row[EPW_MONTH]), int(row[EPW_DAY]), int(row[EPW_HOUR]))
    if actual_stamp != expected_stamp:
        raise ValueError(f'Record {hour_of_year}: expected chronological '
                         f'Jan 1-Dec 31 hourly data, found month/day/hour {actual_stamp}')


def convert_row(row, hour_of_year):
    """Convert one EPW data row into one .109 data line."""
    check_timestamp(row, hour_of_year)

    global_horiz = read_checked_value(row, EPW_GLOBAL_HORIZ, 0, 9998,
                                      'global horizontal radiation')
    diffuse_horiz = read_checked_value(row, EPW_DIFFUSE_HORIZ, 0, 9998,
                                       'diffuse horizontal radiation')
    if diffuse_horiz > global_horiz:
        raise ValueError(f'Record {hour_of_year}: diffuse radiation exceeds global radiation')

    # Beam radiation on a horizontal surface = global - diffuse.
    # (EPW's direct normal radiation is measured on a surface facing the sun,
    # not a horizontal one, so it cannot be used here directly.)
    # An hourly total in Wh/m2 has the same number as the hourly mean in W/m2.
    beam_horiz = global_horiz - diffuse_horiz

    values = [
        beam_horiz,
        diffuse_horiz,
        global_horiz,
        read_checked_value(row, EPW_DRY_BULB, -90, 70, 'temperature'),
        read_checked_value(row, EPW_WIND_SPEED, 0, 40, 'wind speed'),
        read_checked_value(row, EPW_REL_HUMIDITY, 0, 110, 'relative humidity'),
        read_checked_value(row, EPW_WIND_DIRECTION, 0, 360, 'wind direction'),
        read_checked_value(row, EPW_TOTAL_SKY_COVER, 0, 10, 'total sky cover') / 10,  # tenths -> 0-1
        read_checked_value(row, EPW_PRESSURE, 31000, 120000, 'pressure'),
        read_checked_value(row, EPW_PRECIPITATION, 0, 998, 'liquid precipitation depth'),
    ]
    return f'{hour_of_year} ' + ' '.join(f'{value:.6f}' for value in values)


def convert(source):
    """Convert one EPW file to a .109 file next to it."""
    target = source.with_suffix('.109')

    location, rows = read_epw(source)
    output_lines = build_109_header(location)
    for hour_of_year, row in enumerate(rows, start=1):
        output_lines.append(convert_row(row, hour_of_year))

    # Everything is checked before anything is written, so a bad input never
    # leaves a half-written file behind. Mode 'x' refuses to overwrite an
    # existing .109 file. Lines end with CRLF (Windows line endings), which
    # some older .109 readers require.
    with target.open('x', encoding='ascii', newline='') as stream:
        stream.write('\r\n'.join(output_lines) + '\r\n')
    print(f'Created: {target} ({HOURS_PER_YEAR} hours)')


def main():
    parser = argparse.ArgumentParser(
        description='Convert EPW weather files to the TRNSYS .109 weather format.')
    parser.add_argument('epw', nargs='+', type=Path, help='one or more .epw files')
    args = parser.parse_args()

    # Try every file, then report all failures at the end.
    failures = []
    for path in args.epw:
        try:
            convert(path)
        except (OSError, ValueError, IndexError) as error:
            failures.append(f'{path}: {error}')

    if failures:
        print(f'\n{len(failures)} of {len(args.epw)} file(s) failed:', file=sys.stderr)
        for message in failures:
            print(f'  {message}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
