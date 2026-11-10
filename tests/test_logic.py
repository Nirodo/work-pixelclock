from datetime import datetime

import pytest

from pixelpanel import config, pages, sources
from pixelpanel.core.render import Rendered

LOG_LINE = ("2026-11-03T12:23:16.356777+01:00 0x00003440 <INFO> native_modules::UserDataCrossCloudModule: "
            "Received Action: UserPresenceAction: {{cloud_context: https://teams.microsoft.com, availability: {}}}\n")
NOISE = "2026-11-03T12:23:16 <INFO> BroadcastGlobalState: { availability: PresenceUnknown, unread: 0 }\n"


def test_last_presence_ignores_unknown():
    assert sources.last_presence(LOG_LINE.format("Busy") + NOISE) == "Busy"
    assert sources.last_presence(NOISE) is None


def test_teams_watcher_follows_appends_and_rotation(tmp_path):
    old = tmp_path / "MSTeams_2026-11-03_11-00-00.01.log"
    old.write_text(LOG_LINE.format("Available"), encoding="utf-8")
    w = sources.TeamsWatcher(tmp_path)
    assert w.poll() == "Available"

    with open(old, "a", encoding="utf-8") as f:
        f.write(LOG_LINE.format("Busy"))
        f.write(LOG_LINE.format("DoNotDisturb")[:40])  # half written line is not read yet
    assert w.poll() == "Busy"

    import os
    import time
    new = tmp_path / "MSTeams_2026-11-03_13-00-00.02.log"
    new.write_text(NOISE, encoding="utf-8")
    t = time.time() + 5
    os.utime(new, (t, t))
    assert w.poll() == "Busy"  # empty new file keeps the last state
    with open(new, "a", encoding="utf-8") as f:
        f.write(LOG_LINE.format("Away"))
    assert w.poll() == "Away"


def test_parse_claude_usage():
    u = sources.parse_claude_usage({
        "five_hour": {"utilization": 50.0, "resets_at": "2026-11-03T18:20:00.46141+01:00"},
        "seven_day": {"utilization": 40.0, "resets_at": "2026-11-09T03:00:00+01:00"},
    }, "pro")
    assert (u.session, u.week, u.plan) == (50.0, 40.0, "pro")
    assert u.session_reset.hour == 18


def _profile(org_type, tier, names=True):
    org = {"organization_type": org_type, "rate_limit_tier": tier}
    acc = {"has_claude_max": org_type == "claude_max", "has_claude_pro": org_type == "claude_pro"}
    data = {"account": acc, "organization": org}
    if names:
        data["organization"]["plan_offer_copy"] = {"max5x": {"name": "Max"}, "max20x": {"name": "Ultra"}}
    return data


def test_plan_comes_from_the_live_profile_not_the_stale_sign_in_value():
    # signed in as Pro, upgraded later: the credentials file still says "pro"
    assert sources.plan_name(_profile("claude_max", "default_claude_max_5x"), "pro") == "Max (5x)"
    assert sources.plan_name(_profile("claude_max", "default_claude_max_20x"), "pro") == "Max (20x)"
    assert sources.plan_name(_profile("claude_max", "default_claude_max_20x", names=False), "pro") == "Max (20x)"
    assert sources.plan_name(_profile("claude_max", "default_claude_max"), "pro") == "Max"
    assert sources.plan_name(_profile("claude_pro", "default_claude_ai"), "pro") == "Pro"
    assert sources.plan_name(_profile("claude_team", "default_claude_ai"), None) == "Team"
    assert sources.plan_name(_profile("claude_enterprise", "x"), None) == "Enterprise"


def test_plan_falls_back_to_the_sign_in_value():
    assert sources.plan_name(None, "max") == "Max"
    assert sources.plan_name({}, "pro") == "Pro"
    assert sources.plan_name(None, None) is None


