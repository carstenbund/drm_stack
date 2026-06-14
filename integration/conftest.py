"""Shared fixtures for drm_stack integration tests.

These tests exercise the real packages together (drm_composer -> drm_screen ->
drm_display) on the headless dummy backend.  Rendering is driven synchronously
via `service.render_once()` so tests are deterministic — no sleeps, no threads —
except where the threaded service path is explicitly under test.
"""

import numpy as np
import pytest

from drm_screen import DrmDisplayBackend, ScreenService
from drm_screen.commands import CreateLayer, PlaceRawBuffer

W, H = 200, 120


@pytest.fixture
def backend():
    b = DrmDisplayBackend(device="dummy", width=W, height=H)
    yield b
    b.close()


@pytest.fixture
def service(backend):
    """A ScreenService whose render loop is NOT started — drive it by hand."""
    return ScreenService(backend, fps=30)


def render(service, batch):
    """Submit a batch and synchronously composite -> returns RGBA frame."""
    service.submit(batch)
    service.render_once()
    return service.backend.snapshot_rgba()


def raw_layer(name, rgba, z=0, x=0, y=0):
    """A [CreateLayer, PlaceRawBuffer] pair from an RGBA array."""
    h, w = rgba.shape[:2]
    return [
        CreateLayer(name, W, H, x=0, y=0, z=z),
        PlaceRawBuffer(name=name, width=w, height=h,
                       data=np.ascontiguousarray(rgba).tobytes(), x=x, y=y),
    ]


def solid(w, h, rgba):
    buf = np.empty((h, w, 4), dtype=np.uint8)
    buf[:] = np.asarray(rgba, dtype=np.uint8)
    return buf
