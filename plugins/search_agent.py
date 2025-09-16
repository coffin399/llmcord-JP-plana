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
        "gemini-2.5-flash",
        "gemini-2.0-flash-exp",
        "gemini-1.5-pro-latest",
        "gemini-1.5-flash-latest"
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
                # Google Search grounding を有効化（最新API対応）
                self.model = genai.GenerativeModel(
                    model_name=self.model_name,
                    tools=[{"google_search_retrieval": {}}],  # 正しいツール指定
                    system_instruction="You are a helpful search assistant. When asked to search, use Google Search grounding to find current, accurate information and provide comprehensive, well-structured responses."
                )
                logger.info(f"SearchAgent model '{self.model_name}' initialized with Google Search grounding")
            except Exception as e:
                logger.error(f"Failed to initialize model with search tool: {e}")
                # フォールバック: ツールなしでモデルを初期化
                try:
                    self.model = genai.GenerativeModel(
                        model_name=self.model_name,
                        system_instruction="You are a helpful assistant. Provide comprehensive information based on your knowledge."
                    )
                    logger.warning(f"Initialized model without search tool as fallback")
                except Exception as fallback_e:
                    logger.error(f"Failed to initialize fallback model: {fallback_e}")
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

    def _has_search_capability(self) -> bool:
        """モデルが検索機能を持っているかチェック"""
        try:
            if not self.model:
                return False
            # モデルのツール設定を確認
            return hasattr(self.model, '_tools') and self.model._tools is not None
        except:
            return False

    async def _perform_search(self, query: str) -> str:
        """Google AIの検索機能を使用してWeb検索を実行"""
        try:
            logger.debug(f"Executing search for query: '{query}'")

            # 検索用のプロンプト（Groundingを意識したプロンプト）
            search_prompt = (
                f"Search for and provide current, accurate information about: {query}\n\n"
                "Please provide a comprehensive report including:\n"
                "- Recent developments and current status\n"
                "- Key facts and important context\n"
                "- Relevant data, statistics, or examples\n"
                "- Multiple viewpoints when appropriate\n\n"
                "Structure your response with clear sections and cite sources when possible."
            )

            # 検索実行（タイムアウト付き）
            response = await asyncio.wait_for(
                self.model.generate_content_async(
                    search_prompt,
                    generation_config=genai.types.GenerationConfig(
                        temperature=0.1,  # より客観的な回答のため低く設定
                        max_output_tokens=3000,
                    )
                ),
                timeout=self.timeout
            )

            if response.text:
                content = response.text.strip()
                logger.info(f"Search completed successfully for: '{query}'")
                return self._format_result(content, query)
            else:
                # レスポンスが空またはブロックされた場合
                logger.warning(f"Empty or blocked response for query: '{query}'")
                if hasattr(response, 'prompt_feedback') and response.prompt_feedback:
                    return f"[Search Error] Response blocked or filtered. Please try rephrasing your query."
                return "[Search Error] No response received from the model"

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
            error_lines.append("  Note: This library will be deprecated Aug 31, 2025")
            error_lines.append("  Consider migrating to Google Gen AI SDK")

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
            f"**Supported models:** {', '.join(self.SEARCH_ENABLED_MODELS)}",
            "",
            "**Note:** Search grounding requires a paid API tier ($35/1000 queries)"
        ])

        return "\n".join(error_lines)

    def _format_result(self, content: str, query: str) -> str:
        """検索結果の整形"""
        try:
            # ヘッダーを追加
            formatted = f"🔍 **Search Results: {query}**\n\n{content}"

            # Discord文字制限対応（2000文字制限）
            if len(formatted) > 1950:
                # 切り詰めて省略表示を追加
                truncated = formatted[:1850]
                # 文の途中で切れないように調整
                last_period = truncated.rfind('.')
                last_newline = truncated.rfind('\n')
                cut_point = max(last_period, last_newline, 1800)

                formatted = truncated[:cut_point] + "\n\n... *[Results truncated due to Discord length limits]*"

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
            "has_search_capability": self._has_search_capability(),
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