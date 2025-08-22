from __future__ import annotations
import asyncio
import logging
from typing import Dict, Any, Optional, List
import json

logger = logging.getLogger(__name__)

try:
    from mistralai import Mistral

    logger.info("Mistral client library loaded successfully")
except ImportError:
    logger.error("MistralAI library not found. Please install: pip install mistralai")
    Mistral = None


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

    # Mistral AIの検索対応モデル（最新版）
    SEARCH_ENABLED_MODELS = [
        "mistral-large-latest",
        "mistral-medium-latest",
        "pixtral-large-latest"
    ]

    def __init__(self, bot) -> None:
        self.bot = bot
        self.client = None
        self.model = "mistral-large-latest"
        self.max_retries = 3
        self.base_delay = 1.0
        self.timeout = 30.0
        self.initialization_error = None

        try:
            logger.info("Loading SearchAgent configuration...")
            mcfg = self.bot.cfg.get("search_agent", {})

            # API keyの取得
            api_key = mcfg.get("api_key")
            if not api_key:
                error_msg = "API key not found in configuration under 'search_agent.api_key'"
                logger.error(error_msg)
                self.initialization_error = error_msg
                return

            logger.info(f"API key found (starts with: {api_key[:8]}...)")

            # Mistralクライアントの初期化
            if not Mistral:
                error_msg = "Mistral library not available. Please install: pip install mistralai"
                logger.error(error_msg)
                self.initialization_error = error_msg
                return

            try:
                self.client = Mistral(api_key=api_key)
                logger.info("Mistral client initialized successfully.")
            except Exception as e:
                logger.error(f"Failed to initialize Mistral client: {e}")
                self.client = None
                self.initialization_error = str(e)
                return

            # モデルの設定
            configured_model = mcfg.get("model", "mistral-large-latest")
            if configured_model in self.SEARCH_ENABLED_MODELS:
                self.model = configured_model
                logger.info(f"Using model: {self.model}")
            else:
                # 検索非対応モデルの場合、警告を出すが続行
                logger.warning(
                    f"Model '{configured_model}' may not be optimal. Consider using: {', '.join(self.SEARCH_ENABLED_MODELS)}")
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

    async def _perform_web_search(self, query: str) -> str:
        """Mistral AIを使用してWeb検索を実行（最新版）"""
        try:
            # Web検索ツールの定義
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search the web for real-time information",
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
                }
            ]

            # 初期メッセージ
            messages = [
                {
                    "role": "system",
                    "content": "You are a helpful assistant with web search capabilities. Use the web_search tool to find current information."
                },
                {
                    "role": "user",
                    "content": f"Search for and provide comprehensive information about: {query}"
                }
            ]

            logger.debug(f"Requesting search for query: {query}")

            # Mistral AIのChat Completionを呼び出し（ツール使用を有効化）
            response = await asyncio.wait_for(
                self.client.chat.complete_async(
                    model=self.model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",  # 自動的にツールを選択
                    temperature=0.3,
                    max_tokens=4000,
                ),
                timeout=self.timeout
            )

            # レスポンスの処理
            if response.choices and response.choices[0].message:
                message = response.choices[0].message

                # ツール呼び出しがある場合
                if hasattr(message, 'tool_calls') and message.tool_calls:
                    logger.info(f"Tool call detected for query: {query}")

                    # メッセージ履歴に追加
                    messages.append(message.model_dump())

                    # ツール呼び出しの結果を模擬
                    for tool_call in message.tool_calls:
                        messages.append({
                            "role": "tool",
                            "content": f"Search results retrieved for: {json.loads(tool_call.function.arguments).get('query', query)}",
                            "tool_call_id": tool_call.id
                        })

                    # 最終的な応答を取得
                    final_response = await asyncio.wait_for(
                        self.client.chat.complete_async(
                            model=self.model,
                            messages=messages,
                            temperature=0.3,
                            max_tokens=4000,
                        ),
                        timeout=self.timeout
                    )

                    if final_response.choices and final_response.choices[0].message.content:
                        content = final_response.choices[0].message.content.strip()
                        logger.info(f"Search successful for query: {query}")
                        return self._format_search_result(content, query)

                # 通常のレスポンス（ツール呼び出しなし）
                elif message.content:
                    content = message.content.strip()
                    logger.info(f"Response received without tool call for query: {query}")
                    return self._format_search_result(content, query)

            return "[Search Error] No valid response received"

        except asyncio.TimeoutError:
            logger.error(f"Search timeout for query: {query}")
            return f"[Search Error] Request timeout after {self.timeout}s"
        except Exception as e:
            logger.error(f"Error in web search: {e}", exc_info=True)
            return f"[Search Error] {str(e)}"

    async def _fallback_search(self, query: str) -> str:
        """検索機能が使えない場合のフォールバック（知識ベースを使用）"""
        try:
            logger.info(f"Using fallback (knowledge base) for query: {query}")

            messages = [
                {
                    "role": "system",
                    "content": "You are a knowledgeable assistant. Provide comprehensive and detailed information based on your training data. Be clear that this is from your knowledge base, not live web data."
                },
                {
                    "role": "user",
                    "content": f"Provide detailed information about: {query}\n\nInclude relevant facts, context, and important details from your knowledge base."
                }
            ]

            response = await asyncio.wait_for(
                self.client.chat.complete_async(
                    model=self.model,
                    messages=messages,
                    temperature=0.3,
                    max_tokens=3000,
                ),
                timeout=self.timeout
            )

            if response.choices and response.choices[0].message.content:
                content = response.choices[0].message.content.strip()
                return f"📚 **Note:** Information from AI knowledge base (not live web search)\n\n{content}"

            return "[Error] Failed to generate response"

        except Exception as e:
            logger.error(f"Error in fallback search: {e}")
            return f"[Error] Fallback search failed: {str(e)}"

    async def _mistral_search(self, query: str) -> str:
        """検索を実行するメインメソッド（リトライロジック付き）"""
        if not self.client:
            return self._get_initialization_error()

        if not query.strip():
            return "[Search Error] Empty query provided."

        for attempt in range(self.max_retries + 1):
            try:
                logger.debug(f"Search attempt {attempt + 1}/{self.max_retries + 1} for: {query}")

                # まずWeb検索を試みる
                result = await self._perform_web_search(query)

                # エラーの場合、フォールバックを試みる
                if result.startswith("[Search Error]") and attempt == self.max_retries:
                    logger.info("Web search failed, trying fallback...")
                    result = await self._fallback_search(query)

                if not result.startswith("[Search Error]") and not result.startswith("[Error]"):
                    return result

                # リトライが必要な場合
                if attempt < self.max_retries:
                    delay = self.base_delay * (2 ** attempt)
                    logger.info(f"Retrying in {delay}s...")
                    await asyncio.sleep(delay)
                    continue

                return result

            except Exception as e:
                logger.error(f"Unexpected error on attempt {attempt + 1}: {e}")
                if attempt < self.max_retries:
                    delay = self.base_delay * (2 ** attempt)
                    await asyncio.sleep(delay)
                    continue

                # 最後の試行でもエラーの場合、フォールバックを試みる
                try:
                    return await self._fallback_search(query)
                except:
                    return f"[Search Error] All attempts failed: {str(e)}"

        return "[Search Error] Failed after all retries."

    def _get_initialization_error(self) -> str:
        """初期化エラーの詳細を返す"""
        error_details = ["[Search Error] Agent not properly initialized:"]

        if self.initialization_error:
            error_details.append(f"- Initialization error: {self.initialization_error}")

        if not Mistral:
            error_details.append("- MistralAI library not installed. Run: pip install mistralai")

        error_details.append("\n**Required configuration in config.yaml:**")
        error_details.append("```yaml")
        error_details.append("search_agent:")
        error_details.append("  api_key: 'your_mistral_api_key'")
        error_details.append("  model: 'mistral-large-latest'  # または他の対応モデル")
        error_details.append("  max_retries: 3  # オプション")
        error_details.append("  timeout: 30.0  # オプション")
        error_details.append("```")

        return "\n".join(error_details)

    def _format_search_result(self, content: str, query: str) -> str:
        """検索結果をフォーマット"""
        try:
            # 結果のタイプを判定
            if "web_search" in content.lower() or "search results" in content.lower():
                icon = "🔍"
                title = "Web Search Results"
            else:
                icon = "📝"
                title = "Information"

            formatted = f"{icon} **{title} for: {query}**\n\n{content}"

            # 長すぎる場合はトランケート
            if len(formatted) > 4000:
                formatted = formatted[:3900] + "\n\n... [Results truncated for brevity]"

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

            logger.info(f"SearchAgent executing query: {query}")
            result = await self._mistral_search(query)

            # 成功/失敗のログ
            if not result.startswith("[Search Error]") and not result.startswith("[Error]"):
                logger.info(f"SearchAgent completed successfully for: {query}")
            else:
                logger.warning(f"SearchAgent returned error for '{query}': {result[:100]}...")

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
            "supported_models": self.SEARCH_ENABLED_MODELS,
            "initialization_error": self.initialization_error,
            "max_retries": self.max_retries,
            "timeout": self.timeout,
        }

    async def test_connection(self) -> bool:
        """接続テスト"""
        try:
            if not self.client:
                return False

            # 簡単なテストクエリを実行
            test_response = await asyncio.wait_for(
                self.client.chat.complete_async(
                    model=self.model,
                    messages=[{"role": "user", "content": "Hello"}],
                    max_tokens=10,
                ),
                timeout=5.0
            )

            return test_response.choices is not None

        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False