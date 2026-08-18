"""
Robot Owl RPi Brain - Serial Communication Handler

Handles NDJSON protocol communication with ESP32-S3 firmware.
Receives telemetry and face detection data, sends commands back.
"""

import serial
import json
import time
import logging
from typing import Callable, Optional, Dict, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class FaceDetection:
    """Face detection result from ESP32"""
    detected: bool = False
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    confidence: float = 0.0
    gaze_x: float = 0.0
    gaze_y: float = 0.0


@dataclass
class IMUData:
    """IMU sensor data"""
    pitch: float = 0.0
    roll: float = 0.0
    yaw: float = 0.0
    calibrated: bool = False


@dataclass
class GPSData:
    """GPS sensor data"""
    valid: bool = False
    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0
    satellites: int = 0


@dataclass
class VibrationData:
    """Vibration sensor data"""
    detected: bool = False
    count: int = 0


@dataclass
class UpdateMode:
    """Firmware update mode (SoftAP + /update HTTP server).

    Populated only while the owl is in the UPDATE state (entered by a
    4-tap vibration sequence). The owl is on an isolated SoftAP during this
    time, so the RPi can no longer reach it over the normal USB serial link.
    """
    active: bool = False
    ssid: str = ""
    password: str = ""
    ip: str = ""
    url: str = ""


@dataclass
class Telemetry:
    """Complete telemetry frame from ESP32"""
    timestamp: float = field(default_factory=time.time)
    state: str = "idle"
    uptime_ms: int = 0
    firmware: str = ""
    imu: IMUData = field(default_factory=IMUData)
    gps: GPSData = field(default_factory=GPSData)
    vibration: VibrationData = field(default_factory=VibrationData)
    update: UpdateMode = field(default_factory=UpdateMode)
    servos: list = field(default_factory=lambda: [0.0] * 5)
    face: FaceDetection = field(default_factory=FaceDetection)
    eye_expression: str = "neutral"


