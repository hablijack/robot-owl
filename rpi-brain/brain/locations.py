"""
Robot Owl RPi Brain - Named-locations store.

Holds the places the owl can navigate to: a mapping of a name (e.g. "home") to
a {lat, lon} coordinate. Persisted to a small JSON file so the map survives
reboots. This is the "teach the owl your places" half of navigation -- the
web UI adds/removes entries here, and the Navigation controller looks a name up
to compute a bearing.

The store is deliberately simple and dependency-free (just the standard
library). Names are normalized (trimmed + lowercased) for matching; the
display name as entered is kept separately.
"""

import json
import logging
import os
import tempfile

logger = logging.getLogger(__name__)

# Default storage location. Overridable via config (navigation.locations_file).
# Kept out of the install dir so a re-install (rsync --delete) doesn't wipe it.
DEFAULT_LOCATIONS_FILE = os.path.join(
    os.path.expanduser("~"), ".config", "robot-owl", "locations.json"
)


class LocationsStore:
    """A persisted, name -> {lat, lon} map the owl can navigate to."""

    def __init__(self, path: str = DEFAULT_LOCATIONS_FILE):
        self.path = path
        # name (normalized) -> {"name": display, "lat": float, "lon": float}
        self._items = {}
        self.load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def load(self) -> None:
        """Load the store from disk (no-op with an empty store if absent)."""
        if not self.path or not os.path.exists(self.path):
            self._items = {}
            return
        try:
            with open(self.path, "r") as f:
                data = json.load(f)
            self._items = {}
            for name, loc in (data or {}).items():
                if isinstance(loc, dict) and "lat" in loc and "lon" in loc:
                    self._items[self.normalize(name)] = {
                        "name": loc.get("name", name),
                        "lat": float(loc["lat"]),
                        "lon": float(loc["lon"]),
                    }
            logger.info("Loaded %d saved location(s) from %s", len(self._items), self.path)
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.warning("Could not read locations file %s (%s); starting empty", self.path, e)
            self._items = {}

    def save(self) -> bool:
        """Persist the store to disk (atomic write: temp file then rename)."""
        if not self.path:
            return False
        try:
            directory = os.path.dirname(self.path) or "."
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".locations-", suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(self._items, f, indent=2)
            os.replace(tmp, self.path)
            return True
        except OSError as e:
            logger.warning("Could not save locations to %s: %s", self.path, e)
            return False

    # ------------------------------------------------------------------
    # Naming
    # ------------------------------------------------------------------
    @staticmethod
    def normalize(name: str) -> str:
        """Normalize a location name for use as a key / for matching."""
        return (name or "").strip().lower()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def add(self, name: str, lat: float, lon: float) -> bool:
        """Add (or update) a location. Returns False on a bad name/coords."""
        key = self.normalize(name)
        if not key:
            return False
        if not (-90.0 <= float(lat) <= 90.0) or not (-180.0 <= float(lon) <= 180.0):
            logger.warning("add(%r): coordinates out of range (lat/lon)", name)
            return False
        self._items[key] = {"name": name.strip(), "lat": float(lat), "lon": float(lon)}
        self.save()
        logger.info("Location %r set to (%.5f, %.5f)", key, lat, lon)
        return True

    def remove(self, name: str) -> bool:
        """Remove a location by name. Returns True if something was removed."""
        key = self.normalize(name)
        if key in self._items:
            del self._items[key]
            self.save()
            logger.info("Location %r removed", key)
            return True
        return False

    def get(self, name: str):
        """Return {"name","lat","lon"} for a location, or None if unknown."""
        return self._items.get(self.normalize(name))

    def names(self):
        """Normalized (key) names, sorted."""
        return sorted(self._items.keys())

    def all(self):
        """All locations as a list of {"name","lat","lon"} (display names)."""
        return [dict(v) for v in self._items.values()]

    def __len__(self):
        return len(self._items)

    def __contains__(self, name):
        return self.normalize(name) in self._items
