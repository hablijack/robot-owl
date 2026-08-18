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
        logger.info(f"Loaded configuration from {config_path}")
    except FileNotFoundError:
        logger.error(f"Configuration file not found: {config_path}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        sys.exit(1)

    # Initialize serial handler
    serial = SerialHandler(
        port=config['serial']['port'],
        baudrate=config['serial']['baudrate'],
        timeout=config['serial']['timeout']
    )

    if not serial.connect():
        logger.error("Failed to connect to ESP32. Check serial port.")
        sys.exit(1)

    # Initialize supervisor
    supervisor = Supervisor(serial, config)

    logger.info("Robot Owl Brain started (supervisor mode)")
    logger.info("ESP32 owns behavior; waiting for telemetry...")

    try:
        # Start the serial read loop
        serial.read_loop(supervisor.on_telemetry)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        serial.disconnect()
        logger.info("Disconnected from ESP32")


if __name__ == "__main__":
    main()
