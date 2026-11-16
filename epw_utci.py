#!/usr/bin/env python3
"""Count hours in each UTCI thermal-stress category, straight from EPW files.

UTCI (Universal Thermal Climate Index) is a "feels like" temperature for people
outdoors. It combines air temperature, humidity, wind speed and mean radiant
temperature (heat from sun and surroundings).

For every hour of the year this script calculates UTCI for four exposure cases,
the same four used by the CBE Clima Tool:

    Sun_Wind      in the sun,  with the weather file's wind
    Sun_noWind    in the sun,  with almost no wind (0.5 m/s)
    noSun_Wind    in shade,    with the weather file's wind
    noSun_noWind  in shade,    with almost no wind (0.5 m/s)

It then counts how many hours fall into each of the 10 UTCI stress categories,
for the whole year and for each month.

Usage:
    python3 epw_utci.py weather.epw [more.epw ...] [--out utci_hours.csv]
    python3 epw_utci.py weather.epw --hourly-out utci_hourly.csv

Output (CSV, long format, one row per file / case / month / category):
    file, location, case, month, category, hours
    "month" is 1-12, or "year" for the annual total.

Requires: pythermalcomfort (pip install pythermalcomfort). Tested with 4.8.1.

Method follows CBE Clima Tool (github.com/CenterForTheBuiltEnvironment/clima,
pages/lib/extract_df.py), so results can be compared with its charts.
Checked against Clima's charts for 10 EPWs: category hours agree within ~10 h
per year (of 8760), except in very humid places (e.g. Houston, up to ~30 h).
That gap is expected: Clima uses pythermalcomfort 2.9.1, whose UTCI vapour
pressure was ~1% too high (fixed in 4.6), which matters most in humid heat.
"""
import argparse
import csv
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from pythermalcomfort.models import solar_gain, utci

HOURS_PER_YEAR = 8760

# ---------------------------------------------------------------------------
# Settings (same values as CBE Clima Tool)
# ---------------------------------------------------------------------------

# Person standing in the sun. Clima assumes the sun hits the body 45 degrees off
# the front, the whole body and sky are exposed, and a typical shortwave
# absorptivity of 0.7 with a ground reflectance of 0.6.
SUN_ANGLE_TO_FRONT_OF_BODY = 45   # degrees ("SHARP" in ASHRAE 55)
SKY_VIEW_FRACTION = 1.0           # f_svv
BODY_FRACTION_IN_SUN = 1.0        # f_bes
SOLAR_TRANSMITTANCE = 1.0         # no glazing or shading outdoors
BODY_ABSORPTIVITY = 0.7
FLOOR_REFLECTANCE = 0.6
POSTURE = 'standing'
MAX_SOLAR_MRT_INCREASE = 70.0     # deg C; Clima caps the sun's effect here

# UTCI is defined with wind measured 10 m above ground, which is what EPW
# files contain, so no height correction is needed. The UTCI model is only
# valid between 0.5 and 17 m/s, so Clima nudges values into that range.
WIND_MIN = 0.6                    # used when the file's wind is <= 0.5 m/s
WIND_MAX = 16.9                   # used when the file's wind is >= 17 m/s
NO_WIND_SPEED = 0.5               # the "noWind" cases

# UTCI stress categories. Each bin includes its upper edge, e.g.
# 26 < UTCI <= 32 is "moderate heat stress".
UTCI_CATEGORIES = [
    (-40, 'extreme cold stress'),
    (-27, 'very strong cold stress'),
    (-13, 'strong cold stress'),
    (0, 'moderate cold stress'),
    (9, 'slight cold stress'),
    (26, 'no thermal stress'),
    (32, 'moderate heat stress'),
    (38, 'strong heat stress'),
    (46, 'very strong heat stress'),
    (math.inf, 'extreme heat stress'),
]
OUTSIDE_MODEL_RANGE = 'outside UTCI model range'   # hours where UTCI is undefined

CASES = ['Sun_Wind', 'Sun_noWind', 'noSun_Wind', 'noSun_noWind']

# ---------------------------------------------------------------------------
# Reading the EPW
# ---------------------------------------------------------------------------

# Columns in the EPW LOCATION line and in each hourly data row (0-based).
LOC_CITY, LOC_LATITUDE, LOC_LONGITUDE, LOC_TIME_ZONE = 1, 6, 7, 8
EPW_MONTH, EPW_DAY, EPW_HOUR = 1, 2, 3       # EPW hours run 1-24
EPW_DRY_BULB = 6                             # deg C
EPW_REL_HUMIDITY = 8                         # %
EPW_DIRECT_NORMAL = 14                       # Wh/m2, beam radiation facing the sun
EPW_WIND_SPEED = 21                          # m/s, at 10 m height
EPW_FIRST_DATA_LINE = 8


