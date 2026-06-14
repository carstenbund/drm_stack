#!/usr/bin/env bash
# Bootstrap the drm_stack dev environment.
#
# Clones the three packages (if missing) and editable-installs them into a
# stack-level .venv, in dependency order:  drm_display -> drm_screen -> drm_composer
#
# Idempotent: safe to re-run.  Existing clones are left untouched (pull them
# yourself); only missing ones are cloned.
set -euo pipefail

cd "$(dirname "$0")"

GH=https://github.com/carstenbund
# package dir : repo name (dependency order matters)
PACKAGES=(drm_display drm_screen drm_composer)

echo "== clone missing packages =="
for pkg in "${PACKAGES[@]}"; do
    if [ -d "$pkg/.git" ]; then
        echo "  $pkg: present (leaving as-is)"
    else
        echo "  $pkg: cloning"
        git clone "$GH/$pkg" "$pkg"
    fi
done

echo "== create .venv =="
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --upgrade pip

echo "== editable install (dependency order) =="
# drm_display first so it satisfies drm-screen's dependency from the local tree
# rather than PyPI; likewise drm_screen before drm_composer.
for pkg in "${PACKAGES[@]}"; do
    echo "  pip install -e ./$pkg"
    python -m pip install --quiet -e "./$pkg"
done
python -m pip install --quiet pillow   # needed by drm_screen.assets / drm_composer.painter

echo "== verify =="
python - <<'PY'
import drm_display, drm_screen, drm_composer
print("  drm_display ", getattr(drm_display, "__version__", "?"))
print("  drm_screen  ok")
print("  drm_composer ok")
PY

echo
echo "Done.  Activate with:  source .venv/bin/activate"
echo "Run the integration demo:  python integration/stack_demo.py"
