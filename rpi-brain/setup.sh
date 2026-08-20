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
#   1. Install system packages (python3-venv, alsa-utils, rsync, portaudio) via apt.
#   2. Enable I2S for the MAX98357A audio amp (dtoverlay=hifiberry-i2s-lite)
#      in the correct config.txt (handles /boot and Bookworm's /boot/firmware).
#   3. Run an INTERACTIVE CONFIG WIZARD: it asks a few questions (serial port,
#      web UI, speech, auto-sleep) and writes /opt/robot-owl/rpi-brain/config.yaml
#      with your choices (useful defaults are pre-filled — Enter accepts).
#   4. Install the brain to /opt/robot-owl, create a virtualenv, and install
#      the Python requirements (faster-whisper + ctranslate2, no torch).
#   5. Pre-download the Whisper model (from Hugging Face) so the first real
#      transcription is instant instead of a several-minute download.
#   6. Create a dedicated 'robotowl' system user in the 'dial' group and
#      install a udev rule so it can open the ESP32 USB CDC serial port.
#   7. Install + enable the systemd service.
#   8. Reboot to apply the I2S overlay. A one-shot boot hook starts the robot
#      on the next boot (and clears itself); the service is also enabled, so
#      the robot is running automatically when the Pi comes back.
#
# The script is idempotent — safe to re-run (re-running after the reboot just
# re-applies everything and clears the one-shot hook).
#
# Non-interactive use (e.g. unattended / scripted installs):
#     sudo ./setup.sh --non-interactive
#   skips the wizard and keeps the bundled config.yaml defaults.
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
# Options: --non-interactive skips the config wizard (keeps bundled defaults).
# ----------------------------------------------------------------------------
NON_INTERACTIVE=0
for arg in "$@"; do
    case "${arg}" in
        --non-interactive|-n) NON_INTERACTIVE=1 ;;
        *) die "unknown argument '${arg}' (only --non-interactive is supported)" ;;
    esac
done
# The wizard prompts on /dev/tty so it still works under `sudo` (stdin is not
# the user's terminal there). If there is no TTY (cron/CI), fall back to
# non-interactive automatically.
if [ "${NON_INTERACTIVE}" -eq 1 ]; then
    INTERACTIVE=0
elif [ -t /dev/tty ]; then
    INTERACTIVE=1
else
    info "No TTY detected — running non-interactive (bundled config defaults)."
    INTERACTIVE=0
fi

# ----------------------------------------------------------------------------
# Interactive config wizard (runs BEFORE the rsync so it edits the source
# config.yaml, which is then copied to /opt/robot-owl).
# ----------------------------------------------------------------------------
ask() {
    # ask <prompt-with-default> <varname>   ->  sets the variable to the answer
    local prompt="${1}"
    local varname="${2}"
    local default
    default=$(printf '%s' "${prompt}" | sed -n 's/.* \[\(.*\)\]$/\1/p')
    local reply
    if [ -t /dev/tty ]; then
        # Read from /dev/tty (via stdin redirect) so the prompt still works
        # under `sudo` (whose stdin is not the user's terminal). -t 180 is a
        # safety net so a forgotten prompt can't hang an unattended run.
        # (We avoid `read -u /dev/tty`: on Linux /dev/tty is a path, but on
        # macOS it is a device — `read -u` wants a numeric fd, so the redirect
        # form is the portable one.)
        read -r -t 180 -p "${prompt} " reply < /dev/tty || reply="${default}"
    else
        reply="${default}"
    fi
    [ -z "${reply}" ] && reply="${default}"
    printf -v "${varname}" '%s' "${reply}"
}

