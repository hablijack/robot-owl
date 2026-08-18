"""
Robot Owl RPi Brain - Startup banner

Prints a small ASCII owl plus the key runtime facts (firmware version, serial
port, config path) once at launch. The text is written straight to stdout so
it shows up cleanly in `journalctl` / a terminal, independent of the logging
configuration.
"""

import logging

logger = logging.getLogger(__name__)

# Kept within 80 columns so it doesn't wrap in a terminal or journal output.
_OWL = r"""
        .--.
        |oO|
       /oY\
       \Y/
     //  \\
    //    \\
"""


def print_banner(
    firmware: str,
    port: str,
    baudrate: int,
    config_path: str,
    web_ui: bool = False,
    audio: bool = False,
) -> None:
    """Print the startup banner to stdout."""
    lines = [
        _OWL,
        "  ROBOT OWL  -  RPi Brain (supervisor)",
        "  " + "-" * 40,
        f"  firmware : {firmware or 'unknown'}",
        f"  serial   : {port} @ {baudrate} baud",
        f"  config   : {config_path}",
        f"  audio    : {'on (I2S amp)' if audio else 'off'}",
    ]
    if web_ui:
        lines.append("  web ui   : enabled")
    lines.append("  " + "-" * 40)
    lines.append("  ESP32 owns behavior; brain supervises + tests.")
    print("\n".join(lines))
    logger.info("Banner printed at startup")
