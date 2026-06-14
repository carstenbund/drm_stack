"""Make the installed drm_* packages win over the source-tree clones.

`python -m pytest` (and several IDE test runners) prepend the repo root to
`sys.path`. Each package's real code lives one level down at ``<name>/<name>/``,
so the repo root contains bare ``drm_screen/``, ``drm_composer/``,
``drm_display/``, ``drm_touch/`` directories. With the repo root on the path,
Python imports *those* as namespace packages and shadows the editable-installed
packages (whose code is the inner dir) — e.g. ``from drm_screen import
DrmDisplayBackend`` then fails with "unknown location".

pytest loads this rootdir conftest before collecting `integration/conftest.py`,
so dropping the repo root here fixes imports regardless of how pytest is invoked
(`pytest`, `python -m pytest`, IDE runner). Tests are collected by path, not via
this entry, so removing it is safe.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
# A "" entry means cwd; resolve it too, so `python -m pytest` from the repo root
# (which prepends "") is caught alongside an explicit repo-root entry.
sys.path[:] = [p for p in sys.path
               if os.path.abspath(p or os.getcwd()) != _ROOT]