def test_split_emoji():
    assert pages.split_emoji("Kaffee ☕ Pause") == ("Kaffee Pause", "☕")
    assert pages.split_emoji("❤️ Liebe") == ("Liebe", "❤️")
    assert pages.split_emoji("Hallo Welt") == ("Hallo Welt", None)


def test_emoji_icon_is_16px():
    icon = pages.emoji_icon("😀")
    assert icon is not None and icon.size == (16, 16)


def test_clawd_icon_has_eyes_and_legs():
    img = pages.load_icon("clawd")
    assert img.size == (16, 10)
    assert img.getpixel((4, 2))[3] == 0 and img.getpixel((11, 2))[3] == 0  # eyes are holes
    assert img.getpixel((0, 4))[:3] == pages.CLAUDE_ORANGE  # the arms reach the edges (2 px wide)
    assert img.getpixel((3, 8))[3] == 255 and img.getpixel((4, 8))[3] == 0  # separate legs


def test_clawd_animation_has_wave_sit_hop_walk_laptop_and_blink():
    frames = pages.clawd_frames()
    names = [name for name, _, _ in frames]
    for move in ("blink", "wave", "hop", "sit", "look", "walk", "type"):
        assert any(n.startswith(move) for n in names), move
    assert all(img.size == (16, 16) for _, img, _ in frames)
    assert all(ms >= 60 for _, _, ms in frames)
    assert 6000 <= sum(ms for _, _, ms in frames) <= 20000  # one richer loop
    imgs = {n: img for n, img, _ in frames}
    assert imgs["wave_up"].tobytes() != imgs["idle"].tobytes()
    assert imgs["hop_top"].getbbox()[1] < imgs["idle"].getbbox()[1]  # in the air: higher
    assert imgs["sit"].getbbox()[3] == imgs["idle"].getbbox()[3]      # sitting: same ground
    assert imgs["sit"].getbbox()[1] > imgs["idle"].getbbox()[1]       # ... but lower body
    assert imgs["type"].tobytes() != imgs["idle"].tobytes()           # the laptop scene differs


def test_clawd_mood_follows_session_usage():
    assert pages.clawd_mood(None) == "lively"
    assert pages.clawd_mood(10) == "lively"
    assert pages.clawd_mood(pages.CLAWD_STARTLED_AT) == "startled"
    assert pages.clawd_mood(85) == "startled"
    assert pages.clawd_mood(pages.CLAWD_SLEEPY_AT) == "sleepy"
    assert pages.clawd_mood(99) == "sleepy"


def test_clawd_moods_have_their_own_moves():
    startled = [n for n, _, _ in pages.clawd_frames("startled")]
    assert any(n == "knock" for n in startled) and any(n == "wonder" for n in startled)
    sleepy = [n for n, _, _ in pages.clawd_frames("sleepy")]
    assert any(n == "tired" for n in sleepy) and any(n == "doze" for n in sleepy)
    for mood in ("lively", "startled", "sleepy"):
        frames = pages.clawd_frames(mood)
        assert all(img.size == (16, 16) for _, img, _ in frames)
        assert all(ms >= 60 for _, _, ms in frames)


def test_claude_page_animates_clawd_unless_switched_off():
    ctx = pages.Context(claude=sources.ClaudeUsage(session=50, week=40))
    now = datetime(2026, 11, 3, 14, 5)
    animated = pages.render_page({**config.new_page("claude"), "logo": "clawd"}, ctx, 64, 16, now)
    assert animated.animated and len(animated.frames) == len(pages.clawd_frames())
    still = pages.render_page({**config.new_page("claude"), "logo": "clawd", "animate": False}, ctx, 64, 16, now)
    assert not still.animated
    logo = pages.render_page({**config.new_page("claude"), "logo": "claude"}, ctx, 64, 16, now)
    assert not logo.animated


