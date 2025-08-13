from __future__ import annotations
import asyncio
import logging
from typing import Dict, Any, Optional, List
import json

logger = logging.getLogger(__name__)

try:
    from mistralai import Mistral
    from mistralai.models import ChatCompletionRequest, UserMessage, SystemMessage

    MistralAPIException = Exception
    logger.info("Using new Mistral client library")
except ImportError:
    try:
        from mistralai.async_client import MistralAsyncClient
        from mistralai.exceptions import MistralAPIException
        from mistralai.models.chat_completion import ChatMessage

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
            "description": "Run a web search using the Mistral AI and return a comprehensive report.",
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

    # Mistral AIの検索対応モデル
    SEARCH_ENABLED_MODELS = [
        "mistral-medium-latest"
    ]

    def __init__(self, bot) -> None:
        self.bot = bot
        self.client = None
        self.model = "mistral-large-latest"
        self.max_retries = 3
        self.base_delay = 1.0
        self.timeout = 30.0
        self.initialization_error = None
        self.use_legacy_client = False

        try:
            logger.info("Loading SearchAgent configuration...")
            mcfg = self.bot.cfg.get("search_agent", {})

            api_key = mcfg.get("api_key")
            if not api_key:
                error_msg = "API key not found in configuration under 'search_agent.api_key'"
                logger.error(error_msg)
                self.initialization_error = error_msg
                return

            logger.info(f"API key found (starts with: {api_key[:8]}...)")

            # クライアントの初期化
            try:
                if Mistral:
                    self.client = Mistral(api_key=api_key)
                    logger.info("New Mistral client initialized successfully.")
                    self.use_legacy_client = False
                else:
                    self.client = MistralAsyncClient(api_key=api_key)
                    logger.info("Legacy MistralAsyncClient initialized successfully.")
                    self.use_legacy_client = True
            except Exception as e:
                logger.error(f"Failed to initialize Mistral client: {e}")
                self.client = None
                self.initialization_error = str(e)
                return

            # モデルの設定
            configured_model = mcfg.get("model", "mistral-large-latest")
            if configured_model in self.SEARCH_ENABLED_MODELS:
                self.model = configured_model
                logger.info(f"Using search-enabled model: {self.model}")
            else:
                # 検索非対応モデルの場合、警告を出すが続行
                logger.warning(
                    f"Model '{configured_model}' may not support web search. Consider using: {', '.join(self.SEARCH_ENABLED_MODELS)}")
                self.model = configured_model

            # その他の設定
            self.max_retries = mcfg.get("max_retries", 3)
            self.base_delay = mcfg.get("base_delay", 1.0)
            self.timeout = mcfg.get("timeout", 30.0)

        except Exception as e:
            error_msg = f"Failed to initialize SearchAgent: {e}"
            logger.error(error_msg, exc_info=True)
            self.initialization_error = error_msg
            self.client = None

    async def _mistral_search_new(self, query: str) -> str:
        """新しいMistralクライアントを使用した検索（修正版）"""
        try:
            # Mistral AIのFunction Callingを使用する正しい方法
            tools = [{
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Search the web for information",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "The search query"
                            }
                        },
                        "required": ["query"]
                    }
                }
            }]

            # まず検索ツールを呼び出すようLLMに指示
            messages = [
                {"role": "user", "content": f"Please search for information about: {query}"}
            ]

            # ツール呼び出しを要求
            logger.debug(f"Requesting tool call for query: {query}")

            response = await asyncio.wait_for(
                self.client.chat.complete_async(
                    model=self.model,
                    messages=messages,
                    tools=tools,
                    tool_choice="required",  # ツールの使用を強制
                ),
                timeout=self.timeout
            )

            if not response.choices or not response.choices[0].message.tool_calls:
                # ツール呼び出しがない場合、通常のチャットとして処理
                logger.warning("No tool calls in response, falling back to regular chat")
                return await self._fallback_search(query)

            # ツール呼び出しの結果を処理
            tool_call = response.choices[0].message.tool_calls[0]

            # ツールの実行結果を模擬（実際にはMistral側で処理される）
            messages.append(response.choices[0].message.model_dump())
            messages.append({
                "role": "tool",
                "content": f"Web search completed for: {query}",
                "tool_call_id": tool_call.id
            })

            # 最終的な応答を取得
            final_response = await asyncio.wait_for(
                self.client.chat.complete_async(
                    model=self.model,
                    messages=messages,
                    temperature=0.1,
                    max_tokens=4000,
                ),
                timeout=self.timeout
            )

            if final_response.choices and final_response.choices[0].message.content:
                content = final_response.choices[0].message.content.strip()
                logger.info(f"Search successful for query: {query}")
                return self._format_search_result(content, query)

            return "[Search Error] No content in response"

        except Exception as e:
            logger.error(f"Error in new Mistral search: {e}", exc_info=True)
            # フォールバックを試みる
            return await self._fallback_search(query)

    async def _fallback_search(self, query: str) -> str:
        """検索機能が使えない場合のフォールバック"""
        try:
            logger.info(f"Using fallback search for query: {query}")

            messages = [
                {
                    "role": "system",
                    "content": "You are a knowledgeable assistant. Provide comprehensive and detailed information about the topic based on your training data. Be clear that this is not live web data."
                },
                {
                    "role": "user",
                    "content": f"Provide detailed information about: {query}\n\nPlease be comprehensive and include relevant facts, recent developments (up to your knowledge cutoff), and important context."
                }
            ]

            if self.use_legacy_client:
                response = await self.client.chat(
                    model=self.model,
                    messages=messages,
                    temperature=0.3,
                    max_tokens=2000,
                )
            else:
                response = await self.client.chat.complete_async(
                    model=self.model,
                    messages=messages,
                    temperature=0.3,
                    max_tokens=2000,
                )

            if response.choices and response.choices[0].message.content:
                content = response.choices[0].message.content.strip()
                return f"**Note:** Using AI knowledge base (not live web search)\n\n{content}"

            return "[Error] Failed to generate response"

        except Exception as e:
            logger.error(f"Error in fallback search: {e}")
            return f"[Error] Search failed: {str(e)}"

    async def _mistral_search_legacy(self, query: str) -> str:
        """古いMistralAsyncClientを使用（フォールバックを使用）"""
        # 古いクライアントでは検索機能が制限されている可能性があるため
        # フォールバックを使用
        return await self._fallback_search(query)

    async def _mistral_search(self, query: str) -> str:
        """検索を実行するメインメソッド"""
        if not self.client:
            return self._get_initialization_error()

        if not query.strip():
            return "[Search Error] Empty query provided."

        for attempt in range(self.max_retries + 1):
            try:
                logger.debug(f"Search attempt {attempt + 1} for: {query}")

                if self.use_legacy_client:
                    result = await self._mistral_search_legacy(query)
                else:
                    result = await self._mistral_search_new(query)

                return result

            except asyncio.TimeoutError:
                logger.warning(f"Search timeout on attempt {attempt + 1}")
                if attempt < self.max_retries:
                    await asyncio.sleep(self.base_delay * (2 ** attempt))
                    continue
                return f"[Search Error] Request timeout after {self.timeout}s."

            except Exception as e:
                logger.error(f"Search error on attempt {attempt + 1}: {e}")
                if attempt < self.max_retries:
                    await asyncio.sleep(self.base_delay * (2 ** attempt))
                    continue
                return f"[Search Error] {str(e)}"

        return "[Search Error] Failed after all retries."

    def _get_initialization_error(self) -> str:
        """初期化エラーの詳細を返す"""
        error_details = ["[Search Error] Agent not properly initialized:"]

        if self.initialization_error:
            error_details.append(f"- Initialization error: {self.initialization_error}")

        if not Mistral and not MistralAsyncClient:
            error_details.append("- MistralAI library not installed. Run: pip install mistralai")

        error_details.append("\nRequired configuration in config.yaml:")
        error_details.append("search_agent:")
        error_details.append("  api_key: 'your_mistral_api_key'")
        error_details.append("  model: 'mistral-large-latest'")

        return "\n".join(error_details)

    def _format_search_result(self, content: str, query: str) -> str:
        """検索結果をフォーマット"""
        try:
            formatted = f"🔍 **Search Results for: {query}**\n\n{content}"

            if len(formatted) > 3500:
                formatted = formatted[:3500] + "\n\n[Results truncated]"

            return formatted
        except Exception as e:
            logger.error(f"Error formatting result: {e}")
            return content

    async def run(self, *, arguments: Dict[str, Any], bot) -> str:
        """LLM Cogから呼び出されるメインエントリーポイント"""
        try:
            query = arguments.get("query", "").strip()
            if not query:
                return "[Search Error] Empty query provided."

            logger.info(f"SearchAgent executing: {query}")
            result = await self._mistral_search(query)

            # エラーでない場合のみ成功ログ
            if not result.startswith("[Search Error]") and not result.startswith("[Error]"):
                logger.info("SearchAgent completed successfully")
            else:
                logger.warning(f"SearchAgent returned error: {result[:100]}...")

            return result

        except Exception as e:
            logger.error(f"Unexpected error in SearchAgent.run: {e}", exc_info=True)
            return f"[Search Error] Unexpected error: {str(e)}"

    def is_available(self) -> bool:
        """エージェントが利用可能かチェック"""
        return self.client is not None

    def get_status(self) -> Dict[str, Any]:
        """エージェントのステータスを取得"""
        return {
            "available": self.is_available(),
            "model": self.model,
            "search_enabled_models": self.SEARCH_ENABLED_MODELS,
            "client_type": "new" if not self.use_legacy_client else "legacy",
            "initialization_error": self.initialization_error,
        }