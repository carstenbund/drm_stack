#!/usr/bin/env bash
# install.sh — put the sudoku kit on a fresh Raspberry Pi OS Lite card.
#
# Run it as the normal user (it calls sudo where it needs to):
#
#   curl -fsSL https://raw.githubusercontent.com/carstenbund/drm_stack/main/kit/install.sh \
#     | bash -s -- --display hdmi
#
# What it does, in order:
#   1. apt: git, a compiler, libdrm headers, and prebuilt numpy/Pillow
#   2. clone drm_stack and run its setup.sh (which clones the four packages)
#   3. verify the DRM C helper actually compiled  <- the one that bites
#   4. add you to the video and input groups
#   5. optionally write a display overlay into config.txt
#   6. optionally install a systemd unit so the game starts at boot
#
# Idempotent: safe to re-run.  Nothing is removed, config.txt edits live in a
# marked block that is replaced rather than appended to.
set -euo pipefail

REPO_URL="https://github.com/carstenbund/drm_stack"
BRANCH="main"
DIR=""
RUN_USER="${SUDO_USER:-${USER:-$(id -un)}}"
DISPLAY_PROFILE="keep"
DIFFICULTY="medium"
AUTOSTART=1
DRY_RUN=0
MARK_BEGIN="# >>> drm_stack sudoku kit >>>"
MARK_END="# <<< drm_stack sudoku kit <<<"

usage() {
    cat <<'EOF'
usage: install.sh [options]

  --display PROFILE  hdmi | dsi7 | keep   (default: keep — touch nothing)
                     DPI and SPI panels are not written automatically:
                     their config is panel-specific, see kit/DISPLAY.md
  --branch NAME      git branch to install (default: main)
  --dir PATH         install location (default: ~/drm_stack)
  --user NAME        account that runs the game (default: invoking user)
  --difficulty NAME  easy | medium | hard   (default: medium)
  --no-autostart     skip the systemd unit; run the game by hand
  --dry-run          print what would happen, change nothing
  -h, --help         this

examples:
  ./install.sh --display hdmi
  ./install.sh --display keep --no-autostart      # SPI panel, configure by hand
EOF
}

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m warn\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mfail\033[0m %s\n' "$*" >&2; exit 1; }
run()  { if [ "$DRY_RUN" = 1 ]; then printf '     would run: %s\n' "$*"; else "$@"; fi; }

while [ $# -gt 0 ]; do
    case "$1" in
        --display)    DISPLAY_PROFILE="${2:?}"; shift 2 ;;
        --branch)     BRANCH="${2:?}"; shift 2 ;;
        --dir)        DIR="${2:?}"; shift 2 ;;
        --user)       RUN_USER="${2:?}"; shift 2 ;;
        --difficulty) DIFFICULTY="${2:?}"; shift 2 ;;
        --no-autostart) AUTOSTART=0; shift ;;
        --dry-run)    DRY_RUN=1; shift ;;
        -h|--help)    usage; exit 0 ;;
        *)            usage; die "unknown option: $1" ;;
    esac
done

case "$DISPLAY_PROFILE" in
    hdmi|dsi7|keep) ;;
    dpi|spi) die "--display $DISPLAY_PROFILE is panel-specific and not written
       automatically — a wrong overlay line is a Pi that boots to nothing.
       See kit/DISPLAY.md, configure it by hand, then re-run with --display keep." ;;
    *) die "unknown display profile: $DISPLAY_PROFILE (hdmi | dsi7 | keep)" ;;
esac
case "$DIFFICULTY" in easy|medium|hard) ;; *) die "bad --difficulty: $DIFFICULTY" ;; esac

HOME_DIR="$(getent passwd "$RUN_USER" | cut -d: -f6)"
[ -n "$HOME_DIR" ] || die "no such user: $RUN_USER"
DIR="${DIR:-$HOME_DIR/drm_stack}"

# ── 0. sanity ────────────────────────────────────────────────────────────────

[ "$(id -u)" -ne 0 ] || warn "running as root; the game will run as '$RUN_USER'"
command -v apt-get >/dev/null || die "this installer expects Raspberry Pi OS / Debian"
command -v sudo    >/dev/null || die "sudo is required"

MODEL="$( { tr -d '\0' < /proc/device-tree/model; } 2>/dev/null || echo unknown)"
log "target: $MODEL"
log "user:   $RUN_USER   dir: $DIR   branch: $BRANCH   display: $DISPLAY_PROFILE"
case "$MODEL" in
    *"Raspberry Pi"*) ;;
    *) warn "this does not look like a Raspberry Pi — continuing anyway" ;;
esac
if [ -d /usr/share/xsessions ] || systemctl list-unit-files 2>/dev/null | grep -qE '^(lightdm|gdm3)\.service'; then
    warn "a desktop session is installed.  It will hold the DRM master lock and"
    warn "the game will render to nothing.  Use Raspberry Pi OS Lite, or:"
    warn "  sudo systemctl set-default multi-user.target && sudo reboot"
fi

# ── 1. system packages ───────────────────────────────────────────────────────
# gcc + libdrm-dev are not optional: drm_display compiles a small C helper for
# the DRM backend at pip-install time, and *silently* falls back to a dummy
# buffer if it cannot.  numpy and Pillow come from apt because building them
# from source on a Zero takes the better part of an hour.

log "installing system packages"
run sudo apt-get update -qq
run sudo apt-get install -y --no-install-recommends \
    git ca-certificates gcc pkg-config libdrm-dev \
    python3-venv python3-dev python3-numpy python3-pil