def test_claude_page_animation_reacts_to_token_usage():
    now = datetime(2026, 11, 3, 14, 5)
    page = {**config.new_page("claude"), "logo": "clawd"}
    lively = pages.render_page(page, pages.Context(claude=sources.ClaudeUsage(session=20, week=10)), 64, 16, now)
    startled = pages.render_page(page, pages.Context(claude=sources.ClaudeUsage(session=85, week=40)), 64, 16, now)
    sleepy = pages.render_page(page, pages.Context(claude=sources.ClaudeUsage(session=96, week=80)), 64, 16, now)
    assert len(lively.frames) == len(pages.clawd_frames("lively"))
    assert len(startled.frames) == len(pages.clawd_frames("startled"))
    assert len(sleepy.frames) == len(pages.clawd_frames("sleepy"))


def test_claude_page_logo_choice():
    ctx = pages.Context(claude=sources.ClaudeUsage(session=50, week=40))
    for logo in ("claude", "clawd"):
        page = {**config.new_page("claude"), "logo": logo}
        out = pages.render_page(page, ctx, 64, 16, datetime(2026, 11, 3, 14, 5))
        assert out.frames[0].size == (64, 16)


def test_panel_texts_are_english_and_fit():
    from pixelpanel.core import font
    for state in sources.TEAMS_STATES:
        long_label, _ = sources.teams_label(state)
        short_label, _ = sources.teams_label(state, short=True)
        assert font.text_width(long_label) <= 64, long_label   # status light: full width
        assert font.text_width(short_label) <= 47, short_label  # next to the dot icon
    assert sources.teams_label("Busy") == ("Busy", (230, 20, 20))
    page = config.new_page("date")
    screen_lines = pages.render_page(page, pages.Context(), 64, 16, datetime(2026, 9, 30, 9, 0))
    assert screen_lines.frames[0].size == (64, 16)
    assert pages.date_lines(datetime(2026, 9, 30)) == ["Wed", "30 Sep"]


def test_wmo_icon():
    assert sources.wmo_icon(0) == "sun"
    assert sources.wmo_icon(0, is_day=False) == "moon"
    assert sources.wmo_icon(63) == "rain"
    assert sources.wmo_icon(1234) == "cloud"


@pytest.mark.parametrize("page_type", list(config.PAGE_TYPES))
def test_every_page_type_renders(page_type):
    ctx = pages.Context(
        teams="Busy",
        claude=sources.ClaudeUsage(session=50, week=40),
        weather=sources.Weather(12.4, "partlycloudy", 14, 6),
    )
    page = config.new_page(page_type)
    out = pages.render_page(page, ctx, 64, 16, datetime(2026, 11, 3, 14, 5))
    if page_type == "native_clock":
        assert isinstance(out, pages.Native)
    else:
        assert isinstance(out, Rendered) and out.frames[0].size == (64, 16)


def test_signature_changes_with_data():
    page = config.new_page("teams")
    now = datetime(2026, 11, 3, 14, 5)
    assert pages.signature(page, pages.Context(teams="Busy"), now) != pages.signature(page, pages.Context(teams="Away"), now)
    clock = config.new_page("clock")
    assert pages.signature(clock, pages.Context(), now) != pages.signature(clock, pages.Context(), now.replace(minute=6))


GREEN = (0, 200, 60)
NOW = datetime(2026, 11, 3, 14, 5)


def _row(img, y, x0=0, x1=None):
    return [img.getpixel((x, y)) for x in range(x0, x1 if x1 is not None else img.width)]


@pytest.mark.parametrize("bars", [1, 2, 3])
def test_status_bars_have_the_chosen_width_and_do_not_cover_content(bars):
    page = config.new_page("text")
    inner = pages.render_page(page, pages.Context(), pages.inner_width(64, bars), 16, NOW)
    framed = pages.with_status_bars(inner, GREEN, bars)
    f = framed.frames[0]
    assert f.size == (64, 16)
    for x in list(range(bars)) + list(range(64 - bars, 64)):
        assert all(f.getpixel((x, y)) == GREEN for y in range(16))
    for x in (bars, 63 - bars):  # one dark column between stripe and content (emoji, icons)
        assert all(f.getpixel((x, y)) == (0, 0, 0) for y in range(16))
    assert f.crop((bars + 1, 0, 63 - bars, 16)).tobytes() == inner.frames[0].tobytes()


