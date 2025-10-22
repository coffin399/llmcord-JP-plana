# PLANA/llm/plugin/search_agent.py
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import google.generativeai as genai

# 作成したカスタム例外をインポート
from PLANA.llm.error.errors import (
    SearchAPIRateLimitError,
    SearchAPIServerError,
    SearchAPIError,
    SearchExecutionError,
    SearchAgentError
)

if TYPE_CHECKING:
    from discord.ext import commands

logger = logging.getLogger(__name__)


class SearchAgent:
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
        gcfg = self.bot.cfg.get("search_agent")
        if not gcfg or not gcfg.get("api_key"):
            logger.error("SearchAgent config (api_key) is missing. Search will be disabled.")
            self.model = None
            return

        try:
            genai.configure(api_key=gcfg["api_key"])
            self.model = genai.GenerativeModel(gcfg["model"])
            self.format_control = gcfg["format_control"]
            self.tools = [{"google_search": {}}]
            logger.info("SearchAgent initialized successfully with Google GenAI.")
        except Exception as e:
            logger.error(f"Failed to initialize Google GenAI for SearchAgent: {e}", exc_info=True)
            self.model = None

    async def _google_search(self, query: str) -> str:
        if not self.model:
            raise SearchExecutionError("SearchAgent is not properly initialized.")

        retries = 2
        delay = 1.5

        for attempt in range(retries + 1):
            try:
                # generate_contentは同期メソッドのため、asyncio.to_threadで実行
                response = await asyncio.to_thread(
                    self.model.generate_content,
                    "**[DeepResearch Request]:** " + query + "\n" + self.format_control,
                    tools=self.tools,
                )
                return response.text
            except genai.errors.ResourceExhaustedError as e:
                # 429 Rate Limit Error
                raise SearchAPIRateLimitError("Google Search API rate limit was reached.", original_exception=e)
            except (genai.errors.InternalServerError, genai.errors.ServiceUnavailableError) as e:
                # 5xx Server-side Errors
                logger.warning(f"SearchAgent server error (attempt {attempt + 1}/{retries + 1}): {e}")
                if attempt < retries:
                    await asyncio.sleep(delay * (attempt + 1))
                    continue
                raise SearchAPIServerError("Google Search API server-side error after retries.", original_exception=e)
            except genai.errors.GoogleAPICallError as e:
                # その他のGoogle API関連エラー
                logger.error(f"Search Agent unexpected API error: {e}")
                raise SearchAPIError(f"An unexpected API error occurred: {str(e)}", original_exception=e)
            except Exception as e:
                # API以外の予期しない実行時エラー
                logger.error(f"Search Agent unexpected execution error: {e}", exc_info=True)
                raise SearchExecutionError(f"An unexpected error occurred during search: {str(e)}",
                                           original_exception=e)

        # ループがリトライを尽くしても完了しなかった場合
        raise SearchExecutionError("Search failed for an unknown reason after all retries.")

    async def run(self, *, arguments: dict, bot, channel_id: int) -> str:
        query = arguments.get("query", "")
        if not query:
            raise SearchExecutionError("Query cannot be empty.")

        # _google_searchは例外を発生させる可能性があるため、呼び出し側(llm_cog.py)で処理する
        return await self._google_search(query)