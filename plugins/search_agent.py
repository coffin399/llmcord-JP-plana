from __future__ import annotations
import asyncio
import logging
from typing import Dict, Any, Optional, List

# import json # Google AI版では不要になる可能性が高い

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

    # Google AIの検索対応モデル
    SEARCH_ENABLED_MODELS = [
        "gemini-2.5-flash"
    ]

    def __init__(self, bot) -> None:
        self.bot = bot
        self.model = None
        self.model_name = "gemini-1.5-pro-latest"
        self.max_retries = 3
        self.base_delay = 1.0
        self.timeout = 60.0  # Google Search連携は少し時間がかかる場合があるため延長
        self.initialization_error = None

        try:
            logger.info("Loading Google AI SearchAgent configuration...")
            # 設定ファイルでは 'google_ai_search_agent' のようなセクションを想定
            gcfg = self.bot.cfg.get("google_ai_search_agent", {})

            # API keyの取得
            api_key = gcfg.get("api_key")
            if not api_key:
                error_msg = "API key not found in configuration under 'google_ai_search_agent.api_key'"
                logger.error(error_msg)
                self.initialization_error = error_msg
                return

            logger.info(f"Google AI API key found (starts with: {api_key[:4]}...)")

            # Google AIクライアントの初期化
            if not genai:
                error_msg = "Google AI library not available. Please install: pip install google-generativeai"
                logger.error(error_msg)
                self.initialization_error = error_msg
                return

            try:
                genai.configure(api_key=api_key)
                logger.info("Google AI client configured successfully.")
            except Exception as e:
                logger.error(f"Failed to configure Google AI client: {e}")
                self.initialization_error = str(e)
                return

            # モデルの設定
            configured_model = gcfg.get("model", "gemini-1.5-pro-latest")
            if configured_model in self.SEARCH_ENABLED_MODELS:
                self.model_name = configured_model
                logger.info(f"Using model: {self.model_name}")
            else:
                logger.warning(
                    f"Model '{configured_model}' is not in the recommended list. "
                    f"Consider using: {', '.join(self.SEARCH_ENABLED_MODELS)}"
                )
                self.model_name = configured_model

            # Google検索ツールを有効にしたモデルを初期化
            try:
                search_tool = genai.Tool.from_google_search_retrieval()
                self.model = genai.GenerativeModel(
                    model_name=self.model_name,
                    tools=[search_tool],
                )
                logger.info(f"GenerativeModel '{self.model_name}' with Google Search initialized.")
            except Exception as e:
                logger.error(f"Failed to initialize GenerativeModel with tools: {e}")
                self.initialization_error = str(e)
                self.model = None
                return

            # その他の設定
            self.max_retries = gcfg.get("max_retries", 3)
            self.base_delay = gcfg.get("base_delay", 1.0)
            self.timeout = gcfg.get("timeout", 60.0)

        except Exception as e:
            error_msg = f"Failed to initialize SearchAgent: {e}"
            logger.error(error_msg, exc_info=True)
            self.initialization_error = error_msg
            self.model = None

    async def _perform_google_search(self, query: str) -> str:
        """Google AIを使用してWeb検索と要約を実行"""
        try:
            logger.debug(f"Requesting Google AI search for query: {query}")

            # Google AIでは、ツールを有効にしたモデルにプロンプトを渡すだけで
            # 内部的に検索が実行され、その結果を基に回答が生成される
            prompt = (
                "Based on a web search, provide a comprehensive and detailed report on the following topic. "
                "Structure your answer clearly with relevant facts, figures, and context.\n\n"
                f"Topic: {query}"
            )

            response = await asyncio.wait_for(
                self.model.generate_content_async(
                    prompt,
                    generation_config=genai.types.GenerationConfig(
                        temperature=0.3,
                        # max_output_tokens=4000 # Gemini 1.5では通常不要
                    )
                ),
                timeout=self.timeout
            )

            if response.text:
                content = response.text.strip()
                logger.info(f"Search successful for query: {query}")
                return self._format_search_result(content, query)
            else:
                # 候補がない場合や安全設定でブロックされた場合
                logger.warning(
                    f"No valid response text received for query: {query}. Finish reason: {response.prompt_feedback}")
                return "[Search Error] No valid response received from Google AI. The request may have been blocked."

        except asyncio.TimeoutError:
            logger.error(f"Search timeout for query: {query}")
            return f"[Search Error] Request timeout after {self.timeout}s"
        except Exception as e:
            logger.error(f"Error in Google AI search: {e}", exc_info=True)
            return f"[Search Error] {str(e)}"

    async def _fallback_search(self, query: str) -> str:
        """検索機能が使えない場合のフォールバック（知識ベースを使用）"""
        try:
            logger.info(f"Using fallback (knowledge base) for query: {query}")

            # ツールを使わないモデルインスタンスを生成
            fallback_model = genai.GenerativeModel(self.model_name)

            prompt = (
                "You are a knowledgeable assistant. Provide comprehensive and detailed information based on your training data. "
                "Be clear that this is from your knowledge base, not live web data.\n\n"
                f"Provide detailed information about: {query}\n\n"
                "Include relevant facts, context, and important details from your knowledge base."
            )

            response = await asyncio.wait_for(
                fallback_model.generate_content_async(
                    prompt,
                    generation_config=genai.types.GenerationConfig(
                        temperature=0.3,
                    )
                ),
                timeout=self.timeout
            )

            if response.text:
                content = response.text.strip()
                return f"📚 **Note:** Information from AI knowledge base (not live web search)\n\n{content}"

            return "[Error] Failed to generate fallback response"

        except Exception as e:
            logger.error(f"Error in fallback search: {e}")
            return f"[Error] Fallback search failed: {str(e)}"

    async def _google_search(self, query: str) -> str:
        """検索を実行するメインメソッド（リトライロジック付き）"""
        if not self.model:
            return self._get_initialization_error()

        if not query.strip():
            return "[Search Error] Empty query provided."

        last_error_result = ""
        for attempt in range(self.max_retries + 1):
            try:
                logger.debug(f"Search attempt {attempt + 1}/{self.max_retries + 1} for: {query}")

                result = await self._perform_google_search(query)
                last_error_result = result

                if not result.startswith("[Search Error]"):
                    return result

                if attempt < self.max_retries:
                    delay = self.base_delay * (2 ** attempt)
                    logger.info(f"Retrying in {delay}s...")
                    await asyncio.sleep(delay)

            except Exception as e:
                logger.error(f"Unexpected error on attempt {attempt + 1}: {e}")
                last_error_result = f"[Search Error] Unexpected error: {str(e)}"
                if attempt < self.max_retries:
                    delay = self.base_delay * (2 ** attempt)
                    await asyncio.sleep(delay)

        # 全てのリトライが失敗した場合、フォールバックを試みる
        logger.warning(f"All search attempts failed for query: '{query}'. Trying fallback.")
        try:
            return await self._fallback_search(query)
        except Exception as fallback_e:
            logger.error(f"Fallback also failed: {fallback_e}")
            return f"{last_error_result}\n[Fallback Error] {str(fallback_e)}"

    def _get_initialization_error(self) -> str:
        """初期化エラーの詳細を返す"""
        error_details = ["[Search Error] Agent not properly initialized:"]

        if self.initialization_error:
            error_details.append(f"- Initialization error: {self.initialization_error}")

        if not genai:
            error_details.append("- Google AI library not installed. Run: pip install google-generativeai")

        error_details.append("\n**Required configuration in config.yaml:**")
        error_details.append("```yaml")
        error_details.append("google_ai_search_agent:")
        error_details.append("  api_key: 'your_google_api_key'")
        error_details.append(f"  model: '{self.model_name}'  # Recommended: {', '.join(self.SEARCH_ENABLED_MODELS)}")
        error_details.append("  max_retries: 3  # Optional")
        error_details.append("  timeout: 60.0  # Optional")
        error_details.append("```")

        return "\n".join(error_details)

    def _format_search_result(self, content: str, query: str) -> str:
        """検索結果をフォーマット"""
        try:
            formatted = f"🔍 **Web Search Results for: {query}**\n\n{content}"
            # Discordの文字数制限を考慮
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
            result = await self._google_search(query)

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
        return self.model is not None

    def get_status(self) -> Dict[str, Any]:
        """エージェントのステータスを取得"""
        return {
            "available": self.is_available(),
            "model": self.model_name,
            "supported_models": self.SEARCH_ENABLED_MODELS,
            "initialization_error": self.initialization_error,
            "max_retries": self.max_retries,
            "timeout": self.timeout,
        }

    async def test_connection(self) -> bool:
        """接続テスト"""
        try:
            if not self.is_available():
                return False

            # 簡単なテストクエリを実行
            test_model = genai.GenerativeModel(self.model_name)
            test_response = await asyncio.wait_for(
                test_model.generate_content_async(
                    "Hello",
                    generation_config=genai.types.GenerationConfig(max_output_tokens=10)
                ),
                timeout=10.0
            )
            return test_response.text is not None
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False