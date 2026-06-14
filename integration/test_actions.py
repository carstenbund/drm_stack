"""The composer↔host action interface — grammar parsing and dispatch routing.

`hit_id` strings are opaque to drm_screen; drm_composer owns their grammar.
These tests pin the wire format (every documented row + malformed input) and the
allowlist-by-registration dispatch contract.  Pure — no Pillow, no display.
"""

from drm_composer import Action, parse_action, Dispatcher


# ── grammar ─────────────────────────────────────────────────────────────────

def test_parses_every_documented_kind():
    assert parse_action("href:settings.html") == Action(
        "navigate", "settings.html", raw="href:settings.html")
    assert parse_action("cmd:reboot") == Action(
        "command", "reboot", raw="cmd:reboot")
    assert parse_action("play:/media/clip.mp4") == Action(
        "play", "/media/clip.mp4", raw="play:/media/clip.mp4")
    assert parse_action("set:brightness=80") == Action(
        "set", "brightness", value="80", raw="set:brightness=80")


def test_bare_id_is_a_routable_action():
    # quit / back have no prefix -> kind="action", whole string as target.
    assert parse_action("quit") == Action("action", "quit", raw="quit")
    assert parse_action("back") == Action("action", "back", raw="back")


def test_parse_is_total_and_never_raises():
    assert parse_action("") == Action("action", "", raw="")
    assert parse_action(None) == Action("action", "", raw="")
    # unknown prefix degrades to a bare action rather than crashing
    assert parse_action("bogus:thing") == Action("action", "bogus:thing", raw="bogus:thing")


def test_set_without_value_is_none():
    a = parse_action("set:muted")
    assert a.kind == "set" and a.target == "muted" and a.value is None


def test_set_value_may_contain_equals():
    a = parse_action("set:filter=a=b")
    assert a.target == "filter" and a.value == "a=b"


def test_target_may_contain_colons():
    # only the FIRST ':' splits the prefix — paths/urls keep theirs
    a = parse_action("play:http://host/clip.mp4")
    assert a.kind == "play" and a.target == "http://host/clip.mp4"


# ── dispatch ────────────────────────────────────────────────────────────────

def test_dispatch_routes_to_registered_handlers():
    seen = {}
    d = (Dispatcher()
         .on_navigate(lambda t: seen.__setitem__("nav", t))
         .on_play(lambda t: seen.__setitem__("play", t))
         .on_command("reboot", lambda: seen.__setitem__("cmd", True))
         .on_set("brightness", lambda v: seen.__setitem__("set", v))
         .on_action("quit", lambda: seen.__setitem__("quit", True)))

    assert d.dispatch(parse_action("href:home.html")) is True
    assert d.dispatch(parse_action("play:clip.mp4")) is True
    assert d.dispatch(parse_action("cmd:reboot")) is True
    assert d.dispatch(parse_action("set:brightness=80")) is True
    assert d.dispatch(parse_action("quit")) is True
    assert seen == {"nav": "home.html", "play": "clip.mp4",
                    "cmd": True, "set": "80", "quit": True}


def test_unregistered_is_silent_noop():
    d = Dispatcher().on_command("reboot", lambda: None)
    # allowlist-by-registration: a command/key/action never opted into does nothing
    assert d.dispatch(parse_action("cmd:rm-rf")) is False
    assert d.dispatch(parse_action("set:brightness=80")) is False
    assert d.dispatch(parse_action("back")) is False
    assert d.dispatch(parse_action("href:home.html")) is False   # no navigate handler


def test_dispatch_accepts_raw_string_and_none():
    hits = []
    d = Dispatcher().on_navigate(hits.append)
    assert d.dispatch("href:menu.html") is True       # raw hit_id, parsed internally
    assert d.dispatch(None) is False                  # no hit
    assert hits == ["menu.html"]


def test_unhandled_hit_is_logged_when_logger_given():
    records = []

    class Log:
        def info(self, msg, *args):
            records.append(msg % args)

    d = Dispatcher(logger=Log())
    assert d.dispatch(parse_action("cmd:nope")) is False
    assert records and "cmd:nope" in records[0]
