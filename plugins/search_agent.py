from __future__ import annotations
import json, asyncio, logging
from google import genai

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

    def __init__(self, bot) -> None:
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
                    return "[Google Search Error]\n Google検索APIの利用制限 (429: Too Many Requests) に遭遇しました。 Userには時間を置いてから再試行するように伝えてください"
                elif code in [500, 502, 503]:
                    last_exception = e
                    if attempt < retries:
                        await asyncio.sleep(delay)
                        continue
                    return f"[Google Search Error]\n サーバー側の一時的な問題 ({code}) により検索に失敗しました。Userには時間を置いてから再試行するように伝えてください"
                else:
                    logging.error(f"Search Agentの予期しないAPIエラー: {e}")
                    return f"[Google Search Error]\n APIエラーが発生しました: {str(e)}"
            except Exception as e:
                logging.error(f"Search Agentの予期しないエラー: {e}")
                return f"[Google Search Error]\n 予期しないエラーが発生しました: {str(e)}"

        return "[Google Search Error]\n 何らかの理由で検索に失敗しました。"

    async def run(self, *, arguments: dict, bot) -> str:
        query = arguments.get("query", "")
        if not query:
            return "[Google Search Error] query が空です。"
        try:
            return await self._google_search(query)
        except Exception as e:
            logging.error(f"Search Agentの予期しないエラー: {e}")
            return f"[Google Search Error]\n予期しないエラーが発生しました: {str(e)}"