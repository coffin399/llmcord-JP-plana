# PLANA/llm/plugin/search_agent.py
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from google import genai

# 作成したカスタム例外をインポート
from PLANA.llm.error.errors import (
    SearchAPIRateLimitError,
    SearchAPIServerError,
    SearchAPIError,
    SearchExecutionError
)

if TYPE_CHECKING:
    from discord.ext import commands


class SearchAgent():
    name = "search"
    tool_spec = {
        "type": "function",
        "function": {
            "name": name,
            "description": "Run a Google web search and return a report.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _google_search(self, query: str) -> str:
        gcfg = self.bot.cfg["search_agent"]

        client = genai.Client(api_key=gcfg["api_key"])
        retries = 2
        delay = 1.5
        last_exception = None

        for attempt in range(retries + 1):
            try:
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=gcfg["model"],
                    contents="**[DeepResearch Request]:** " + query + "\n" + gcfg["format_control"],
                    config={"tools": [{"google_search": {}}]},
                )
                return response.text
            except genai.errors.APIError as e:
                code = getattr(e, "code", None)
                if code == 429:
                    # エラー文字列を返す代わりに例外を発生させる
                    raise SearchAPIRateLimitError("Google Search API rate limit (429) was reached.",
                                                  original_exception=e)
                elif code in [500, 502, 503]:
                    last_exception = e
                    if attempt < retries:
                        await asyncio.sleep(delay)
                        continue
                    # リトライ後も失敗した場合に例外を発生させる
                    raise SearchAPIServerError(f"Google Search API server-side error ({code}).", original_exception=e)
                else:
                    logging.error(f"Search Agent unexpected API error: {e}")
                    raise SearchAPIError(f"An unexpected API error occurred: {str(e)}", original_exception=e)
            except Exception as e:
                logging.error(f"Search Agent unexpected error: {e}")
                # 予期しないエラーもカスタム例外でラップする
                raise SearchExecutionError(f"An unexpected error occurred during search: {str(e)}",
                                           original_exception=e)

        # ループが正常に完了しなかった場合 (リトライが尽きた場合など)
        if last_exception:
            raise SearchAPIServerError(f"Google Search failed after multiple retries.",
                                       original_exception=last_exception)

        raise SearchExecutionError("Search failed for an unknown reason after all retries.")

    async def run(self, *, arguments: dict, bot, channel_id: int) -> str:
        query = arguments.get("query", "")
        if not query:
            # queryが空の場合も例外を発生させるのが一貫性がある
            raise SearchExecutionError("Query is empty.")

        # _google_searchが例外を発生させるようになったため、このtry-exceptは不要かもしれないが、
        # runメソッドの呼び出し側でハンドリングする想定のため、ここではそのまま呼び出す。
        return await self._google_search(query)