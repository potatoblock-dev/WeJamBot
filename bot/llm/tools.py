"""Reply-path LLM tools: Beijing time and lightweight web search."""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse
from zoneinfo import ZoneInfo

import httpx

_log = logging.getLogger(__name__)

_BEIJING = ZoneInfo("Asia/Shanghai")
_MAX_QUERY_LEN = 200
_MAX_SNIPPET = 400
_DDG_INSTANT = "https://api.duckduckgo.com/"
_DDG_HTML = "https://html.duckduckgo.com/html/"
_WIKI_API = "https://zh.wikipedia.org/w/api.php"
_BING_HTML = "https://cn.bing.com/search"
_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# OpenAI-compatible tool schemas exposed to the chat model.
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_beijing_time",
            "description": (
                "返回当前北京时间（中国标准时间 CST，UTC+8）。"
                "问几点、今天几号、星期几时务必调用，勿猜测。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "联网搜索公开网页摘要，用于查事实、新闻、资料。"
                "勿用于私人信息或未经请求的长篇检索；查询用中文或英文均可。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词或短句",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
]

def tools_system_hint(*, web_search_on: bool = True) -> str:
    """Build a system-prompt reminder matching the tools actually enabled."""
    parts: list[str] = ["【可用工具】"]
    parts.append("需要准确北京时间请调用 get_beijing_time。")
    if web_search_on:
        parts.append("需要查网上公开事实请调用 web_search。勿编造时间或新闻。")
    else:
        parts.append("勿编造精确时间。")
    return "".join(parts)


def get_beijing_time() -> str:
    """Return the current wall clock in Asia/Shanghai as a readable string."""
    now = datetime.now(_BEIJING)
    weekdays = "一二三四五六日"
    wd = weekdays[now.weekday()]
    return (
        f"{now.strftime('%Y-%m-%d %H:%M:%S')} 星期{wd} "
        f"（北京时间 / CST UTC+8）"
    )


async def web_search(query: str, *, max_results: int = 5) -> str:
    """Search the public web and return a short text digest for the model."""
    q = " ".join(str(query or "").split())
    if not q:
        return "（搜索失败：空查询）"
    if len(q) > _MAX_QUERY_LEN:
        q = q[:_MAX_QUERY_LEN]
    limit = max(1, min(8, int(max_results)))
    # Wikipedia is the most reliable from CN; Bing/DDG are best-effort scrapes.
    backends = (
        ("wikipedia", _wikipedia_zh),
        ("bing", lambda query: _bing_html(query, limit=limit)),
        ("ddg_instant", _duckduckgo_instant),
        ("ddg_html", lambda query: _duckduckgo_html(query, limit=limit)),
    )
    errors: list[str] = []
    for name, runner in backends:
        try:
            text = await runner(q)
            if text and text.strip():
                return text.strip()
        except Exception as exc:
            errors.append(f"{name}:{type(exc).__name__}")
            _log.warning("web_search backend=%s failed query=%r err=%s", name, q, exc)
    if errors:
        _log.info("web_search all backends missed query=%r errors=%s", q, errors)
        return "（搜索暂时不可用，请稍后重试或改用常识简答）"
    return "（未找到相关公开结果）"


async def run_tool(name: str, arguments_json: str, *, max_results: int = 5) -> str:
    """Dispatch one tool call by name; unknown names return an error string."""
    args = _parse_args(arguments_json)
    if name == "get_beijing_time":
        return get_beijing_time()
    if name == "web_search":
        return await web_search(str(args.get("query") or ""), max_results=max_results)
    return f"（未知工具：{name}）"


def enabled_tool_definitions(*, web_search_on: bool) -> list[dict[str, Any]]:
    """Filter TOOL_DEFINITIONS by feature flags."""
    out: list[dict[str, Any]] = []
    for item in TOOL_DEFINITIONS:
        fname = str(item.get("function", {}).get("name") or "")
        if fname == "web_search" and not web_search_on:
            continue
        out.append(item)
    return out


def _parse_args(raw: str) -> dict[str, Any]:
    """Parse model-supplied JSON arguments; bad JSON becomes empty dict."""
    text = (raw or "").strip() or "{}"
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


