"""Window-title status emoji from process health and log lines."""

from bot.admin.status_light import (
    LIGHT_BUSY,
    LIGHT_OFF,
    LIGHT_ON,
    LIGHT_WARN,
    is_replying,
    title_light,
    window_title,
)


def test_window_title_puts_emoji_after_name() -> None:
    """The light sits to the right of the WeJam console name."""
    assert window_title(LIGHT_ON) == "WeJam 闲聊控制台  🟢"


def test_title_light_off_when_stopped() -> None:
    """Stopped process is red even if the last line looks healthy."""
    assert (
        title_light(
            running=False,
            healthy=False,
            log_lines=["2026 INFO bot.chat replied session=x"],
        )
        == LIGHT_OFF
    )


def test_title_light_busy_while_replying() -> None:
    """Newest in-flight marker is blue."""
    lines = [
        "inbound session=a",
        "正在回复 session=a user=甲",
    ]
    assert (
        title_light(running=True, healthy=True, log_lines=lines) == LIGHT_BUSY
    )
    assert is_replying(lines) is True


def test_title_light_on_after_replied() -> None:
    """replied ends the busy cycle."""
    lines = [
        "正在回复 session=a user=甲",
        "replied session=a preview='hi'",
    ]
    assert title_light(running=True, healthy=True, log_lines=lines) == LIGHT_ON
    assert is_replying(lines) is False


def test_title_light_warn() -> None:
    """Latest WARNING is yellow while still running."""
    lines = ["2026-10-05 00:00:00,000 WARNING bot.chat send_text failed"]
    assert title_light(running=True, healthy=True, log_lines=lines) == LIGHT_WARN
