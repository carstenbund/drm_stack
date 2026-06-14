"""HTML-compatibility of screen-HTML value parsing — lengths and colors.

The parser is forgiving like real HTML: lengths tolerate units and resolve
percentages against the screen; colors accept the full CSS range; bad values
fall back rather than raising.
"""

from drm_composer import parse_scene, paint_scene
from drm_composer.parser import _length
from drm_composer.painter import _rgba
from drm_screen.commands import CreateLayer, PlaceRawBuffer


# ── lengths ───────────────────────────────────────────────────────────────────

def test_length_tolerates_units_and_floats():
    assert _length("20", 0) == 20
    assert _length("20px", 0) == 20
    assert _length("20.5", 0) == 20         # rounded to nearest pixel
    assert _length("1.5em", 0) == 2
    assert _length("-3px", 0) == -3


def test_length_falls_back_on_junk():
    assert _length("auto", 9) == 9
    assert _length("", 9) == 9
    assert _length(None, 9) == 9
    assert _length("nope", 9) == 9


def test_percent_resolves_against_screen():
    scene = parse_scene(
        '<screen width="800" height="480"><layer id="l" z="0">'
        '<box x="50%" y="50%" w="25%" h="100%" color="#fff"/></layer></screen>')
    box = scene.layers[0].children[0]
    # x/w against width (800), y/h against height (480)
    assert (box.x, box.y, box.w, box.h) == (400, 240, 200, 480)


# ── colors ────────────────────────────────────────────────────────────────────

def test_colors_cover_the_css_range():
    assert _rgba("red") == (255, 0, 0, 255)
    assert _rgba("navy") == (0, 0, 128, 255)
    assert _rgba("rebeccapurple") == (102, 51, 153, 255)   # full CSS name list
    assert _rgba("#f0c") == (255, 0, 204, 255)
    assert _rgba("#000000aa") == (0, 0, 0, 170)
    assert _rgba("#1234") == (17, 34, 51, 68)
    assert _rgba("rgb(255,0,0)") == (255, 0, 0, 255)
    assert _rgba("rgba(0,0,0,128)") == (0, 0, 0, 128)


def test_bad_color_is_transparent_not_an_error():
    assert _rgba("not-a-color") == (0, 0, 0, 0)
    assert _rgba("") == (0, 0, 0, 0)


def test_missing_image_renders_placeholder_not_an_error():
    html = ('<screen width="200" height="100"><layer id="l" z="0">'
            '<img src="/no/such/image.jpg" x="10" y="10" w="80" h="60"/>'
            '</layer></screen>')
    batch = paint_scene(parse_scene(html))   # placeholder drawn; no exception
    assert any(isinstance(c, CreateLayer) for c in batch)
    assert any(isinstance(c, PlaceRawBuffer) for c in batch)


def test_a_scene_with_messy_values_still_parses():
    # units, percents, a CSS name, and a typo'd color — no exception
    html = ('<screen width="200" height="100"><layer id="l" z="0">'
            '<box x="10px" y="50%" w="100%" h="20" color="darkslategray"/>'
            '<text x="5em" y="oops" size="14pt" color="bogus">hi</text>'
            '</layer></screen>')
    scene = parse_scene(html)
    box = scene.layers[0].children[0]
    assert (box.x, box.y, box.w, box.h) == (10, 50, 200, 20)