async def _wikipedia_zh(query: str) -> str:
    """Resolve a Chinese Wikipedia page summary when the hit is relevant enough."""
    if _looks_time_sensitive(query):
        # News / "today" queries rarely have a useful encyclopedia page.
        return ""
    search_q = _wiki_search_term(query)
    if not search_q:
        return ""
    params = {
        "action": "opensearch",
        "search": search_q,
        "limit": "3",
        "namespace": "0",
        "format": "json",
    }
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        response = await client.get(_WIKI_API, params=params, headers=_HTTP_HEADERS)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list) or len(data) < 2 or not data[1]:
            return ""
        title = str(data[1][0])
        # Score relevance against both the cleaned term and the raw query.
        if not (
            _wiki_title_relevant(search_q, title)
            or _wiki_title_relevant(query, title)
        ):
            return ""
        summary = await client.get(
            "https://zh.wikipedia.org/api/rest_v1/page/summary/"
            + quote(title, safe=""),
            headers=_HTTP_HEADERS,
        )
        summary.raise_for_status()
        body = summary.json()
    if not isinstance(body, dict):
        return ""
    extract = str(body.get("extract") or "").strip()
    page_url = str(
        (body.get("content_urls") or {}).get("desktop", {}).get("page") or ""
    )
    if not extract:
        return ""
    if not (
        _wiki_extract_relevant(search_q, title, extract)
        or _wiki_extract_relevant(query, title, extract)
    ):
        return ""
    line = f"维基百科 · {title}：{_clip(extract)}"
    if page_url:
        line += f"\n来源：{page_url}"
    return line


_WIKI_FILLER = re.compile(
    r"(是什么|是什麼|什么是|什麼是|简介|介紹|介绍|意思|怎么|怎麼|"
    r"请|請|查一下|搜一下|告诉我|告訴我|介绍一下|介紹一下)"
)


def _wiki_search_term(query: str) -> str:
    """Strip question filler so opensearch gets an entity-like term."""
    cleaned = _WIKI_FILLER.sub(" ", query or "")
    cleaned = re.sub(r"[?？!！。．.、,，:：;；\"'“”‘’]", " ", cleaned)
    cleaned = " ".join(cleaned.split())
    return cleaned or (query or "").strip()


_TIME_SENSITIVE = re.compile(
    r"(今日|今天|昨晚|昨夜|刚刚|最新|实时|新闻|头条|热搜|股价|天气|比分|赛况)"
)


def _looks_time_sensitive(query: str) -> bool:
    """True when the query likely needs fresh SERP results, not an encyclopedia page."""
    return bool(_TIME_SENSITIVE.search(query or ""))


def _normalize_for_match(text: str) -> str:
    """Lowercase and strip punctuation/spaces for loose relevance checks."""
    raw = (text or "").casefold()
    return re.sub(r"[\s\-_/\\|·•,.，。:：;；!！?？\"'“”‘’()（）\[\]【】<>《》]+", "", raw)


def _wiki_title_relevant(query: str, title: str) -> bool:
    """Accept a wiki title only when it shares meaningful tokens with the query."""
    q = _normalize_for_match(query)
    t = _normalize_for_match(title)
    if not q or not t:
        return False
    if q in t or t in q:
        return True
    # Short CJK: every unique query char must appear in the title (故宫 ⊂ 北京故宫).
    if len(q) <= 8:
        return all(ch in t for ch in set(q))
    # Longer queries: require at least one contiguous 2+ char chunk from query in title.
    for i in range(len(q) - 1):
        if q[i : i + 2] in t:
            return True
    return False


def _wiki_extract_relevant(query: str, title: str, extract: str) -> bool:
    """Reject summaries that barely mention the query topic."""
    if _wiki_title_relevant(query, title):
        return True
    body = _normalize_for_match(extract)
    q = _normalize_for_match(query)
    if len(q) >= 2 and q[:2] in body:
        return True
    return False


async def _bing_html(query: str, *, limit: int) -> str:
    """Scrape cn.bing.com result titles and snippets (best-effort)."""
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        response = await client.get(
            _BING_HTML,
            params={"q": query},
            headers=_HTTP_HEADERS,
        )
        response.raise_for_status()
        html = response.text
    rows = _parse_bing_html(html, limit=limit)
    if not rows:
        return ""
    lines = [f"搜索「{query}」："]
    for idx, row in enumerate(rows, start=1):
        title = row.get("title") or ""
        snippet = row.get("snippet") or ""
        url = row.get("url") or ""
        block = f"{idx}. {title}"
        if snippet:
            block += f"\n   {_clip(snippet, 280)}"
        if url:
            block += f"\n   {url}"
        lines.append(block)
    return "\n".join(lines)


