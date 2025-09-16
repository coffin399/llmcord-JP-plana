from __future__ import annotations
import asyncio
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

try:
    import google.generativeai as genai

    logger.info("Google Generative AI client library loaded successfully")
except ImportError:
    logger.error("Google AI library not found. Please install: pip install google-generativeai")
    genai = None


class SearchAgent:
    name = "search"
    tool_spec = {
        "type": "function",
        "function": {
            "name": name,
            "description": "Run a web search using Google AI and return a comprehensive report.",
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

    # Google Search対応モデル（Tool Use対応）
    SEARCH_ENABLED_MODELS = [
        "gemini-2.5-flash"
    ]

    def __init__(self, bot) -> None:
        self.bot = bot
        self.model = None
        self.model_name = "gemini-2.5-flash"
        self.max_retries = 3
        self.base_delay = 1.0
        self.timeout = 60.0
        self.initialization_error = None

        try:
            logger.info("Initializing Google AI SearchAgent...")

            # config.yamlから設定を読み込み（search_agentセクション）
            search_config = self.bot.cfg.get("search_agent", {})

            # API keyの取得
            api_key = search_config.get("api_key")
            if not api_key or api_key == "YOUR_GOOGLE_GEMINI_API_KEY_HERE":
                error_msg = "Valid API key not found. Please set 'search_agent.api_key' in config.yaml"
                logger.error(error_msg)
                self.initialization_error = error_msg
                return

            logger.info(f"Google AI API key found (starts with: {api_key[:8]}...)")

            # Google AIライブラリの確認
            if not genai:
                error_msg = "Google AI library not available. Please install: pip install google-generativeai"
                logger.error(error_msg)
                self.initialization_error = error_msg
                return

            # Google AIクライアントの設定
            try:
                genai.configure(api_key=api_key)
                logger.info("Google AI client configured successfully")
            except Exception as e:
                logger.error(f"Failed to configure Google AI client: {e}")
                self.initialization_error = str(e)
                return

            # モデル設定
            configured_model = search_config.get("model", "gemini-2.5-flash")
            if configured_model in self.SEARCH_ENABLED_MODELS:
                self.model_name = configured_model
                logger.info(f"Using search-enabled model: {self.model_name}")
            else:
                logger.warning(
                    f"Model '{configured_model}' may not support search. "
                    f"Recommended models: {', '.join(self.SEARCH_ENABLED_MODELS)}"
                )
                self.model_name = configured_model

            # 検索ツール付きモデルの初期化
            try:
                # Google Search tool を有効化
                search_tool = genai.Tool.from_google_search_retrieval()
                self.model = genai.GenerativeModel(
                    model_name=self.model_name,
                    tools=[search_tool],
                    system_instruction="You are a helpful search assistant. When asked to search, use the Google Search tool to find current, accurate information and provide comprehensive, well-structured responses."
                )
                logger.info(f"SearchAgent model '{self.model_name}' initialized with Google Search tool")
            except Exception as e:
                logger.error(f"Failed to initialize model with search tool: {e}")
                self.initialization_error = str(e)
                self.model = None
                return

            # その他の設定
            self.timeout = search_config.get("timeout", 60.0)
            self.max_retries = search_config.get("max_retries", 3)
            self.base_delay = search_config.get("base_delay", 1.0)

            logger.info("SearchAgent initialization completed successfully")

        except Exception as e:
            error_msg = f"Failed to initialize SearchAgent: {e}"
            logger.error(error_msg, exc_info=True)
            self.initialization_error = error_msg
            self.model = None

    async def _perform_search(self, query: str) -> str:
        """Google AIの検索機能を使用してWeb検索を実行"""
        try:
            logger.debug(f"Executing search for query: '{query}'")

            # 検索プロンプトの構成
            search_prompt = (
                f"Please search for current information about: {query}\n\n"
                "Provide a comprehensive report that includes:\n"
                "- Key facts and recent developments\n"
                "- Important context and background\n"
                "- Relevant statistics or data if available\n"
                "- Multiple perspectives when appropriate\n\n"
                "Structure your response clearly with appropriate headings and organize the information logically."
            )

            # 検索実行（タイムアウト付き）
            response = await asyncio.wait_for(
                self.model.generate_content_async(
                    search_prompt,
                    generation_config=genai.types.GenerationConfig(
                        temperature=0.2,  # より事実的な回答のため低めに設定
                        max_output_tokens=4000,
                    )
                ),
                timeout=self.timeout
            )

            if response.text:
                content = response.text.strip()
                logger.info(f"Search completed successfully for: '{query}'")
                return self._format_result(content, query)
            else:
                # レスポンスが空の場合
                logger.warning(f"Empty response received for query: '{query}'")
                if hasattr(response, 'prompt_feedback') and response.prompt_feedback:
                    feedback = response.prompt_feedback
                    return f"[Search Error] Response blocked. Reason: {feedback.block_reason if hasattr(feedback, 'block_reason') else 'Unknown'}"
                return "[Search Error] Empty response received from Google AI"

        except asyncio.TimeoutError:
            logger.error(f"Search timeout ({self.timeout}s) for query: '{query}'")
            return f"[Search Error] Request timeout after {self.timeout} seconds"
        except Exception as e:
            logger.error(f"Error during search execution: {e}", exc_info=True)
            return f"[Search Error] {str(e)}"

    async def _fallback_knowledge_query(self, query: str) -> str:
        """検索が失敗した場合の知識ベースフォールバック"""
        try:
            logger.info(f"Using knowledge base fallback for: '{query}'")

            # ツールなしの基本モデルを使用
            fallback_model = genai.GenerativeModel(self.model_name)

            fallback_prompt = (
                f"Based on your training data, provide detailed information about: {query}\n\n"
                "Please note that this information is from your training data and may not reflect "
                "the most recent developments. Include relevant facts, context, and important details."
            )

            response = await asyncio.wait_for(
                fallback_model.generate_content_async(
                    fallback_prompt,
                    generation_config=genai.types.GenerationConfig(
                        temperature=0.3,
                        max_output_tokens=3000,
                    )
                ),
                timeout=30.0
            )

            if response.text:
                content = response.text.strip()
                return f"📚 **Knowledge Base Response** (Not live search data)\n\n{content}"

            return "[Fallback Error] No response from knowledge base"

        except Exception as e:
            logger.error(f"Fallback query failed: {e}")
            return f"[Fallback Error] Knowledge base query failed: {str(e)}"

    async def _search_with_retries(self, query: str) -> str:
        """リトライロジック付きの検索実行"""
        if not self.model:
            return self._get_initialization_error_message()

        if not query.strip():
            return "[Search Error] Empty or invalid query provided"

        last_error_result = ""

        # メイン検索の実行（リトライ付き）
        for attempt in range(self.max_retries + 1):
            try:
                logger.debug(f"Search attempt {attempt + 1}/{self.max_retries + 1} for: '{query}'")

                result = await self._perform_search(query)

                # 成功した場合は結果を返す
                if not result.startswith("[Search Error]"):
                    return result

                # エラーの場合、最後のエラーを記録
                last_error_result = result

                # 最後の試行でない場合はリトライ
                if attempt < self.max_retries:
                    retry_delay = self.base_delay * (2 ** attempt)
                    logger.info(f"Search failed, retrying in {retry_delay}s... (attempt {attempt + 1})")
                    await asyncio.sleep(retry_delay)

            except Exception as e:
                logger.error(f"Unexpected error on search attempt {attempt + 1}: {e}")
                last_error_result = f"[Search Error] Unexpected error: {str(e)}"

                if attempt < self.max_retries:
                    retry_delay = self.base_delay * (2 ** attempt)
                    await asyncio.sleep(retry_delay)

        # 全ての検索試行が失敗した場合、フォールバックを試行
        logger.warning(f"All search attempts failed for: '{query}'. Attempting fallback.")
        try:
            fallback_result = await self._fallback_knowledge_query(query)
            return f"{last_error_result}\n\n{fallback_result}"
        except Exception as fallback_error:
            logger.error(f"Fallback also failed: {fallback_error}")
            return f"{last_error_result}\n\n[Fallback Error] Knowledge base query failed: {str(fallback_error)}"

    def _get_initialization_error_message(self) -> str:
        """初期化エラーの詳細メッセージ"""
        error_lines = ["[Search Error] SearchAgent is not properly initialized:"]

        if self.initialization_error:
            error_lines.append(f"• {self.initialization_error}")

        if not genai:
            error_lines.append("• Google AI library not installed")
            error_lines.append("  Install with: pip install google-generativeai")

        error_lines.extend([
            "",
            "**Required configuration in config.yaml:**",
            "```yaml",
            "search_agent:",
            "  api_key: 'your_google_gemini_api_key'",
            f"  model: '{self.model_name}'  # Recommended",
            "  timeout: 60.0  # Optional",
            "```",
            "",
            f"**Supported models:** {', '.join(self.SEARCH_ENABLED_MODELS)}"
        ])

        return "\n".join(error_lines)

    def _format_result(self, content: str, query: str) -> str:
        """検索結果の整形"""
        try:
            # ヘッダーを追加
            formatted = f"🔍 **Search Results: {query}**\n\n{content}"

            # Discord文字制限対応（2000文字制限を考慮）
            if len(formatted) > 1900:
                truncated = formatted[:1800]
                formatted = truncated + "\n\n... *[Results truncated due to length limits]*"

            return formatted

        except Exception as e:
            logger.error(f"Error formatting search result: {e}")
            return content

    # --- Public Interface ---

    async def run(self, *, arguments: Dict[str, Any], bot) -> str:
        """LLM Cogから呼び出されるメインエントリーポイント"""
        try:
            query = arguments.get("query", "").strip()

            if not query:
                return "[Search Error] No query provided"

            logger.info(f"SearchAgent processing query: '{query}'")
            result = await self._search_with_retries(query)

            # 結果のログ出力
            if result.startswith("[Search Error]") or result.startswith("[Error]"):
                logger.warning(f"SearchAgent returned error for '{query}': {result[:150]}...")
            else:
                logger.info(f"SearchAgent completed successfully for: '{query}'")

            return result

        except Exception as e:
            logger.error(f"Unexpected error in SearchAgent.run: {e}", exc_info=True)
            return f"[Search Error] Unexpected system error: {str(e)}"

    def is_available(self) -> bool:
        """エージェントが利用可能かどうか"""
        return self.model is not None and self.initialization_error is None

    def get_status(self) -> Dict[str, Any]:
        """エージェントのステータス情報"""
        return {
            "available": self.is_available(),
            "model_name": self.model_name,
            "supported_models": self.SEARCH_ENABLED_MODELS,
            "initialization_error": self.initialization_error,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
        }

    async def test_connection(self) -> bool:
        """接続テストの実行"""
        try:
            if not self.is_available():
                logger.warning("Connection test failed: Agent not available")
                return False

            # 簡単なテストクエリでの接続確認
            test_model = genai.GenerativeModel(self.model_name)
            test_response = await asyncio.wait_for(
                test_model.generate_content_async(
                    "Test connection",
                    generation_config=genai.types.GenerationConfig(max_output_tokens=10)
                ),
                timeout=15.0
            )

            success = test_response.text is not None
            logger.info(f"Connection test {'passed' if success else 'failed'}")
            return success

        except Exception as e:
            logger.error(f"Connection test failed with error: {e}")
            return False