#!/usr/bin/env bash
#
# install.sh - install the Robot Owl Brain as a systemd service on the RPi.
#
# What it does:
#   1. Copies this rpi-brain/ tree to /opt/robot-owl/rpi-brain (idempotent).
#   2. Creates a virtualenv at /opt/robot-owl/rpi-brain/.venv and installs
#      requirements.txt into it.
#   3. Creates a dedicated 'robotowl' user (system user, no login) and adds it
#      to the 'dial' group for serial port access.
#   4. Installs the udev rule for the ESP32 USB CDC port.
#   5. Installs + enables the systemd unit.
#
# Run as root (or with sudo). Re-running is safe.
#
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # .../rpi-brain
DEST_DIR="/opt/robot-owl/rpi-brain"
UNIT_SRC="${SRC_DIR}/deploy/robot-owl-brain.service"
UDEV_SRC="${SRC_DIR}/deploy/99-robot-owl-serial.rules"
UDEV_DEST="/etc/udev/rules.d/99-robot-owl-serial.rules"
UNIT_DEST="/etc/systemd/system/robot-owl-brain.service"
SERVICE_USER="robotowl"

log() { printf '\n\033[1;32m[robot-owl]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[robot-owl] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run with sudo (needs root for user/udev/systemd)"

log "1/5 Copying rpi-brain to ${DEST_DIR}"
mkdir -p "${DEST_DIR}"
# Copy source (not the venv, not build junk)
rsync -a --delete \
    --exclude '.venv' --exclude '__pycache__' --exclude '*.pyc' \
    "${SRC_DIR}/" "${DEST_DIR}/"

log "2/5 Creating virtualenv + installing requirements"
python3 -m venv "${DEST_DIR}/.venv"
"${DEST_DIR}/.venv/bin/pip" install --upgrade pip >/dev/null
"${DEST_DIR}/.venv/bin/pip" install -r "${DEST_DIR}/requirements.txt"

log "3/5 Ensuring '${SERVICE_USER}' user exists (in dial group)"
if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
    chown -R "${SERVICE_USER}:" "${DEST_DIR}"
fi
usermod -aG dial "${SERVICE_USER}" 2>/dev/null || true

log "4/5 Installing udev rule for the ESP32 serial port"
install -m 0644 "${UDEV_SRC}" "${UDEV_DEST}"
systemctl daemon-reload
udevadm control --reload-rules || true

log "5/5 Installing + enabling systemd unit"
install -m 0644 "${UNIT_SRC}" "${UNIT_DEST}"
sed -i "s|^ExecStart=.*|ExecStart=${DEST_DIR}/.venv/bin/python main.py ${DEST_DIR}/config.yaml|" "${UNIT_DEST}"
systemctl daemon-reload
systemctl enable robot-owl-brain.service

log "Done."
log "Start now:        systemctl start robot-owl-brain"
log "Watch logs:       journalctl -u robot-owl-brain -f"
log "Status:           systemctl status robot-owl-brain"
log "If the port is not /dev/ttyACM0, edit ${DEST_DIR}/config.yaml 'serial.port'."
