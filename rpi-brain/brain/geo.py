"""
Robot Owl RPi Brain - Geodesy helpers (pure math, no hardware, unit-testable).

These functions power the navigation feature: given the owl's current GPS fix
and a destination's coordinates, compute the bearing (which compass direction
the destination is in) and the distance (how far away it is). They are pure
functions of their inputs so they can be tested on any machine without a GPS.
"""

import math

# Mean Earth radius in meters (spherical model). Good enough for a walking
# compass (sub-meter accuracy is not needed; GPS itself is ~3-10 m).
EARTH_RADIUS_M = 6371000.0


def to_rad(deg: float) -> float:
    """Convert degrees to radians."""
    return math.radians(deg)


def to_deg(rad: float) -> float:
    """Convert radians to degrees."""
    return math.degrees(rad)


def wrap_180(angle_deg: float) -> float:
    """Normalize an angle (degrees) into the range [-180, 180).

    Used to express a *relative* direction: 0 = straight ahead, positive =
    turn right, negative = turn left. Exactly 180 (straight behind) maps to
    -180 so "behind" reads as a leftward limit. e.g. wrap_180(190) == -170.
    """
    a = angle_deg % 360.0
    if a >= 180.0:
        a -= 360.0
    return a


def wrap_360(angle_deg: float) -> float:
    """Normalize an angle (degrees) into the range [0, 360)."""
    return angle_deg % 360.0


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 to point 2, in degrees.

    0 = north, 90 = east, 180 = south, 270 = west (standard compass bearing).
    This is the initial bearing (the direction you must face at the start),
    which is what a walking compass wants.
    """
    phi1 = to_rad(lat1)
    phi2 = to_rad(lat2)
    d_lambda = to_rad(lon2 - lon1)

    y = math.sin(d_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(d_lambda)
    return wrap_360(to_deg(math.atan2(y, x)))


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points (haversine), in meters."""
    phi1 = to_rad(lat1)
    phi2 = to_rad(lat2)
    d_phi = to_rad(lat2 - lat1)
    d_lambda = to_rad(lon2 - lon1)

    a = (math.sin(d_phi / 2.0) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2)
    a = min(1.0, max(0.0, a))  # guard against floating-point overshoot of 1
    c = 2.0 * math.asin(math.sqrt(a))
    return EARTH_RADIUS_M * c


def aim_angle(bearing: float, heading: float, sign: int = 1,
               head_min: float = -45.0, head_max: float = 45.0) -> float:
    """Head-servo angle (clamped to [head_min, head_max]) that points the beak
    at `bearing` given the owl currently faces `heading`.

    `bearing` and `heading` are compass degrees. The head points the offset
    between them, wrapped to (-180, 180] so 0 = straight ahead. `sign` flips the
    convention (the one thing that must be confirmed on hardware: does the head
    servo's +angle turn left or right, and does the IMU yaw increase clockwise).
    The clamp handles "destination behind the owl" by pinning the head to its
    range limit (the head only turns +-45, so a destination straight behind can
    only be pointed at as closely as the range allows).
    """
    rel = wrap_180(bearing - heading) * sign
    if rel > head_max:
        return head_max
    if rel < head_min:
        return head_min
    return rel
