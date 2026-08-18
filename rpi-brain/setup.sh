#!/usr/bin/env bash
#
# setup.sh — one-shot installer for the Robot Owl on a fresh Raspberry Pi OS.
#
# Run it once after installing the OS (and wiring the owl):
#
#     cd rpi-brain
#     sudo ./setup.sh
#
# It will:
#   0. Pre-flight checks (apt present, required files present).
#   1. Install system packages (python3-venv, alsa-utils, rsync) via apt.
#   2. Enable I2S for the MAX98357A audio amp (dtoverlay=hifiberry-i2s-lite)
#      in the correct config.txt (handles /boot and Bookworm's /boot/firmware).
#   3. Install the brain to /opt/robot-owl, create a virtualenv, and install
#      the Python requirements.
#   4. Create a dedicated 'robotowl' system user in the 'dial' group and
#      install a udev rule so it can open the ESP32 USB CDC serial port.
#   5. Install + enable the systemd service.
#   6. Reboot to apply the I2S overlay. A one-shot boot hook starts the robot
#      on the next boot (and clears itself); the service is also enabled, so
#      the robot is running automatically when the Pi comes back.
#
# The script is idempotent — safe to re-run (re-running after the reboot just
# re-applies everything and clears the one-shot hook).
#
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # .../rpi-brain
DEST_DIR="/opt/robot-owl/rpi-brain"
UNIT_SRC="${SRC_DIR}/deploy/robot-owl-brain.service"
UDEV_SRC="${SRC_DIR}/deploy/99-robot-owl-serial.rules"
UDEV_DEST="/etc/udev/rules.d/99-robot-owl-serial.rules"
UNIT_DEST="/etc/systemd/system/robot-owl-brain.service"
SERVICE_USER="robotowl"
I2S_OVERLAY="hifiberry-i2s-lite"