class SerialHandler:
    """Handles serial communication with ESP32-S3"""

    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 1.0):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.serial: Optional[serial.Serial] = None
        self._buffer = ""
        self._telemetry_callback: Optional[Callable[[Telemetry], None]] = None
        self._heartbeat_callback: Optional[Callable[[], None]] = None

    def connect(self) -> bool:
        """Connect to ESP32 via serial"""
        try:
            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout
            )
            time.sleep(2)  # Wait for ESP32 to boot
            logger.info(f"Connected to ESP32 on {self.port}")
            return True
        except serial.SerialException as e:
            logger.error(f"Failed to connect to ESP32: {e}")
            return False

    def disconnect(self):
        """Disconnect from ESP32"""
        if self.serial and self.serial.is_open:
            self.serial.close()
            logger.info("Disconnected from ESP32")

    def send_command(self, command: Dict[str, Any]) -> bool:
        """Send a JSON command to ESP32"""
        if not self.serial or not self.serial.is_open:
            logger.error("Serial not connected")
            return False

        try:
            json_str = json.dumps(command) + "\n"
            self.serial.write(json_str.encode('utf-8'))
            logger.debug(f"Sent command: {json_str.strip()}")
            return True
        except serial.SerialException as e:
            logger.error(f"Failed to send command: {e}")
            return False

    def set_expression(self, expression: str) -> bool:
        """Set eye expression on ESP32"""
        return self.send_command({"type": "expression", "value": expression})

    def set_servo(self, channel: int, angle: float) -> bool:
        """Set servo angle (channel 0-4, angle -45 to 45)"""
        return self.send_command({
            "type": "servo",
            "channel": channel,
            "angle": angle
        })

    def set_gaze(self, x: float, y: float) -> bool:
        """Set gaze direction (x, y in range -1.0 to 1.0)"""
        return self.send_command({
            "type": "gaze",
            "x": x,
            "y": y
        })

    def wake(self) -> bool:
        """Wake up ESP32 from sleep"""
        return self.send_command({"type": "wake"})

    def blink(self, speed: int = 3) -> bool:
        """Trigger blink animation"""
        return self.send_command({"type": "blink", "speed": speed})

    def heartbeat(self) -> bool:
        """Send heartbeat request"""
        return self.send_command({"type": "heartbeat"})

    def parse_telemetry(self, json_str: str) -> Optional[Telemetry]:
        """Parse a telemetry JSON string into Telemetry object"""
        try:
            data = json.loads(json_str)
            if data.get("type") != "telemetry":
                return None

            telemetry = Telemetry()
            telemetry.timestamp = time.time()
            telemetry.state = data.get("state", "idle")
            telemetry.uptime_ms = data.get("uptime", 0)
            telemetry.firmware = data.get("fw", "")
            telemetry.eye_expression = data.get("eye", "neutral")

            # Parse IMU
            imu_data = data.get("imu", {})
            telemetry.imu.pitch = imu_data.get("pitch", 0.0)
            telemetry.imu.roll = imu_data.get("roll", 0.0)
            telemetry.imu.yaw = imu_data.get("yaw", 0.0)
            telemetry.imu.calibrated = imu_data.get("calibrated", False)

            # Parse GPS
            gps_data = data.get("gps", {})
            telemetry.gps.valid = gps_data.get("valid", False)
            telemetry.gps.latitude = gps_data.get("latitude", 0.0)
            telemetry.gps.longitude = gps_data.get("longitude", 0.0)
            telemetry.gps.altitude = gps_data.get("altitude", 0.0)
            telemetry.gps.satellites = gps_data.get("satellites", 0)

            # Parse vibration
            vib_data = data.get("vibration", {})
            telemetry.vibration.detected = vib_data.get("detected", False)
            telemetry.vibration.count = vib_data.get("count", 0)

            # Parse update mode (present only while the owl is in UPDATE state)
            update_data = data.get("update")
            if update_data:
                telemetry.update.active = True
                telemetry.update.ssid = update_data.get("ssid", "")
                telemetry.update.password = update_data.get("password", "")
                telemetry.update.ip = update_data.get("ip", "")
                telemetry.update.url = update_data.get("url", "")

            # Parse servos
            telemetry.servos = data.get("servos", [0.0] * 5)

            # Parse face detection
            face_data = data.get("face", {})
            telemetry.face.detected = face_data.get("detected", False)
            telemetry.face.x = face_data.get("x", 0)
            telemetry.face.y = face_data.get("y", 0)
            telemetry.face.w = face_data.get("w", 0)
            telemetry.face.h = face_data.get("h", 0)
            telemetry.face.confidence = face_data.get("confidence", 0.0)
            telemetry.face.gaze_x = face_data.get("gaze_x", 0.0)
            telemetry.face.gaze_y = face_data.get("gaze_y", 0.0)

            return telemetry

        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Failed to parse telemetry: {e}")
            return None

    def _handle_message(self, line: str, callback: Optional[Callable[[Telemetry], None]] = None):
        """Dispatch a single NDJSON line by its 'type' field.

        Telemetry frames are parsed into a Telemetry object and passed to the
        callback. All other message types (boot, update_mode, *_ack) are logged
        here instead of being silently dropped.
        """
        try:
            data = json.loads(line)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse message: {e}")
            return

        msg_type = data.get("type")

        if msg_type == "telemetry":
            telemetry = self.parse_telemetry(line)
            if telemetry:
                logger.debug(
                    f"Telemetry: state={telemetry.state}, "
                    f"face_detected={telemetry.face.detected}"
                )
                if callback:
                    callback(telemetry)
        elif msg_type == "boot":
            logger.info("Owl booted: %s", data.get("msg", "ready"))
        elif msg_type == "update_mode":
            logger.info(
                "Owl entered UPDATE mode: join WiFi '%s' (password '%s') "
                "and open %s to flash firmware. Tap the owl once to exit.",
                data.get("ssid", ""),
                data.get("password", ""),
                data.get("url", ""),
            )
        elif msg_type == "update_mode_end":
            logger.info("Owl exited UPDATE mode; normal operation resumed.")
        elif msg_type == "error":
            logger.error("Owl reported error: %s", data.get("msg", "unknown"))
        elif msg_type and msg_type.endswith("_ack"):
            logger.debug("Owl ack: %s %s", data)
        else:
            logger.debug("Owl message (unhandled): %s", data)

    def read_loop(self, callback: Optional[Callable[[Telemetry], None]] = None):
        """Main read loop - processes incoming serial data"""
        self._telemetry_callback = callback

        if not self.serial or not self.serial.is_open:
            logger.error("Serial not connected")
            return

        logger.info("Starting serial read loop...")

        while self.serial.is_open:
            try:
                # Read available data
                data = self.serial.read(self.serial.in_waiting or 1)
                if not data:
                    continue

                text = data.decode('utf-8', errors='ignore')
                self._buffer += text

                # Process complete lines
                while '\n' in self._buffer:
                    line, self._buffer = self._buffer.split('\n', 1)
                    line = line.strip()

                    if not line:
                        continue

                    # Dispatch by message type (telemetry + acks/boot/update)
                    self._handle_message(line, callback)

            except serial.SerialException as e:
                logger.error(f"Serial read error: {e}")
                break
            except Exception as e:
                logger.error(f"Unexpected error in read loop: {e}")
                break