# ----------------------------------------------------------------------------
run_config_wizard() {
    log "Config wizard (Enter accepts the default shown in [brackets])"

    # ---- Serial: discover the ESP32 USB CDC port ---------------------------
    local port="/dev/ttyACM0"
    local first
    first=$(ls /dev/ttyACM* 2>/dev/null | head -1)
    if [ -n "${first}" ]; then
        port="${first}"
        info "Detected USB CDC port: ${first}"
    else
        info "No /dev/ttyACM* device found right now (that's fine if the ESP32"
        info "is not plugged in yet)."
    fi
    if [ "${INTERACTIVE}" -eq 1 ]; then
        ask "Serial port for the ESP32 [${port}]" SERIAL_PORT
        ask "Serial baudrate [115200]" BAUDRATE
        ask "Serial read timeout (seconds) [1]" SERIAL_TIMEOUT
    else
        SERIAL_PORT="${port}"; BAUDRATE=115200; SERIAL_TIMEOUT=1
    fi

    # ---- Web UI -------------------------------------------------------------
    if [ "${INTERACTIVE}" -eq 1 ]; then
        ask "Enable the web UI (test panel at http://<pi-ip>:8080)? [y]" WEB_ENABLED
        ask "Web UI port [8080]" WEB_PORT
    else
        WEB_ENABLED="n"; WEB_PORT=8080
    fi

    # ---- Speech recognition -------------------------------------------------
    local mic=""
    # A USB mic shows up as an ALSA card. Try to spot it (a "USB PnP" / "USB Audio"
    # card, or any card that is not the I2S amp 'bcm2835-I2S').
    if command -v arecord >/dev/null 2>&1; then
        mic=$(arecord -l 2>/dev/null | grep -iE "USB|PnP|Audio" | grep -iv "bcm2835" | head -1 \
             | sed -E 's/.*card[[:space:]]+[0-9]+.*//' | tr -d ' ')
    fi
    local speech_enabled="y"
    if [ "${INTERACTIVE}" -eq 1 ]; then
        if [ -n "${mic}" ]; then
            info "Detected a USB audio card (mic): ${mic}"
        else
            info "No USB mic detected yet — plug one in, or choose the device"
            info "index from 'arecord -l' after plugging it in."
        fi
        ask "Enable speech recognition (USB mic -> Whisper)? [y]" SPEECH_ENABLED
    else
        SPEECH_ENABLED="n"
    fi
    local speech_model="tiny" speech_lang="de" speech_mic="${mic}"
    if [ "${SPEECH_ENABLED}" = "y" ] && [ "${INTERACTIVE}" -eq 1 ]; then
        ask "Whisper model size (tiny = fastest; base = a bit better) [tiny]" speech_model
        ask "Spoken language (ISO code) [de]" speech_lang
        ask "Mic device (ALSA card from 'arecord -l'; blank = default) [${mic}]" speech_mic
    fi
    # Keep a valid model size + language even if speech was just disabled, so the
    # pre-download step (and a later re-enable) still have sensible values.
    [ -z "${speech_model}" ] && speech_model="tiny"
    [ -z "${speech_lang}" ] && speech_lang="de"

    # ---- Autonomous sleep (Phase 4) ----------------------------------------
    local auto_sleep_enabled="y" auto_sleep_after=60
    if [ "${INTERACTIVE}" -eq 1 ]; then
        ask "Owl falls asleep automatically when idle? [y]" auto_sleep_enabled
        if [ "${auto_sleep_enabled}" = "y" ]; then
            ask "Sleep after how many seconds with no face / tap / speech? [60]" auto_sleep_after
        fi
    fi

    # ---- Apply the choices to the source config.yaml -----------------------
    info "Writing choices to ${SRC_DIR}/config.yaml ..."
    _set_serial "${SERIAL_PORT}" "${BAUDRATE}" "${SERIAL_TIMEOUT}"
    _set_web "${WEB_ENABLED}" "${WEB_PORT}"
    _set_speech "${SPEECH_ENABLED}" "${speech_model}" "${speech_lang}" "${speech_mic}"
    _set_auto_sleep "${auto_sleep_enabled}" "${auto_sleep_after}"

    # Show what we set.
    info "Config summary:"
    echo "    serial.port        = ${SERIAL_PORT} (baud ${BAUDRATE}, timeout ${SERIAL_TIMEOUT}s)"
    echo "    web.enabled        = ${WEB_ENABLED} (port ${WEB_PORT})"
    echo "    speech.enabled     = ${SPEECH_ENABLED}"
    if [ "${SPEECH_ENABLED}" = "y" ]; then
        echo "    speech.model       = ${speech_model} (lang ${speech_lang}, mic ${speech_mic:-default})"
    fi
    echo "    auto_sleep.enabled = ${auto_sleep_enabled}"
    if [ "${auto_sleep_enabled}" = "y" ]; then
        echo "    auto_sleep.after_s   = ${auto_sleep_after}"
    fi
}