# ── 2. the stack ─────────────────────────────────────────────────────────────

if [ -d "$DIR/.git" ]; then
    log "updating $DIR"
    run git -C "$DIR" fetch --depth 1 origin "$BRANCH"
    run git -C "$DIR" checkout -B "$BRANCH" "origin/$BRANCH"
else
    log "cloning $REPO_URL ($BRANCH)"
    run git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$DIR"
fi

if [ "$DRY_RUN" = 0 ] && [ ! -f "$DIR/integration/sudoku_demo.py" ]; then
    die "branch '$BRANCH' has no integration/sudoku_demo.py.
       Pass the branch that carries the demo, e.g. --branch claude/sudoku-game-demo-lj04jt"
fi

# Pre-create the venv with --system-site-packages so the apt-installed numpy and
# Pillow are visible inside it; setup.sh only creates .venv when it is missing,
# so it will reuse this one instead of building those wheels from source.
if [ ! -d "$DIR/.venv" ]; then
    log "creating .venv (--system-site-packages: reuse apt numpy/Pillow)"
    run python3 -m venv --system-site-packages "$DIR/.venv"
fi

log "running setup.sh (clones drm_display, drm_screen, drm_touch, drm_composer)"
run env -C "$DIR" ./setup.sh

# ── 3. verify the DRM backend actually built ─────────────────────────────────

SO="$DIR/drm_display/drm_display/libdrm_display.so"
if [ "$DRY_RUN" = 0 ]; then
    if [ ! -f "$SO" ]; then
        log "C helper missing — building it explicitly"
        make -C "$DIR/drm_display" || true
    fi
    [ -f "$SO" ] || die "libdrm_display.so did not build.  Without it drm_display
       falls back to a headless buffer and you get a black screen with no error.
       Check that gcc and libdrm-dev installed cleanly, then:
         make -C $DIR/drm_display"
    log "DRM backend built: $(basename "$SO")"
fi

# ── 4. groups ────────────────────────────────────────────────────────────────
# video -> /dev/dri/cardN, input -> /dev/input/event*.  Takes effect at next login.

for grp in video input; do
    if getent group "$grp" >/dev/null && ! id -nG "$RUN_USER" | tr ' ' '\n' | grep -qx "$grp"; then
        log "adding $RUN_USER to group $grp"
        run sudo usermod -aG "$grp" "$RUN_USER"
    fi
done

# ── 5. display overlay ───────────────────────────────────────────────────────

CONFIG=/boot/firmware/config.txt
[ -f "$CONFIG" ] || CONFIG=/boot/config.txt          # Bullseye and older

write_config_block() {
    local body="$1"
    [ -f "$CONFIG" ] || { warn "no config.txt found — skipping display config"; return; }
    if [ "$DRY_RUN" = 1 ]; then
        printf '     would write to %s:\n%s\n' "$CONFIG" "$body"; return
    fi
    sudo cp -n "$CONFIG" "$CONFIG.kit-backup" || true
    # replace our marked block rather than appending a second copy
    sudo sed -i "/$MARK_BEGIN/,/$MARK_END/d" "$CONFIG"
    printf '%s\n%s\n%s\n' "$MARK_BEGIN" "$body" "$MARK_END" | sudo tee -a "$CONFIG" >/dev/null
    log "wrote display config to $CONFIG (backup: $CONFIG.kit-backup)"
}

case "$DISPLAY_PROFILE" in
    hdmi) write_config_block "dtoverlay=vc4-kms-v3d
disable_overscan=1" ;;
    dsi7) write_config_block "dtoverlay=vc4-kms-v3d
dtoverlay=vc4-kms-dsi-7inch" ;;
    keep) log "leaving $CONFIG alone (--display keep)" ;;
esac

# ── 6. autostart ─────────────────────────────────────────────────────────────

if [ "$AUTOSTART" = 1 ]; then
    UNIT_SRC="$DIR/kit/sudoku-kiosk.service.in"
    UNIT_DST=/etc/systemd/system/sudoku-kiosk.service
    if [ -f "$UNIT_SRC" ] || [ "$DRY_RUN" = 1 ]; then
        log "installing systemd unit sudoku-kiosk.service"
        if [ "$DRY_RUN" = 0 ]; then
            sed -e "s|@USER@|$RUN_USER|g" -e "s|@DIR@|$DIR|g" \
                -e "s|@DIFFICULTY@|$DIFFICULTY|g" "$UNIT_SRC" \
                | sudo tee "$UNIT_DST" >/dev/null
            sudo systemctl daemon-reload
            sudo systemctl enable sudoku-kiosk.service >/dev/null
        fi
    else
        warn "$UNIT_SRC missing — skipping autostart"
    fi
else
    log "skipping autostart (--no-autostart)"
fi

# ── done ─────────────────────────────────────────────────────────────────────

cat <<EOF

$(log "installed")

  check it              cd $DIR && ./kit/verify.sh
  play it now           cd $DIR && .venv/bin/python integration/sudoku_demo.py
  how fast is this Pi   cd $DIR && .venv/bin/python integration/sudoku_demo.py --benchmark

$( [ "$AUTOSTART" = 1 ] && echo "  the game starts at boot: systemctl status sudoku-kiosk" )
$( [ "$DISPLAY_PROFILE" = keep ] && echo "  display config untouched — see kit/DISPLAY.md if /dev/dri is empty" )

Reboot now: the group membership and any config.txt change need it.

  sudo reboot
EOF