log()  { printf '\n\033[1;32m[robot-owl]\033[0m %s\n' "$*"; }
info() { printf '\033[0;37m[robot-owl]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[robot-owl] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run with sudo:  sudo ./setup.sh"

# ----------------------------------------------------------------------------
log "0/6 Pre-flight checks"
# ----------------------------------------------------------------------------
command -v apt-get >/dev/null 2>&1 || die "apt-get not found — this script targets Raspberry Pi OS (Debian)."
grep -q "Raspberry Pi" /etc/os-release 2>/dev/null || info "Note: /etc/os-release doesn't say 'Raspberry Pi' — continuing anyway."
[ -f "${UNIT_SRC}" ] || die "missing ${UNIT_SRC}"
[ -f "${UDEV_SRC}" ] || die "missing ${UDEV_SRC}"
[ -f "${SRC_DIR}/requirements.txt" ] || die "missing ${SRC_DIR}/requirements.txt"

# ----------------------------------------------------------------------------
log "1/6 Installing system packages (python3-venv, alsa-utils, rsync)"
# ----------------------------------------------------------------------------
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3-venv python3-pip alsa-utils rsync

# ----------------------------------------------------------------------------
log "2/6 Enabling I2S for the MAX98357A audio amp"
# ----------------------------------------------------------------------------
# Bookworm and later use /boot/firmware; older images use /boot. Pick whichever
# exists (and is not just a symlink to the other).
CONFIG_FILE=""
for candidate in /boot/firmware/config.txt /boot/config.txt; do
    if [ -f "${candidate}" ]; then CONFIG_FILE="${candidate}"; break; fi
done
[ -n "${CONFIG_FILE}" ] || die "could not find config.txt (looked in /boot/firmware and /boot)"
info "Using ${CONFIG_FILE}"

if grep -q "^dtoverlay=${I2S_OVERLAY}" "${CONFIG_FILE}"; then
    info "I2S overlay already enabled."
else
    info "Adding 'dtoverlay=${I2S_OVERLAY}' to ${CONFIG_FILE}"
    echo "dtoverlay=${I2S_OVERLAY}" >> "${CONFIG_FILE}"
fi

# ----------------------------------------------------------------------------
log "3/6 Installing the brain to ${DEST_DIR} + virtualenv"
# ----------------------------------------------------------------------------
mkdir -p "${DEST_DIR}"
rsync -a --delete \
    --exclude '.venv' --exclude '__pycache__' --exclude '*.pyc' --exclude 'deploy' \
    "${SRC_DIR}/" "${DEST_DIR}/"

python3 -m venv "${DEST_DIR}/.venv"
"${DEST_DIR}/.venv/bin/pip" install --upgrade pip >/dev/null
"${DEST_DIR}/.venv/bin/pip" install -r "${DEST_DIR}/requirements.txt"

# ----------------------------------------------------------------------------
log "4/6 Creating '${SERVICE_USER}' user + udev rule for the ESP32 serial port"
# ----------------------------------------------------------------------------
if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
    info "Created system user '${SERVICE_USER}'."
fi
# Make sure the 'dial' group exists (it does on stock Pi OS, but be safe).
getent group dial >/dev/null 2>&1 || groupadd dial
usermod -aG dial "${SERVICE_USER}"
chown -R "${SERVICE_USER}:" "${DEST_DIR}"

install -m 0644 "${UDEV_SRC}" "${UDEV_DEST}"
udevadm control --reload-rules || true
# Apply to any port that is already plugged in right now.
udevadm trigger --subsystem-match=usb 2>/dev/null || true
udevadm settle 2>/dev/null || true

# ----------------------------------------------------------------------------
log "5/6 Installing + enabling the systemd service"
# ----------------------------------------------------------------------------
install -m 0644 "${UNIT_SRC}" "${UNIT_DEST}"
sed -i "s|^ExecStart=.*|ExecStart=${DEST_DIR}/.venv/bin/python main.py ${DEST_DIR}/config.yaml|" "${UNIT_DEST}"
systemctl daemon-reload
systemctl enable robot-owl-brain.service
info "Service enabled (will auto-start on boot)."

# ----------------------------------------------------------------------------
log "6/6 Rebooting to apply the I2S overlay"
# ----------------------------------------------------------------------------
# The service is already enabled, so it starts automatically after the reboot.
# We also drop a one-shot boot hook so that, even if you had the service
# disabled, the robot is guaranteed to be running when the Pi comes back.
# The hook removes itself after it has run once.
BOOT_HOOK="/etc/systemd/system/robot-owl-brain-startup.service"
cat > "${BOOT_HOOK}" <<'EOF'
[Unit]
Description=One-shot: start the Robot Owl brain after I2S setup reboot
After=multi-user.target

[Service]
Type=oneshot
RemainAfterExit=no
# Give the USB CDC serial port + I2S a moment to come up, then start the brain.
ExecStart=/bin/sh -c 'sleep 5; systemctl start robot-owl-brain.service'

[Install]
WantedBy=multi-user.target
EOF

info "The I2S overlay needs a reboot to take effect."
info "After the reboot the robot starts automatically (no further action needed)."
info "Rebooting in 10s — press Ctrl-C to cancel."
sleep 10
# Stop the service (if running pre-reboot) so it restarts cleanly post-reboot.
systemctl stop robot-owl-brain.service 2>/dev/null || true
systemctl enable robot-owl-brain-startup.service 2>/dev/null || true
reboot

# NOTE: the lines below only run if you re-execute this script after the
# reboot (e.g. 'sudo ./setup.sh' again). Normally you just log in and the
# robot is already running. Re-running is harmless and cleans up the hook.
info "Back up — starting the robot brain and clearing the one-shot hook..."
sleep 5
systemctl start robot-owl-brain.service 2>/dev/null || true
# Clear the one-shot startup hook now that the main service is confirmed up.
systemctl disable robot-owl-brain-startup.service 2>/dev/null || true
rm -f "${BOOT_HOOK}"
systemctl daemon-reload
sleep 3
systemctl --no-pager status robot-owl-brain.service || true

log "Setup complete."
log "  Watch logs:    journalctl -u robot-owl-brain -f"
log "  Status:     systemctl status robot-owl-brain"
log "  Stop:       systemctl stop robot-owl-brain"
log "  Web UI:     set 'web.enabled: true' in ${DEST_DIR}/config.yaml, then open"
log "               http://<pi-ip>:8080 (LAN only, no auth)."
log "  Serial:     if the port is not /dev/ttyACM0, edit 'serial.port' in"
log "               ${DEST_DIR}/config.yaml (check with: ls /dev/ttyACM*)."
log "  Audio:      run 'aplay -l' — it should list a bcm2835 device. If silent,"
log "               confirm the amp's SD MODE pin is tied to 3.3V (WIRING.md)."