# (Nested-key setters. Kept as helpers so run_config_wizard stays readable.)
_set_serial() {
    local file="${SRC_DIR}/config.yaml"
    local block_re="^serial:"
    local start end
    start=$(grep -n "${block_re}" "${file}" | head -1 | cut -d: -f1)
    end=$(awk -v s="${start}" 'NR>s && /^[^[:space:]]/ { print NR; exit }' "${file}")
    [ -z "${end}" ] && end=$(wc -l < "${file}")
    awk -v s="${start}" -v e="${end}" -v p="$1" -v b="$2" -v t="$3" '
        NR>=s+1 && NR<e {
            if ($0 ~ /^  port:/)            { print "  port: " p; next }
            if ($0 ~ /^  baudrate:/)        { print "  baudrate: " b; next }
            if ($0 ~ /^  timeout:/)         { print "  timeout: " t; next }
        } { print }' "${file}" > "${file}.tmp" && mv "${file}.tmp" "${file}"
}
_set_web() {
    local file="${SRC_DIR}/config.yaml"
    local enabled="$1" port="$2"
    local start end
    start=$(grep -n "^web:" "${file}" | head -1 | cut -d: -f1)
    end=$(awk -v s="${start}" 'NR>s && /^[^[:space:]]/ { print NR; exit }' "${file}")
    [ -z "${end}" ] && end=$(wc -l < "${file}")
    awk -v s="${start}" -v e="${end}" -v en="${enabled}" -v p="${port}" '
        NR>=s+1 && NR<e {
            if ($0 ~ /^  enabled:/) { print "  enabled: " en; next }
            if ($0 ~ /^  port:/)    { print "  port: " p; next }
        } { print }' "${file}" > "${file}.tmp" && mv "${file}.tmp" "${file}"
}
_set_speech() {
    local file="${SRC_DIR}/config.yaml"
    local enabled="$1" model="$2" lang="$3" mic="$4"
    local start end
    start=$(grep -n "^speech:" "${file}" | head -1 | cut -d: -f1)
    end=$(awk -v s="${start}" 'NR>s && /^[^[:space:]]/ { print NR; exit }' "${file}")
    [ -z "${end}" ] && end=$(wc -l < "${file}")
    awk -v s="${start}" -v e="${end}" -v en="${enabled}" -v m="${model}" -v l="${lang}" -v mic="${mic}" '
        NR>=s+1 && NR<e {
            if ($0 ~ /^  enabled:/)      { print "  enabled: " en; next }
            if ($0 ~ /^  model:/)        { print "  model: " m; next }
            if ($0 ~ /^  language:/)     { print "  language: " l; next }
            if ($0 ~ /^  mic_device:/)   { print "  mic_device: \"" mic "\""; next }
        } { print }' "${file}" > "${file}.tmp" && mv "${file}.tmp" "${file}"
}
_set_auto_sleep() {
    local file="${SRC_DIR}/config.yaml"
    local enabled="$1" after="$2"
    local start end
    start=$(grep -n "^supervisor:" "${file}" | head -1 | cut -d: -f1)
    end=$(awk -v s="${start}" 'NR>s && /^[^[:space:]]/ { print NR; exit }' "${file}")
    [ -z "${end}" ] && end=$(wc -l < "${file}")
    awk -v s="${start}" -v e="${end}" -v en="${enabled}" -v a="${after}" '
        NR>=s+1 && NR<e {
            if ($0 ~ /^    enabled:/) { print "    enabled: " en; next }
            if ($0 ~ /^    after_s:/) { print "    after_s: " a; next }
        } { print }' "${file}" > "${file}.tmp" && mv "${file}.tmp" "${file}"
}

# ----------------------------------------------------------------------------
log "0/8 Pre-flight checks"
# ----------------------------------------------------------------------------
command -v apt-get >/dev/null 2>&1 || die "apt-get not found — this script targets Raspberry Pi OS (Debian)."
grep -q "Raspberry Pi" /etc/os-release 2>/dev/null || info "Note: /etc/os-release doesn't say 'Raspberry Pi' — continuing anyway."
[ -f "${UNIT_SRC}" ] || die "missing ${UNIT_SRC}"
[ -f "${UDEV_SRC}" ] || die "missing ${UDEV_SRC}"
[ -f "${SRC_DIR}/requirements.txt" ] || die "missing ${SRC_DIR}/requirements.txt"
[ -f "${SRC_DIR}/config.yaml" ] || die "missing ${SRC_DIR}/config.yaml (the config wizard needs it)"