def test_clock_digits_keep_a_gap_above_the_weekday_bar():
    f = pages.render_page(config.new_page("clock"), pages.Context(), 64, 16, NOW).frames[0]
    assert all(p == (0, 0, 0) for p in _row(f, 14)), "row 14 must stay dark between digits and bar"
    assert any(p != (0, 0, 0) for p in _row(f, 15)), "weekday bar in the bottom row"
    assert any(p != (0, 0, 0) for p in _row(f, 1)), "digits stay big (two times)"


def test_claude_percent_keeps_a_gap_above_the_progress_bar():
    page = {**config.new_page("claude"), "show": "session"}
    ctx = pages.Context(claude=sources.ClaudeUsage(session=50, week=40))
    f = pages.render_page(page, ctx, 64, 16, NOW).frames[0]
    assert all(p == (0, 0, 0) for p in _row(f, 14, 17)), "row 14 dark between number and bar"
    assert any(p != (0, 0, 0) for p in _row(f, 15, 17)), "progress bar in the bottom row"


def test_default_light_states_leave_rotation_for_available():
    light = config.default_config()["teams"]["light_states"]
    assert "Available" not in light and "Away" not in light


def _engine(pages_cfg, teams_state=None):
    from pixelpanel.engine import Engine
    cfg = config.default_config()
    cfg["pages"] = pages_cfg
    e = Engine(cfg)
    e.ctx.teams = teams_state
    return e


def test_rotation_advances_after_duration():
    ps = [config.new_page(t) for t in ("clock", "claude", "teams")]
    for p in ps:
        p["duration"] = 10
    e = _engine(ps, "Available")
    seen = [e._choose(e.config, t)[0]["type"] for t in (100, 105, 111, 116, 122, 133)]
    assert seen == ["clock", "clock", "claude", "claude", "teams", "clock"]


def test_slow_tick_does_not_shorten_the_first_page(monkeypatch):
    """The page's time starts when it is chosen, not when a slow tick (network, connect) began."""
    import asyncio

    from pixelpanel import engine as eng_mod
    ps = [config.new_page("clock"), config.new_page("claude")]
    e = _engine(ps, "Available")
    clock = iter([100.0, 108.5])  # tick starts at 100, sources + connect take 8.5 s
    monkeypatch.setattr(eng_mod, "_clock", lambda: next(clock, 108.5))

    async def slow_sources(cfg, now_m):
        return False

    async def nothing(*a, **k):
        return None

    shown = []

    async def show(page, label, light):
        shown.append(page["type"])

    monkeypatch.setattr(e, "_update_sources", slow_sources)
    monkeypatch.setattr(e, "_ensure_connection", nothing)
    monkeypatch.setattr(e, "_show", show)
    asyncio.run(e._tick())
    assert e._rot_started == 108.5 and shown == ["clock"]


def test_status_light_pauses_rotation_only_for_checked_states():
    ps = [config.new_page("clock"), config.new_page("claude")]
    e = _engine(ps, "Busy")
    assert e._choose(e.config, 100)[2] is True
    e.ctx.teams = "Available"
    assert e._choose(e.config, 101)[2] is False


def test_no_active_page_falls_back_to_clock():
    ps = [config.new_page("claude")]
    ps[0]["enabled"] = False
    e = _engine(ps, "Available")
    page, _label, light = e._choose(e.config, 100)
    assert page is not None and page["type"] == "clock" and not light


def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "c.json")
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    cfg = config.load()
    cfg["brightness"] = 33
    config.save(cfg)
    assert config.load()["brightness"] == 33