def read_epw(path):
    """Return the EPW's location info and its hourly columns as numpy arrays."""
    with open(path, encoding='utf-8-sig', newline='') as stream:
        lines = list(csv.reader(stream))
    if len(lines) <= EPW_FIRST_DATA_LINE or lines[0][0] != 'LOCATION':
        raise ValueError('Not a valid EPW file')

    location = {
        'city': lines[0][LOC_CITY],
        'latitude': float(lines[0][LOC_LATITUDE]),
        'longitude': float(lines[0][LOC_LONGITUDE]),
        'time_zone': float(lines[0][LOC_TIME_ZONE]),
    }
    rows = lines[EPW_FIRST_DATA_LINE:]
    if len(rows) != HOURS_PER_YEAR:
        raise ValueError(f'Expected {HOURS_PER_YEAR} hourly rows, found {len(rows)}')

    def column(index):
        return np.array([float(row[index]) for row in rows])

    weather = {
        'month': column(EPW_MONTH).astype(int),
        'day': column(EPW_DAY).astype(int),
        'hour': column(EPW_HOUR).astype(int),
        'air_temp': column(EPW_DRY_BULB),
        'rel_humidity': column(EPW_REL_HUMIDITY),
        'direct_normal': column(EPW_DIRECT_NORMAL),
        'wind_speed': column(EPW_WIND_SPEED),
    }
    return location, weather


# ---------------------------------------------------------------------------
# Sun position (NOAA solar calculator equations; no extra packages needed)
# ---------------------------------------------------------------------------

def solar_altitude(month, day, hour, latitude, longitude, time_zone):
    """Sun's height above the horizon in degrees, including atmospheric refraction.

    The sun position is taken at the clock time the EPW hour ends (EPW hour 1
    = 01:00 local standard time), the same convention Clima uses. 2019 is used as
    the calendar year, as in Clima; any non-leap year gives nearly identical angles.
    """
    altitudes = np.empty(len(month))
    for i, (m, d, h) in enumerate(zip(month, day, hour)):
        local_time = datetime(2019, m, d) + timedelta(hours=int(h))
        utc_time = local_time - timedelta(hours=time_zone)
        julian_day = (utc_time - datetime(2000, 1, 1, 12)).total_seconds() / 86400 + 2451545.0
        centuries = (julian_day - 2451545.0) / 36525

        # Sun's apparent position along its yearly path
        mean_longitude = (280.46646 + centuries * (36000.76983 + centuries * 0.0003032)) % 360
        mean_anomaly = 357.52911 + centuries * (35999.05029 - 0.0001537 * centuries)
        eccentricity = 0.016708634 - centuries * (0.000042037 + 0.0000001267 * centuries)
        anomaly_rad = math.radians(mean_anomaly)
        center = (math.sin(anomaly_rad) * (1.914602 - centuries * (0.004817 + 0.000014 * centuries))
                  + math.sin(2 * anomaly_rad) * (0.019993 - 0.000101 * centuries)
                  + math.sin(3 * anomaly_rad) * 0.000289)
        omega = math.radians(125.04 - 1934.136 * centuries)
        apparent_longitude = mean_longitude + center - 0.00569 - 0.00478 * math.sin(omega)
        mean_obliquity = 23 + (26 + (21.448 - centuries * (46.815 + centuries * (0.00059 - centuries * 0.001813))) / 60) / 60
        obliquity = math.radians(mean_obliquity + 0.00256 * math.cos(omega))
        declination = math.asin(math.sin(obliquity) * math.sin(math.radians(apparent_longitude)))

        # Equation of time: difference between clock time and sun time, in minutes
        y = math.tan(obliquity / 2) ** 2
        lon_rad = math.radians(mean_longitude)
        equation_of_time = 4 * math.degrees(
            y * math.sin(2 * lon_rad)
            - 2 * eccentricity * math.sin(anomaly_rad)
            + 4 * eccentricity * y * math.sin(anomaly_rad) * math.cos(2 * lon_rad)
            - 0.5 * y * y * math.sin(4 * lon_rad)
            - 1.25 * eccentricity ** 2 * math.sin(2 * anomaly_rad))

        # Hour angle: how far the sun is from its highest point of the day
        minutes_after_midnight = h * 60
        true_solar_minutes = (minutes_after_midnight + equation_of_time
                              + 4 * longitude - 60 * time_zone) % 1440
        hour_angle = math.radians(true_solar_minutes / 4 - 180)

        lat_rad = math.radians(latitude)
        cos_zenith = (math.sin(lat_rad) * math.sin(declination)
                      + math.cos(lat_rad) * math.cos(declination) * math.cos(hour_angle))
        altitude = 90 - math.degrees(math.acos(max(-1.0, min(1.0, cos_zenith))))
        altitudes[i] = altitude + atmospheric_refraction(altitude)
    return altitudes


def atmospheric_refraction(altitude):
    """Small upward correction (degrees) because air bends sunlight near the horizon."""
    if altitude > 85:
        return 0.0
    tan_alt = math.tan(math.radians(altitude))
    if altitude > 5:
        arcseconds = 58.1 / tan_alt - 0.07 / tan_alt ** 3 + 0.000086 / tan_alt ** 5
    elif altitude > -0.575:
        arcseconds = 1735 + altitude * (-518.2 + altitude * (103.4 + altitude * (-12.79 + altitude * 0.711)))
    else:
        arcseconds = -20.772 / tan_alt
    return arcseconds / 3600


