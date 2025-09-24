# PLANA/llm/error/errors.py
from __future__ import annotations

import json
import logging
from typing import Dict, Any, Optional

import openai

logger = logging.getLogger(__name__)

# Discordのメッセージ最大長
DISCORD_MESSAGE_MAX_LENGTH = 1990


class LLMExceptionHandler:
    """LLM関連のAPI例外を処理し、ユーザー向けの整形されたエラーメッセージを生成するクラス。"""
    # ... (このクラスの中身は変更ありません) ...
    def __init__(self, llm_config: Dict[str, Any]):
        self.llm_config = llm_config

    def handle_exception(self, e: Exception) -> str:
        error_detail = ""
        error_messages = self.llm_config.get('error_msg', {})
        if isinstance(e, openai.RateLimitError):
            logger.warning(f"LLM API rate limit exceeded: {e.status_code} - {e.response.text if e.response else 'N/A'}")
            base_msg_key, default_msg = 'ratelimit_error', "⚠️生成AIが現在非常に混雑しています。(Code: {status_code})"
        elif isinstance(e, (openai.APIConnectionError, openai.APITimeoutError)):
            logger.error(f"LLM API connection error: {e}")
            return error_messages.get('general_error', "Failed to connect to the AI service.")
        elif isinstance(e, openai.APIStatusError):
            logger.error(f"LLM API status error: {e.status_code} - {e.response.text if e.response else 'N/A'}")
            base_msg_key, default_msg = 'api_status_error', "⚠️AIとの通信でエラーが発生しました。(Code: {status_code})"
        else:
            logger.error(f"An unexpected error occurred during LLM interaction: {e}", exc_info=True)
            return error_messages.get('general_error', "An unexpected error occurred.")
        if hasattr(e, 'response') and e.response:
            try:
                error_data = e.response.json()
                detail = error_data.get('detail') or error_data.get('message') or error_data.get('title')
                error_detail = f"\n> **Details**: {detail}" if detail else f"\n> **Response**: `{str(error_data)[:500]}`"
            except json.JSONDecodeError:
                error_detail = f"\n> **Raw Response**: `{e.response.text[:500]}`"
        base_message = error_messages.get(base_msg_key, default_msg).format(status_code=e.status_code)
        return f"{base_message}{error_detail}"[:DISCORD_MESSAGE_MAX_LENGTH]


# --- ここからSearch Agent用のカスタム例外を追記 ---

class SearchAgentError(Exception):
    """SearchAgentで発生するエラーの基底クラス。"""
    def __init__(self, message: str, original_exception: Optional[Exception] = None):
        self.original_exception = original_exception
        super().__init__(message)

class SearchAPIRateLimitError(SearchAgentError):
    """Google Search APIのレート制限に達したときに発生する例外。"""
    pass

class SearchAPIServerError(SearchAgentError):
    """Google Search APIのサーバー側エラー(5xx系)で発生する例外。"""
    pass

class SearchAPIError(SearchAgentError):
    """その他の予期しないAPIエラーで発生する例外。"""
    pass

class SearchExecutionError(SearchAgentError):
    """検索実行中の一般的なエラー（空のクエリなど）で発生する例外。"""
    pass