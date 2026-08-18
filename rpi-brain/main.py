"""
Robot Owl RPi Brain - Main Entry Point

Starts the serial communication loop and supervisor. The ESP32 owns the
behavior state machine; the RPi supervisor only logs telemetry and can send
policy commands (sleep/wake) and temporary overrides.
"""

import sys
import os
import yaml
import logging

# Add project root to path (before importing brain modules)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from brain.serial_handler import SerialHandler
from brain.supervisor import Supervisor
from brain.banner import print_banner
from brain.audio import Audio


def load_config(config_path: str = "config.yaml") -> dict:
    """Load configuration from YAML file"""
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def main():
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    )
    logger = logging.getLogger(__name__)

    # Load configuration
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    try:
        config = load_config(config_path)
    except FileNotFoundError:
        logger.error(f"Configuration file not found: {config_path}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        sys.exit(1)

    serial_cfg = config['serial']
    web_cfg = config.get('web', {})

    # Initialize serial handler
    serial = SerialHandler(
        port=serial_cfg['port'],
        baudrate=serial_cfg['baudrate'],
        timeout=serial_cfg['timeout']
    )

    if not serial.connect():
        logger.error("Failed to connect to ESP32. Check serial port.")
        sys.exit(1)

    # Initialize audio (MAX98357A I2S amp). Degrades to a no-op if the amp
    # or I2S is not available, so the brain still runs without sound.
    audio = Audio(config)

    # Initialize supervisor (audio is used for state-change sound cues).
    supervisor = Supervisor(serial, config, audio=audio)

    # Startup banner (printed to stdout; also mirrored to the log).
    print_banner(
        firmware=supervisor._last_fw or "",
        port=serial_cfg['port'],
        baudrate=serial_cfg['baudrate'],
        config_path=config_path,
        web_ui=bool(web_cfg.get('enabled', False)),
        audio=bool(audio.enabled and audio._ready),
    )
    logger.info("Robot Owl Brain started (supervisor mode); waiting for telemetry...")

    # Optional web UI for manually testing features. Runs in a daemon thread
    # so the serial read loop (below) stays the foreground loop.
    web_server = None
    if web_cfg.get('enabled', False):
        try:
            from brain.web_ui import WebUI
            web_server = WebUI(
                serial,
                supervisor,
                host=web_cfg.get('host', '0.0.0.0'),
                port=web_cfg.get('port', 8080),
            )
            web_server.start()
        except Exception as e:
            logger.error(f"Web UI failed to start (continuing without it): {e}")
            web_server = None

    try:
        # Start the serial read loop. The idle callback runs on quiet
        # iterations so we can warn when telemetry goes stale.
        serial.read_loop(supervisor.on_telemetry, supervisor.check_stale)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        if web_server:
            web_server.stop()
        serial.disconnect()
        logger.info("Disconnected from ESP32")


if __name__ == "__main__":
    main()
