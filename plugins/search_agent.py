from __future__ import annotations
import asyncio
import logging
from typing import Dict, Any, Optional, List
import json

# ログの初期化を最初に行う
logger = logging.getLogger(__name__)

# 最新のMistralAI クライアントライブラリのインポート
try:
    from mistralai import Mistral
    from mistralai.models import ChatCompletionRequest, ChatMessage

    MistralAPIException = Exception
    logger.info("Using new Mistral client library")
except ImportError:
    try:
        # 古いバージョンのフォールバック
        from mistralai.async_client import MistralAsyncClient
        from mistralai.exceptions import MistralAPIException
        from mistralai.models.chat_completion import ChatMessage, ChatCompletionResponse

        Mistral = None
        logger.info("Using legacy MistralAsyncClient")
    except ImportError:
        logger.error("MistralAI library not found. Please install: pip install mistralai")
        Mistral = None
        MistralAsyncClient = None
        MistralAPIException = Exception


class SearchAgent:
    name = "search"
    tool_spec = {
        "type": "function",
        "function": {
            "name": name,
            "description": "Run a web search using the Mistral AI search tool and return a comprehensive report.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query to execute"
                    }
                },
                "required": ["query"],
            },
        },
    }

    # サポートされている検索対応モデルのリスト
    SUPPORTED_MODELS = [
        "mistral-large-latest",
        "mistral-large-2407",
        "mistral-large-2411",
        "mistral-medium-2505",
        "mistral-medium-latest"
    ]

    def __init__(self, bot) -> None:
        self.bot = bot
        self.client = None
        self.model = "mistral-large-latest"  # デフォルトモデル
        self.max_retries = 3
        self.base_delay = 1.0
        self.timeout = 30.0
        self.initialization_error = None
        self.use_legacy_client = False

        try:
            # config.yamlのキー 'search_agent' を参照します
            logger.info("Loading configuration...")
            mcfg = self.bot.cfg.get("search_agent", {})
            logger.info(f"Configuration loaded: {list(mcfg.keys())}")

            api_key = mcfg.get("api_key")

            if not api_key:
                error_msg = "API key not found in configuration under 'search_agent.api_key'"
                logger.error(error_msg)
                logger.error("Please add the following to your config.yaml:")
                logger.error("search_agent:")
                logger.error("  api_key: 'your_mistral_api_key_here'")
                logger.error("  model: 'mistral-large-latest'")
                self.initialization_error = error_msg
                return

            # API keyの長さをチェック（セキュリティのため最初の数文字のみ表示）
            logger.info(f"API key found (starts with: {api_key[:8]}...)")

            # 新しいクライアントを試してから古いクライアントにフォールバック
            try:
                if Mistral:
                    # 新しいクライアント（v1.0.0以降）
                    self.client = Mistral(api_key=api_key)
                    logger.info("New Mistral client initialized successfully.")
                    self.use_legacy_client = False
                else:
                    # 古いクライアント（v0.x.x）
                    self.client = MistralAsyncClient(api_key=api_key)
                    logger.info("Legacy MistralAsyncClient initialized successfully.")
                    self.use_legacy_client = True
            except Exception as e:
                logger.error(f"Failed to initialize Mistral client: {e}")
                self.client = None
                self.initialization_error = str(e)

            # 設定からモデルを取得し、サポートされているかチェック
            configured_model = mcfg.get("model", "mistral-large-latest")
            if configured_model in self.SUPPORTED_MODELS:
                self.model = configured_model
                logger.info(f"Using configured model: {self.model}")
            else:
                logger.warning(f"Model '{configured_model}' may not support search. Using default: {self.model}")

            # その他の設定
            self.max_retries = mcfg.get("max_retries", 3)
            self.base_delay = mcfg.get("base_delay", 1.0)
            self.timeout = mcfg.get("timeout", 30.0)

        except Exception as e:
            error_msg = f"Failed to initialize Mistral client for SearchAgent: {e}"
            logger.error(error_msg, exc_info=True)
            self.initialization_error = error_msg
            self.client = None

    async def _mistral_search_new(self, query: str) -> str:
        """新しいMistralクライアントを使用した検索"""
        try:
            messages = [
                ChatMessage(
                    role="user",
                    content=f"Please search for information about: {query}\n\nProvide a comprehensive summary of the search results including key findings, relevant details, and sources when available."
                )
            ]

            # 新しいクライアントのAPIを使用
            response = await asyncio.wait_for(
                self.client.chat.complete_async(
                    model=self.model,
                    messages=messages,
                    tools=[{"type": "web_search"}],
                    temperature=0.1,
                    max_tokens=4000,
                ),
                timeout=self.timeout
            )

            if response.choices and len(response.choices) > 0:
                message = response.choices[0].message
                if message.content:
                    content = message.content.strip()
                    if content:
                        logger.info(f"Mistral Search successful for query: {query}")
                        return self._format_search_result(content, query)
                    else:
                        return "[Mistral Search Error]\nEmpty response content received."
                else:
                    return "[Mistral Search Error]\nNo content received from the API."
            else:
                return "[Mistral Search Error]\nNo response choices received from the API."

        except Exception as e:
            logger.error(f"Error with new Mistral client: {e}")
            raise

    async def _mistral_search_legacy(self, query: str) -> str:
        """古いMistralAsyncClientを使用した検索"""
        try:
            messages = [
                {
                    "role": "user",
                    "content": f"Please search for information about: {query}\n\nProvide a comprehensive summary of the search results including key findings, relevant details, and sources when available."
                }
            ]

            response = await asyncio.wait_for(
                self.client.chat(
                    model=self.model,
                    messages=messages,
                    tool_choice="search",
                    temperature=0.1,
                    max_tokens=4000,
                ),
                timeout=self.timeout
            )

            if response.choices and len(response.choices) > 0:
                message = response.choices[0].message
                if message.content:
                    content = message.content.strip()
                    if content:
                        logger.info(f"Mistral Search successful for query: {query}")
                        return self._format_search_result(content, query)
                    else:
                        return "[Mistral Search Error]\nEmpty response content received."
                else:
                    return "[Mistral Search Error]\nNo content received from the API."
            else:
                return "[Mistral Search Error]\nNo response choices received from the API."

        except Exception as e:
            logger.error(f"Error with legacy Mistral client: {e}")
            raise

    async def _mistral_search(self, query: str) -> str:
        if not self.client:
            error_details = []
            error_details.append("API client is not initialized. Check configuration and logs.")

            if self.initialization_error:
                error_details.append(f"Initialization error: {self.initialization_error}")

            if not Mistral and not MistralAsyncClient:
                error_details.append("MistralAI library not installed. Run: pip install mistralai")

            # 設定の診断
            try:
                mcfg = self.bot.cfg.get("search_agent", {})
                if not mcfg:
                    error_details.append("No 'search_agent' configuration found in config.yaml")
                else:
                    api_key = mcfg.get("api_key")
                    if not api_key:
                        error_details.append("No 'api_key' found in search_agent configuration")
                    else:
                        error_details.append(f"API key present (length: {len(api_key)})")
            except Exception as e:
                error_details.append(f"Error checking configuration: {e}")

            return "[Mistral Search Error]\n" + "\n".join(error_details)

        if not query.strip():
            return "[Mistral Search Error]\nEmpty query provided."

        for attempt in range(self.max_retries + 1):
            try:
                logger.debug(f"Mistral Search attempt {attempt + 1}: {query}")

                # 新しいクライアントか古いクライアントかを判断
                if self.use_legacy_client:
                    result = await self._mistral_search_legacy(query)
                else:
                    result = await self._mistral_search_new(query)

                return result

            except asyncio.TimeoutError:
                logger.warning(f"Mistral Search timeout on attempt {attempt + 1}")
                if attempt < self.max_retries:
                    await asyncio.sleep(self.base_delay * (2 ** attempt))
                    continue
                return f"[Mistral Search Error]\nRequest timeout after {self.timeout}s. Please try again."

            except Exception as e:
                error_message = str(e)
                logger.error(f"Mistral API error on attempt {attempt + 1}: {error_message}")

                # レート制限のチェック
                if self._is_rate_limit_error(error_message):
                    msg = "Rate limit encountered. Please wait and try again."
                    logger.warning(f"Mistral Search: {msg}")
                    return f"[Mistral Search Error]\n{msg}"

                # サーバーエラーのチェック
                if self._is_server_error(error_message) and attempt < self.max_retries:
                    delay = self.base_delay * (2 ** attempt)
                    logger.warning(f"Mistral Search: Server error detected. Retrying in {delay}s...")
                    await asyncio.sleep(delay)
                    continue

                # その他のエラー
                if attempt < self.max_retries:
                    delay = self.base_delay * (2 ** attempt)
                    logger.info(f"Retrying after API error... (attempt {attempt + 1}/{self.max_retries}) in {delay}s")
                    await asyncio.sleep(delay)
                    continue

                return f"[Mistral Search Error]\nAPI error: {error_message}"

        return "[Mistral Search Error]\nFailed to get a response after several retries."

    def _is_rate_limit_error(self, error_message: str) -> bool:
        """レート制限エラーかどうかをチェック"""
        rate_limit_indicators = ["429", "rate limit", "too many requests"]
        return any(indicator in error_message.lower() for indicator in rate_limit_indicators)

    def _is_server_error(self, error_message: str) -> bool:
        """サーバーエラーかどうかをチェック"""
        server_error_codes = ["500", "502", "503", "504"]
        return any(code in error_message for code in server_error_codes)

    def _format_search_result(self, content: str, query: str) -> str:
        """検索結果をフォーマットする"""
        try:
            # 基本的なフォーマット
            formatted_result = f"🔍 **Search Results for: {query}**\n\n"
            formatted_result += content

            # 結果の長さをチェックし、必要に応じて切り詰める
            if len(formatted_result) > 3500:  # 安全マージンを考慮
                formatted_result = formatted_result[:3500] + "\n\n[Results truncated due to length limit]"

            return formatted_result

        except Exception as e:
            logger.error(f"Error formatting search result: {e}")
            return content  # フォーマットに失敗した場合は元のコンテンツを返す

    async def run(self, *, arguments: Dict[str, Any], bot) -> str:
        """
        The main entry point for the agent, called by the LLM cog.

        Args:
            arguments: Dictionary containing the query and other parameters
            bot: The bot instance

        Returns:
            str: The search results or error message
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

        Returns:
            bool: True if the agent is available, False otherwise
        """
        return self.client is not None

    def get_status(self) -> Dict[str, Any]:
        """
        Get the current status of the SearchAgent.

        Returns:
            dict: Status information including configuration and availability
        """
        status = {
            "available": self.is_available(),
            "model": self.model,
            "supported_models": self.SUPPORTED_MODELS,
            "max_retries": self.max_retries,
            "timeout": self.timeout,
            "client_initialized": self.client is not None,
            "initialization_error": self.initialization_error,
            "use_legacy_client": self.use_legacy_client,
            "mistral_library_available": Mistral is not None or MistralAsyncClient is not None
        }

        # 設定の診断
        try:
            mcfg = self.bot.cfg.get("search_agent", {})
            status["config_present"] = bool(mcfg)
            status["api_key_present"] = bool(mcfg.get("api_key"))
            if mcfg.get("api_key"):
                status["api_key_length"] = len(mcfg.get("api_key"))
        except Exception as e:
            status["config_error"] = str(e)

        return status

    def get_diagnostic_info(self) -> str:
        """
        Get detailed diagnostic information as a formatted string.

        Returns:
            str: Formatted diagnostic information
        """
        status = self.get_status()
        lines = []
        lines.append("=== SearchAgent Diagnostic Information ===")

        for key, value in status.items():
            lines.append(f"{key}: {value}")

        lines.append("")
        lines.append("=== Required Setup ===")
        lines.append("1. Install MistralAI library: pip install mistralai")
        lines.append("2. Add to config.yaml:")
        lines.append("  search_agent:")
        lines.append("    api_key: 'your_mistral_api_key'")
        lines.append("    model: 'mistral-large-latest'")
        lines.append("3. Get API key from: https://console.mistral.ai/")

        return "\n".join(lines)

    async def test_connection(self) -> tuple[bool, str]:
        """
        Test the connection to the Mistral API.

        Returns:
            tuple: (success: bool, message: str)
        """
        if not self.client:
            return False, "Client not initialized"

        try:
            # シンプルなテストクエリを実行
            test_result = await self._mistral_search("test connection")
            if "[Mistral Search Error]" in test_result:
                return False, test_result
            return True, "Connection test successful"
        except Exception as e:
            return False, f"Connection test failed: {e}"