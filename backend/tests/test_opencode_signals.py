from __future__ import annotations

from app.opencode_signals import classify_signal, should_open_ui, task_mentions_browser


def test_classify_signal_detects_browser_activity() -> None:
    assert (
        classify_signal(
            {
                "type": "tool.execute.after",
                "summary": "OpenCode ran webfetch against https://example.com/docs",
            }
        )
        == "browser"
    )


def test_classify_signal_detects_intervention_activity() -> None:
    assert (
        classify_signal(
            {
                "event_type": "permission.asked",
                "summary_text": "Approval required before continuing",
            }
        )
        == "intervention"
    )


def test_should_open_ui_ignores_plain_local_work() -> None:
    assert (
        should_open_ui(
            {
                "event_type": "tool_start",
                "summary_text": "Reading local repository files",
                "meta": {"browser_candidate": False},
            }
        )
        is False
    )


def test_task_mentions_browser_uses_shared_browser_tokens() -> None:
    assert task_mentions_browser("Search the web for docs") is True
    assert task_mentions_browser("Inspect local code only") is False
