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


def test_image_fit_modes_preserve_aspect():
    from PIL import Image
    from drm_composer.painter import _fit_image
    src = Image.new("RGBA", (100, 50), (255, 0, 0, 255))   # opaque 2:1 image

    contain = _fit_image(src, 80, 80, "contain")           # letterboxed
    assert contain.size == (80, 80)
    assert contain.getpixel((40, 0))[3] == 0               # top is transparent bar
    assert contain.getpixel((40, 40))[3] == 255            # centre is the image

    cover = _fit_image(src, 80, 80, "cover")               # cropped, fully covers
    assert cover.size == (80, 80)
    assert cover.getpixel((40, 0))[3] == 255

    assert _fit_image(src, 80, 80, "fill").size == (80, 80)  # stretched


def test_img_fullscreen_emits_a_tap_overlay():
    html = ('<screen width="800" height="480"><layer id="l" z="0">'
            '<img src="/p/x.jpg" x="10" y="10" w="100" h="80" fullscreen/>'
            '</layer></screen>')
    batch = paint_scene(parse_scene(html))
    overlays = [c for c in batch if isinstance(c, CreateLayer) and c.interactive]
    assert len(overlays) == 1
    o = overlays[0]
    assert o.hit_id == "full:/p/x.jpg"                       # carries the src
    assert (o.x, o.y, o.width, o.height) == (10, 10, 100, 80)  # over the image


def test_fullscreen_hit_parses_to_an_action():
    from drm_composer import parse_action
    a = parse_action("full:/p/x.jpg")
    assert a.kind == "fullscreen" and a.target == "/p/x.jpg"


def test_fullscreen_always_has_no_toggle_overlay():
    # 'always' is drawn fullscreen and is NOT toggle-able -> no interactive layer
    html = ('<screen width="800" height="480"><layer id="l" z="0">'
            '<img src="/p/x.jpg" x="10" y="10" w="100" h="80" fullscreen="always"/>'
            '</layer></screen>')
    batch = paint_scene(parse_scene(html))
    assert not [c for c in batch if isinstance(c, CreateLayer) and c.interactive]


def test_fullscreen_modes_parse():
    from drm_composer.parser import _fullscreen
    assert _fullscreen({"fullscreen": None}) == "toggle"       # bare attribute
    assert _fullscreen({"fullscreen": "toggle"}) == "toggle"
    assert _fullscreen({"fullscreen": "always"}) == "always"
    assert _fullscreen({"fullscreen": "false"}) == ""
    assert _fullscreen({}) == ""


def test_a_scene_with_messy_values_still_parses():
    # units, percents, a CSS name, and a typo'd color — no exception
    html = ('<screen width="200" height="100"><layer id="l" z="0">'
            '<box x="10px" y="50%" w="100%" h="20" color="darkslategray"/>'
            '<text x="5em" y="oops" size="14pt" color="bogus">hi</text>'
            '</layer></screen>')
    scene = parse_scene(html)
    box = scene.layers[0].children[0]
    assert (box.x, box.y, box.w, box.h) == (10, 50, 200, 20)