# ----------------------------------------------------------------------------
log "1/8 Installing system packages (python3-venv, alsa-utils, rsync, portaudio)"
# ----------------------------------------------------------------------------
# portaudio19-dev provides the PortAudio library that the 'sounddevice' Python
# package (USB mic capture for speech recognition) links against.
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3-venv python3-pip alsa-utils rsync portaudio19-dev

# ----------------------------------------------------------------------------
log "2/8 Enabling I2S for the MAX98357A audio amp"
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
log "3/8 Config wizard — setting your defaults"
# ----------------------------------------------------------------------------
run_config_wizard

# ----------------------------------------------------------------------------
log "4/8 Installing the brain to ${DEST_DIR} + virtualenv"
# ----------------------------------------------------------------------------
mkdir -p "${DEST_DIR}"
rsync -a --delete \
    --exclude '.venv' --exclude '__pycache__' --exclude '*.pyc' --exclude 'deploy' \
    "${SRC_DIR}/" "${DEST_DIR}/"

python3 -m venv "${DEST_DIR}/.venv"
"${DEST_DIR}/.venv/bin/pip" install --upgrade pip >/dev/null
"${DEST_DIR}/.venv/bin/pip" install -r "${DEST_DIR}/requirements.txt"

# ----------------------------------------------------------------------------
log "5/8 Pre-downloading the Whisper model (first run won't stall)"
# ----------------------------------------------------------------------------
# faster-whisper loads the model from the Hugging Face Hub on first use. We
# do that now (as the robotowl user, into its home) so the live service starts
# with the model already cached. Read the chosen model size from the installed
# config (the wizard just wrote it).
WHISPER_MODEL=$("${DEST_DIR}/.venv/bin/python" - "$DEST_DIR/config.yaml" <<'PY'
import sys
try:
    import yaml
    with open(sys.argv[1]) as f:
        cfg = yaml.safe_load(f) or {}
    print((cfg.get("speech", {}) or {}).get("model", "tiny"))
except Exception:
    print("tiny")
PY
)
if [ -n "${WHISPER_MODEL}" ]; then
    info "Pre-downloading faster-whisper model '${WHISPER_MODEL}' (CPU/int8) ..."
    # Run as robotowl so the cache lands in the user that runs the service.
    # (robotowl is created in the next step; create it first if needed.)
    id -u "${SERVICE_USER}" >/dev/null 2>&1 || \
        useradd --system --no-create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
    HOME="/home/${SERVICE_USER}" "${DEST_DIR}/.venv/bin/python" - "${WHISPER_MODEL}" <<'PY' || \
        info "Model pre-download skipped/failed (it will download on first use instead)."
import sys
from faster_whisper import WhisperModel
WhisperModel(sys.argv[1], device="cpu", compute_type="int8")
print("Model ready.")
PY
fi

# ----------------------------------------------------------------------------
log "6/8 Creating '${SERVICE_USER}' user + udev rule for the ESP32 serial port"
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
log "7/8 Installing + enabling the systemd service"
# ----------------------------------------------------------------------------
install -m 0644 "${UNIT_SRC}" "${UNIT_DEST}"
sed -i "s|^ExecStart=.*|ExecStart=${DEST_DIR}/.venv/bin/python main.py ${DEST_DIR}/config.yaml|" "${UNIT_DEST}"
systemctl daemon-reload
systemctl enable robot-owl-brain.service
info "Service enabled (will auto-start on boot)."

# ----------------------------------------------------------------------------
log "8/8 Rebooting to apply the I2S overlay"
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
log "  Web UI:     if you enabled it, open http://<pi-ip>:$WEB_PORT (LAN only, no auth)."
log "  Serial:     if the port is not what the wizard set, edit 'serial.port' in"
log "               ${DEST_DIR}/config.yaml (check with: ls /dev/ttyACM*)."
log "  Audio:      run 'aplay -l' — it should list a bcm2835 device. If silent,"
log "               confirm the amp's SD MODE pin is tied to 3.3V (WIRING.md)."
