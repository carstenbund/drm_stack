#!/usr/bin/env bash
# verify.sh — answer "why is my screen black", in order of likelihood.
#
#   ./kit/verify.sh            # checks + headless selftest + benchmark
#   ./kit/verify.sh --quick    # checks only
#
# Exit code 0 = everything a black screen could be caused by is fine.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$DIR/.venv/bin/python"
QUICK=0
[ "${1:-}" = "--quick" ] && QUICK=1

pass=0; fail=0; warn=0
ok()   { printf '  \033[1;32m ok \033[0m %s\n' "$*"; pass=$((pass+1)); }
no()   { printf '  \033[1;31mFAIL\033[0m %s\n' "$*"; fail=$((fail+1)); }
hmm()  { printf '  \033[1;33mwarn\033[0m %s\n' "$*"; warn=$((warn+1)); }
head_() { printf '\n\033[1;36m%s\033[0m\n' "$*"; }

head_ "system"
printf '  %s\n' "$( { tr -d '\0' < /proc/device-tree/model; } 2>/dev/null || echo 'unknown model')"
printf '  %s\n' "$(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME") — $(uname -rm)"

head_ "1. a device to render to"
shopt -s nullglob
cards=(/dev/dri/card*)
fbs=(/dev/fb*)
FBDEV_PATCHED=0
if [ -d "$DIR/drm_display" ] && \
   git -C "$DIR/drm_display" apply --check --reverse \
       "$DIR/kit/patches/drm_display-fbdev.patch" 2>/dev/null; then
    FBDEV_PATCHED=1
fi

if [ ${#cards[@]} -gt 0 ]; then
    ok "DRM: ${cards[*]}"
elif [ ${#fbs[@]} -gt 0 ] && [ "$FBDEV_PATCHED" = 1 ]; then
    ok "no DRM card, but ${fbs[*]} and the fbdev patch is applied"
    for fb in "${fbs[@]}"; do
        n=$(basename "$fb")
        printf '       %s: %s, %s, %s bpp\n' "$n" \
            "$(cat "/sys/class/graphics/$n/name" 2>/dev/null || echo '?')" \
            "$(cat "/sys/class/graphics/$n/virtual_size" 2>/dev/null || echo '?')" \
            "$(cat "/sys/class/graphics/$n/bits_per_pixel" 2>/dev/null || echo '?')"
    done
    hmm "pass the panel explicitly if auto-detect picks the wrong one: --device ${fbs[0]}"
elif [ ${#fbs[@]} -gt 0 ]; then
    no "only ${fbs[*]} — a framebuffer panel, which needs the fbdev patch:
         ./kit/install.sh --patch-fbdev     (see kit/patches/README.md)
       or put the panel on DRM instead (kit/DISPLAY.md)"
else
    no "no /dev/dri/card* and no /dev/fb* — nothing can be rendered.  See kit/DISPLAY.md"
fi
if [ "$FBDEV_PATCHED" = 0 ]; then
    hmm "fbdev patch not applied: unpatched, a missing framebuffer reports
       'Display: /dev/fb0 (1920x1080)' and renders into RAM.  That exact line —
       fb0 at 1920x1080 — means nothing is connected."
fi

head_ "2. nothing else is holding the display"
if [ -d /usr/share/xsessions ] || systemctl list-unit-files 2>/dev/null | grep -qE '^(lightdm|gdm3)\.service'; then
    hmm "a desktop is installed; if it runs it takes DRM master and your frames go nowhere"
else
    ok "no desktop session installed — nothing will take the DRM master lock"
fi
if [ -x "$DIR/.venv/bin/drm-list-modes" ]; then
    "$DIR/.venv/bin/drm-list-modes" 2>&1 | sed 's/^/    /'
else
    hmm "drm-list-modes not installed — run kit/install.sh"
fi

head_ "3. the DRM C helper compiled"
SO="$DIR/drm_display/drm_display/libdrm_display.so"
if [ -f "$SO" ]; then
    ok "libdrm_display.so present"
else
    no "libdrm_display.so missing — drm_display silently falls back to a dummy
       buffer, which looks exactly like a dead panel.  Fix:
         sudo apt install -y gcc libdrm-dev && make -C $DIR/drm_display"
fi

head_ "4. permissions"
ME="$(id -un)"
for grp in video input; do
    if [ "$ME" = root ] || id -nG | tr ' ' '\n' | grep -qx "$grp"; then
        ok "$ME can reach $grp devices"
    else
        no "not in group $grp — 'sudo usermod -aG $grp $ME', then log out and back in"
    fi
done

head_ "5. a pointer to play with"
evs=(/dev/input/event*)
if [ ${#evs[@]} -gt 0 ]; then
    ok "${#evs[@]} input device(s)"
    [ -x "$PY" ] && "$PY" -m drm_touch 2>&1 | sed 's/^/    /' | head -20
else
    no "no /dev/input/event* — no touch and no mouse.  Check the touch overlay (kit/DISPLAY.md)"
fi

head_ "6. the game itself"
if [ ! -x "$PY" ]; then
    no "no venv at $DIR/.venv — run kit/install.sh"
elif [ "$QUICK" = 1 ]; then
    hmm "skipped (--quick)"
else
    if out=$("$PY" "$DIR/integration/sudoku_demo.py" --selftest 2>&1); then
        ok "selftest passed — $(echo "$out" | tail -1)"
    else
        no "selftest failed:"; echo "$out" | sed 's/^/    /'
    fi
fi

if [ "$QUICK" = 0 ] && [ -x "$PY" ]; then
    head_ "7. how fast this board is"
    # measure at the panel's own resolution when we can read it
    W=480; H=320
    for m in /sys/class/drm/card*-*/modes; do
        first=$(head -1 "$m" 2>/dev/null)
        case "$first" in
            [0-9]*x[0-9]*) W="${first%%x*}"; H="${first##*x}"; H="${H%%[!0-9]*}"; break ;;
        esac
    done
    "$PY" "$DIR/integration/sudoku_demo.py" --benchmark --width "$W" --height "$H" \
        2>/dev/null | grep -v '^Display:' | sed 's/^/  /'
fi

head_ "summary"
printf '  %d ok, %d warnings, %d failures\n' "$pass" "$warn" "$fail"
if [ "$fail" -eq 0 ]; then
    printf '  \033[1;32mnothing blocking — start it with:\033[0m %s integration/sudoku_demo.py\n' "$PY"
    exit 0
fi
printf '  \033[1;31mfix the FAIL lines above, then re-run\033[0m\n'
exit 1
