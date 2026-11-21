"""Unit tests for reply-path LLM tools (time + search helpers)."""

from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from bot.llm import tools as llm_tools


def test_get_beijing_time_contains_cst_marker() -> None:
    """Beijing time string includes date and CST timezone label."""
    text = llm_tools.get_beijing_time()
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    assert now.strftime("%Y-%m-%d") in text
    assert "北京时间" in text
    assert "UTC+8" in text


def test_run_tool_beijing_time() -> None:
    """Dispatcher routes get_beijing_time without arguments."""
    text = asyncio.run(llm_tools.run_tool("get_beijing_time", "{}"))
    assert "北京时间" in text


def test_run_tool_unknown() -> None:
    """Unknown tool names return a clear error string."""
    text = asyncio.run(llm_tools.run_tool("nope", "{}"))
    assert "未知工具" in text


def test_web_search_empty_query() -> None:
    """Empty search query fails closed without calling the network."""
    text = asyncio.run(llm_tools.web_search("   "))
    assert "空查询" in text


def test_parse_ddg_html_extracts_results() -> None:
    """HTML parser pulls title/snippet from DuckDuckGo-shaped markup."""
    html = """
    <div class="result">
      <a class="result__a" href="https://example.com/a">Hello Title</a>
      <a class="result__snippet">Some snippet about cats.</a>
    </div>
    """
    rows = llm_tools._parse_ddg_html(html, limit=3)
    assert rows
    assert rows[0]["title"] == "Hello Title"
    assert "cats" in rows[0]["snippet"]


def test_enabled_tool_definitions_can_drop_search() -> None:
    """WEB_SEARCH off removes only the search tool schema."""
    both = llm_tools.enabled_tool_definitions(web_search_on=True)
    names = {t["function"]["name"] for t in both}
    assert names == {"get_beijing_time", "web_search"}
    time_only = llm_tools.enabled_tool_definitions(web_search_on=False)
    assert {t["function"]["name"] for t in time_only} == {"get_beijing_time"}


def test_tools_system_hint_follows_web_search_flag() -> None:
    """System hint must not mention web_search when that tool is disabled."""
    both = llm_tools.tools_system_hint(web_search_on=True)
    assert "web_search" in both
    assert "get_beijing_time" in both
    time_only = llm_tools.tools_system_hint(web_search_on=False)
    assert "get_beijing_time" in time_only
    assert "web_search" not in time_only


def test_wiki_skips_time_sensitive_queries() -> None:
    """News-like queries skip Wikipedia so Bing/DDG can answer."""
    assert llm_tools._looks_time_sensitive("今日新闻")
    assert llm_tools._looks_time_sensitive("北京今天天气")
    assert not llm_tools._looks_time_sensitive("北京故宫")


def test_wiki_title_relevance() -> None:
    """Loose title match accepts 故宫→北京故宫 and rejects unrelated hits."""
    assert llm_tools._wiki_title_relevant("北京故宫", "故宫")
    assert llm_tools._wiki_title_relevant("故宫", "北京故宫")
    assert not llm_tools._wiki_title_relevant("今日新闻", "今日新聞 (德國)")
    assert not llm_tools._wiki_title_relevant("今日新闻", "黎曼猜想")


def test_wiki_search_term_strips_filler() -> None:
    """Question filler is removed before Wikipedia opensearch."""
    assert llm_tools._wiki_search_term("故宫是什么？简介") == "故宫"
    assert llm_tools._wiki_search_term("北京故宫") == "北京故宫"


def test_complete_with_tools_loop(monkeypatch, tmp_path) -> None:
    """LLMClient executes a tool_call then returns the follow-up content."""
    from bot.config import Settings
    from bot.llm.client import LLMClient
    from bot.personas.catalog import PersonaCatalog
    from bot.prompt_book import PromptBook

    data = tmp_path / "data"
    data.mkdir()
    personas = data / "personas"
    personas.mkdir()
    (tmp_path / "personas.toml").write_text('active = ""\n', encoding="utf-8")
    settings = Settings(
        llm_base_url="https://example.invalid/v1",
        llm_api_key="test-key",
        llm_model="test-model",
        llm_timeout_seconds=5.0,
        llm_tools_enabled=True,
        web_search_enabled=True,
        web_search_max_results=3,
        llm_tool_max_rounds=3,
        archive_llm_base_url="https://example.invalid/v1",
        archive_llm_api_key="test-key",
        archive_llm_model="test-archive",
        memory_max_turns=8,
        data_dir=data,
        reply_policy_path=tmp_path / "reply_policy.toml",
        personas_dir=personas,
        personas_index_path=tmp_path / "personas.toml",
        allow_chats_path=data / "allow_chats.txt",
        observe_chats_path=data / "observe_chats.txt",
        bot_mode_path=data / "bot_mode.txt",
        wejam_target="127.0.0.1:7701",
        bot_name="bot",
        coalesce_burst_seconds=30.0,
        coalesce_debounce_seconds=3.0,
    )
    catalog = PersonaCatalog(settings.personas_index_path, settings.personas_dir)
    client = LLMClient(settings, prompts=PromptBook(catalog))

    calls: list[dict] = []

    async def fake_api(url, headers, payload, *, label):
        calls.append(payload)
        if len(calls) == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "get_beijing_time",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        return {
            "choices": [
                {"message": {"role": "assistant", "content": "现在是测试时间。"}}
            ]
        }

    monkeypatch.setattr(client, "_call_api", fake_api)
    reply = asyncio.run(client.complete([], "现在北京几点？"))
    assert reply == "现在是测试时间。"
    assert len(calls) == 2
    assert "tools" in calls[0]
    tool_msgs = [m for m in calls[1]["messages"] if m.get("role") == "tool"]
    assert tool_msgs
    assert "北京时间" in tool_msgs[0]["content"]
    assert any(
        t["function"]["name"] == "get_beijing_time" for t in calls[0]["tools"]
    )