def _parse_bing_html(html: str, *, limit: int) -> list[dict[str, str]]:
    """Extract Bing SERP title / url / snippet blocks."""
    out: list[dict[str, str]] = []
    # Bing wraps organic results in <li class="b_algo"> …
    blocks = re.findall(
        r'<li class="b_algo".*?</li>',
        html or "",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for block in blocks:
        href_m = re.search(
            r'<h2[^>]*>\s*<a[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>',
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not href_m:
            continue
        title = _strip_tags(href_m.group("title"))
        url = href_m.group("url")
        snip_m = re.search(
            r'class="b_caption"[^>]*>.*?<p[^>]*>(?P<snippet>.*?)</p>',
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        snippet = _strip_tags(snip_m.group("snippet")) if snip_m else ""
        if not title or _is_junk_result(title, url):
            continue
        out.append({"title": title, "snippet": snippet, "url": url})
        if len(out) >= limit:
            break
    return out


def _is_junk_result(title: str, url: str) -> bool:
    """Filter obvious ad / interstitial Bing rows that are not about the query."""
    t = (title or "").strip().casefold()
    u = (url or "").casefold()
    if t in {"google", "bing", "microsoft"}:
        return True
    junk_hosts = (
        "translate.google.",
        "account.aliyun.",
        "login.live.",
        "microsoftonline.",
    )
    return any(host in u for host in junk_hosts)


async def _duckduckgo_instant(query: str) -> str:
    """Prefer DuckDuckGo Instant Answer JSON when it has useful fields."""
    params = {
        "q": query,
        "format": "json",
        "no_html": "1",
        "skip_disambig": "1",
    }
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        response = await client.get(
            _DDG_INSTANT,
            params=params,
            headers=_HTTP_HEADERS,
        )
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict):
        return ""
    parts: list[str] = []
    heading = str(data.get("Heading") or "").strip()
    abstract = str(data.get("AbstractText") or "").strip()
    abstract_url = str(data.get("AbstractURL") or "").strip()
    answer = str(data.get("Answer") or "").strip()
    definition = str(data.get("Definition") or "").strip()
    if answer:
        parts.append(f"速答：{_clip(answer)}")
    if abstract:
        label = heading or "摘要"
        line = f"{label}：{_clip(abstract)}"
        if abstract_url:
            line += f"\n来源：{abstract_url}"
        parts.append(line)
    if definition and definition not in abstract:
        parts.append(f"释义：{_clip(definition)}")
    related = data.get("RelatedTopics")
    if isinstance(related, list):
        for item in related[:5]:
            text = _related_text(item)
            if text:
                parts.append(f"- {_clip(text, 240)}")
    return "\n".join(parts).strip()


async def _duckduckgo_html(query: str, *, limit: int) -> str:
    """Fallback: scrape DuckDuckGo HTML result titles and snippets."""
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        response = await client.post(
            _DDG_HTML,
            data={"q": query},
            headers=_HTTP_HEADERS,
        )
        response.raise_for_status()
        html = response.text
    results = _parse_ddg_html(html, limit=limit)
    if not results:
        return ""
    lines = [f"搜索「{query}」："]
    for idx, row in enumerate(results, start=1):
        title = row.get("title") or ""
        snippet = row.get("snippet") or ""
        url = row.get("url") or ""
        block = f"{idx}. {title}"
        if snippet:
            block += f"\n   {_clip(snippet, 280)}"
        if url:
            block += f"\n   {url}"
        lines.append(block)
    return "\n".join(lines)


def _parse_ddg_html(html: str, *, limit: int) -> list[dict[str, str]]:
    """Extract result blocks from DuckDuckGo HTML (best-effort, no extra deps)."""
    out: list[dict[str, str]] = []
    # result__a links carry the title; result__snippet holds the blurb.
    pattern = re.compile(
        r'class="result__a"[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>'
        r'.*?class="result__snippet"[^>]*>(?P<snippet>.*?)</(?:a|td|div)',
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(html):
        title = _strip_tags(match.group("title"))
        snippet = _strip_tags(match.group("snippet"))
        url = _unwrap_ddg_redirect(match.group("url"))
        if not title:
            continue
        out.append({"title": title, "snippet": snippet, "url": url})
        if len(out) >= limit:
            break
    if out:
        return out
    # Simpler fallback: just titles.
    for match in re.finditer(
        r'class="result__a"[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>',
        html,
        re.IGNORECASE | re.DOTALL,
    ):
        title = _strip_tags(match.group("title"))
        if not title:
            continue
        out.append(
            {
                "title": title,
                "snippet": "",
                "url": _unwrap_ddg_redirect(match.group("url")),
            }
        )
        if len(out) >= limit:
            break
    return out


def _unwrap_ddg_redirect(url: str) -> str:
    """Pull the real target out of DuckDuckGo uddg= redirect links when present."""
    raw = url or ""
    if "uddg=" in raw:
        qs = parse_qs(urlparse(raw).query)
        target = qs.get("uddg") or []
        if target:
            return unquote(target[0])
    return raw


def _related_text(item: Any) -> str:
    """Flatten one RelatedTopics entry (may nest under Topics)."""
    if isinstance(item, dict):
        text = str(item.get("Text") or "").strip()
        if text:
            return text
        topics = item.get("Topics")
        if isinstance(topics, list) and topics:
            return _related_text(topics[0])
    return ""


def _strip_tags(html: str) -> str:
    """Remove tags, unescape entities, and collapse whitespace."""
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = html_lib.unescape(text)
    return " ".join(text.split())


def _clip(text: str, limit: int = _MAX_SNIPPET) -> str:
    """Truncate long snippets for the tool result budget."""
    s = " ".join((text or "").split())
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 1)] + "…"
