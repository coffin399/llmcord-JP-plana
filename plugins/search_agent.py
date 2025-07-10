from __future__ import annotations
import asyncio
import logging
from typing import Dict, Any, Optional

# ログの初期化を最初に行う
logger = logging.getLogger(__name__)

# MistralAIのライブラリバージョンによってexceptionsモジュールが異なる場合があります
try:
    from mistralai.async_client import MistralAsyncClient
    from mistralai.exceptions import MistralAPIException
except ImportError:
    try:
        from mistralai import MistralAsyncClient

        MistralAPIException = Exception
        logger.warning("MistralAI exceptions module not found, using generic Exception")
    except ImportError:
        logger.error("MistralAI library not found. Please install: pip install mistralai")
        MistralAsyncClient = None
        MistralAPIException = Exception

# MistralAIのバージョンを確認
try:
    import mistralai

    mistral_version = getattr(mistralai, '__version__', 'unknown')
    logger.info(f"MistralAI library version: {mistral_version}")
except Exception as e:
    logger.warning(f"Could not determine MistralAI library version: {e}")


class SearchAgent:
    """
    An agent that uses the Mistral AI's native search tool to answer queries.
    This implementation leverages the `tool_choice="search"` feature available
    in specific Mistral models.
    """
    name = "search"
    tool_spec = {
        "type": "function",
        "function": {
            "name": name,
            "description": "Run a web search using the Mistral AI search tool and return a report.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }

    def __init__(self, bot) -> None:
        self.bot = bot
        self.client: Optional[MistralAsyncClient] = None

        # MistralAsyncClientが利用可能かチェック
        if MistralAsyncClient is None:
            logger.error("MistralAsyncClient is not available. Please install mistralai library.")
            return

        try:
            # config.yamlのキー 'search_agent' を参照します
            mcfg = self.bot.cfg.get("search_agent", {})
            api_key = mcfg.get("api_key")

            if not api_key:
                logger.error("API key not found in configuration under 'search_agent.api_key'")
                return

            self.client = MistralAsyncClient(api_key=api_key)
            logger.info("MistralAsyncClient for SearchAgent initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize MistralAsyncClient for SearchAgent: {e}")

    async def _mistral_search(self, query: str) -> str:
        """
        Performs a web search using the Mistral AI API with the 'search' tool.
        """
        if not self.client:
            return "[Mistral Search Error]\nAPI client is not initialized. Check configuration and logs."

        retries = 2
        delay = 1.5
        mcfg = self.bot.cfg.get("search_agent", {})
        model = mcfg.get("model", "mistral-large-latest")  # デフォルト値を設定

        for attempt in range(retries + 1):
            try:
                # メッセージの形式を明示的に指定
                messages = [
                    {
                        "role": "user",
                        "content": f"Search for information about: {query}"
                    }
                ]

                # 非同期クライアントを使用してチャットを実行
                response = await self.client.chat(
                    model=model,
                    messages=messages,
                    tool_choice="search",
                    temperature=0.1,  # より一貫した結果のために低めの温度を設定
                )

                # レスポンスの内容を取得
                if response.choices and len(response.choices) > 0:
                    message = response.choices[0].message
                    if message.content:
                        return message.content
                    else:
                        return "[Mistral Search Error]\nNo content received from the API."
                else:
                    return "[Mistral Search Error]\nNo response choices received from the API."

            except Exception as e:
                # MistralAIのライブラリによってはHTTPErrorやRequestExceptionなどの場合もあります
                error_message = str(e)

                # レート制限のチェック（エラーメッセージで判定）
                if "429" in error_message or "rate limit" in error_message.lower():
                    msg = "Rate limit encountered. Please wait and try again."
                    logger.warning(f"Mistral Search: {msg}")
                    return f"[Mistral Search Error]\n{msg}"

                # サーバーエラーのチェック
                if any(code in error_message for code in ["500", "502", "503"]) and attempt < retries:
                    logger.warning(f"Mistral Search: Server error detected. Retrying in {delay}s...")
                    await asyncio.sleep(delay)
                    delay *= 2  # 指数バックオフ
                    continue

                logger.error(f"Mistral API error in SearchAgent: {error_message}", exc_info=True)
                if attempt < retries:
                    logger.info(f"Retrying after API error... (attempt {attempt + 1}/{retries})")
                    await asyncio.sleep(delay)
                    continue
                return f"[Mistral Search Error]\nAPI error: {error_message}"

            except asyncio.TimeoutError:
                logger.error("Timeout occurred during Mistral API call")
                if attempt < retries:
                    logger.info(f"Retrying after timeout... (attempt {attempt + 1}/{retries})")
                    await asyncio.sleep(delay)
                    continue
                return "[Mistral Search Error]\nRequest timeout. Please try again."

            except Exception as e:
                logger.error(f"Unexpected error in SearchAgent: {e}", exc_info=True)
                if attempt < retries:
                    logger.info(f"Retrying after unexpected error... (attempt {attempt + 1}/{retries})")
                    await asyncio.sleep(delay)
                    continue
                return f"[Mistral Search Error]\nUnexpected error: {e}"

        return "[Mistral Search Error]\nFailed to get a response after several retries."

    async def run(self, *, arguments: Dict[str, Any], bot) -> str:
        """
        The main entry point for the agent, called by the LLM cog.
        """
        try:
            query = arguments.get("query", "").strip()
            if not query:
                return "[Mistral Search Error] The 'query' argument is empty or invalid."

            logger.info(f"SearchAgent executing query: {query}")
            result = await self._mistral_search(query)
            logger.info("SearchAgent completed successfully")
            return result

        except Exception as e:
            logger.error(f"Unexpected error in SearchAgent's run method: {e}", exc_info=True)
            return f"[Mistral Search Error]\nUnexpected error in run method: {e}"

    def is_available(self) -> bool:
        """
        Check if the SearchAgent is properly configured and available.
        """
        return self.client is not None