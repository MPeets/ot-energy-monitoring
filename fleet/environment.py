"""The world the devices live in.

Everything here is a pure function of wall-clock time, so separate containers
agree without talking to each other. That matters most for grid frequency:
it's one value for the whole synchronous area, so every site has to report
the same number, the way real meters do.
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone

NOMINAL_FREQUENCY_HZ = 50.0

# Where the simulated parks are. Roughly Baltic, so day length and sun height
# through the year look right. Not any real site.
LATITUDE_DEG = 58.0
LONGITUDE_DEG = 25.0


def _random(key: str, bucket: int) -> float:
    """A fixed pseudo-random number in [-1, 1) for this key and bucket, the same on every host."""
    digest = hashlib.blake2b(f"{key}:{bucket}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") / 2**63 - 1.0


def noise(key: str, t: float, period: float) -> float:
    """Smooth noise in [-1, 1]: a new random target every `period` seconds, eased between."""
    position = t / period
    bucket = math.floor(position)
    weight = (1 - math.cos(math.pi * (position - bucket))) / 2
    start, end = _random(key, bucket), _random(key, bucket + 1)
    return start + (end - start) * weight


def grid_frequency(t: float) -> float:
    """System frequency in Hz at time t, before any scenario.

    Wanders by at most 30 mHz: normal operation, inside the +-50 mHz standard
    frequency range of the Continental European synchronous area.
    """
    deviation = (
        0.012 * math.sin(2 * math.pi * t / 600)  # slow generation/load imbalance
        + 0.008 * math.sin(2 * math.pi * t / 127)
        + 0.010 * noise("grid-frequency", t, 15)  # load noise
    )
    return NOMINAL_FREQUENCY_HZ + deviation


def phase_voltages_pu(key: str, t: float) -> tuple[float, float, float]:
    """Phase voltages at one device's terminals in per-unit, before any scenario.

    Unlike frequency, voltage is local: it depends on load and reactive power
    nearby, so every device gets its own variation, plus a small fixed
    unbalance between phases. Stays within +-2% of nominal.
    """
    shared = 0.008 * math.sin(2 * math.pi * t / 900 + math.pi * _random(key, 0)) + 0.004 * noise(f"{key}:v", t, 30)
    return tuple(
        1.0 + shared + 0.003 * _random(f"{key}:unbalance", phase) + 0.001 * noise(f"{key}:l{phase}", t, 10)
        for phase in (1, 2, 3)
    )


def wind_speed(site: str, t: float) -> float:
    """Mean wind over one park in m/s: swings over hours plus gusts lasting minutes.

    Ranges roughly 3.5 to 13 m/s, so the turbines spend most of the time
    producing and some of it at rated power.
    """
    return 8.5 + 3.0 * math.sin(2 * math.pi * t / 5400 + math.pi * _random(site, 0)) + 1.5 * noise(f"{site}:wind", t, 120)


def sun_elevation(t: float, latitude: float = LATITUDE_DEG, longitude: float = LONGITUDE_DEG) -> float:
    """Height of the sun above the horizon in degrees. Good to about a degree,
    which is plenty for a simulated pyranometer."""
    moment = datetime.fromtimestamp(t, timezone.utc)
    day = moment.timetuple().tm_yday
    declination = math.radians(23.44) * math.sin(2 * math.pi * (284 + day) / 365)
    b = 2 * math.pi * (day - 81) / 364
    equation_of_time_min = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)
    utc_hours = moment.hour + moment.minute / 60 + moment.second / 3600
    solar_hours = utc_hours + longitude / 15 + equation_of_time_min / 60
    hour_angle = math.radians(15 * (solar_hours - 12))
    lat = math.radians(latitude)
    sin_elevation = math.sin(lat) * math.sin(declination) + math.cos(lat) * math.cos(declination) * math.cos(hour_angle)
    return math.degrees(math.asin(sin_elevation))


def clear_sky_irradiance(elevation_deg: float) -> float:
    """Irradiance on a cloudless day in W/m2 (Haurwitz clear-sky model)."""
    if elevation_deg <= 0:
        return 0.0
    cos_zenith = math.sin(math.radians(elevation_deg))
    return 1098 * cos_zenith * math.exp(-0.057 / cos_zenith)


def irradiance(site: str, t: float) -> float:
    """What the park's pyranometer reads in W/m2: clear sky dimmed by passing
    clouds. Zero at night, which is a normal state, not a fault."""
    cover = 0.35 + 0.5 * noise(f"{site}:clouds", t, 600) + 0.25 * noise(f"{site}:cloud-edges", t, 45)
    cover = min(max(cover, 0.0), 1.0)
    return clear_sky_irradiance(sun_elevation(t)) * (1 - 0.75 * cover)


def ambient_temperature(t: float) -> float:
    """Outdoor air temperature in degrees C: cold winters, mild summers, warmest
    in the afternoon. Drives how hot the substation transformer runs."""
    moment = datetime.fromtimestamp(t, timezone.utc)
    day = moment.timetuple().tm_yday
    seasonal = 6.0 - 11.0 * math.cos(2 * math.pi * (day - 20) / 365)  # about -5 in January, 17 in July
    solar_hours = moment.hour + moment.minute / 60 + LONGITUDE_DEG / 15
    daily = 4.0 * math.sin(2 * math.pi * (solar_hours - 9) / 24)  # coolest around dawn
    return seasonal + daily + 1.5 * noise("ambient", t, 1800)