# ---------------------------------------------------------------------------
# UTCI
# ---------------------------------------------------------------------------

def mean_radiant_temp_in_sun(weather, location):
    """Mean radiant temperature of a person standing in direct sun (deg C).

    In shade it is assumed equal to air temperature. In the sun, ASHRAE 55's
    SolarCal method adds the extra radiant heat from the direct beam.
    """
    altitude = solar_altitude(weather['month'], weather['day'], weather['hour'],
                              location['latitude'], location['longitude'],
                              location['time_zone'])
    altitude = np.clip(altitude, 0, 90)          # sun below horizon -> 0 (no direct beam)

    gain = solar_gain(
        sol_altitude=altitude,
        sharp=SUN_ANGLE_TO_FRONT_OF_BODY,
        sol_radiation_dir=weather['direct_normal'],
        sol_transmittance=SOLAR_TRANSMITTANCE,
        f_svv=SKY_VIEW_FRACTION,
        f_bes=BODY_FRACTION_IN_SUN,
        asw=BODY_ABSORPTIVITY,
        posture=POSTURE,
        floor_reflectance=FLOOR_REFLECTANCE,
    )
    increase = np.minimum(np.asarray(gain.delta_mrt, dtype=float), MAX_SOLAR_MRT_INCREASE)
    return weather['air_temp'] + increase


def utci_for_all_cases(weather, location):
    """Return {case name: hourly UTCI array} for the four sun/wind cases."""
    air_temp = weather['air_temp']
    humidity = weather['rel_humidity']
    radiant_sun = mean_radiant_temp_in_sun(weather, location)
    radiant_shade = air_temp

    wind = weather['wind_speed'].copy()
    wind[wind >= 17] = WIND_MAX
    wind[wind <= 0.5] = WIND_MIN
    calm = np.full_like(wind, NO_WIND_SPEED)

    inputs = {
        'Sun_Wind': (radiant_sun, wind),
        'Sun_noWind': (radiant_sun, calm),
        'noSun_Wind': (radiant_shade, wind),
        'noSun_noWind': (radiant_shade, calm),
    }
    return {case: np.asarray(utci(tdb=air_temp, tr=radiant, v=speed, rh=humidity).utci, dtype=float)
            for case, (radiant, speed) in inputs.items()}


def stress_category(value):
    """Name of the UTCI stress category for one UTCI value."""
    if math.isnan(value):
        return OUTSIDE_MODEL_RANGE
    for upper_edge, name in UTCI_CATEGORIES:
        if value <= upper_edge:
            return name


def count_hours(utci_values, months):
    """Count hours per category, per month and for the whole year."""
    category_names = [name for _, name in UTCI_CATEGORIES] + [OUTSIDE_MODEL_RANGE]
    counts = {period: dict.fromkeys(category_names, 0) for period in list(range(1, 13)) + ['year']}
    for value, month in zip(utci_values, months):
        category = stress_category(value)
        counts[month][category] += 1
        counts['year'][category] += 1
    return counts


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Hours in each UTCI thermal-stress category, from EPW files.')
    parser.add_argument('epw', nargs='+', type=Path, help='one or more .epw files')
    parser.add_argument('--out', type=Path, default=Path('utci_hours.csv'),
                        help='summary CSV to write (default: utci_hours.csv)')
    parser.add_argument('--hourly-out', type=Path,
                        help='optional CSV with the hourly UTCI values for every file and case')
    args = parser.parse_args()

    summary_rows, hourly_rows, failures = [], [], []
    for path in args.epw:
        try:
            location, weather = read_epw(path)
            utci_by_case = utci_for_all_cases(weather, location)
        except (OSError, ValueError, IndexError) as error:
            failures.append(f'{path}: {error}')
            continue

        for case in CASES:
            counts = count_hours(utci_by_case[case], weather['month'])
            for period, by_category in counts.items():
                for category, hours in by_category.items():
                    if category == OUTSIDE_MODEL_RANGE and hours == 0:
                        continue
                    summary_rows.append([path.name, location['city'], case, period, category, hours])

        if args.hourly_out:
            for i in range(HOURS_PER_YEAR):
                hourly_rows.append([path.name, weather['month'][i], weather['day'][i], weather['hour'][i]]
                                   + [round(utci_by_case[case][i], 1) for case in CASES])
        print(f'Done: {path.name} ({location["city"]})')

    with args.out.open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['file', 'location', 'case', 'month', 'category', 'hours'])
        writer.writerows(summary_rows)
    print(f'Wrote {args.out}')

    if args.hourly_out:
        with args.hourly_out.open('w', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['file', 'month', 'day', 'hour'] + [f'utci_{case}' for case in CASES])
            writer.writerows(hourly_rows)
        print(f'Wrote {args.hourly_out}')

    if failures:
        print(f'\n{len(failures)} of {len(args.epw)} file(s) failed:', file=sys.stderr)
        for message in failures:
            print(f'  {message}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
