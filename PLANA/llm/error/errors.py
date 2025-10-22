# PLANA/llm/error/errors.py

import logging
import openai

logger = logging.getLogger(__name__)


class LLMExceptionHandler:
    def __init__(self, config: dict):
        self.config = config.get('error_msg', {})

    def handle_exception(self, exception: Exception) -> str:
        """
        LLM関連の例外を処理し、ユーザーフレンドリーなエラーメッセージを返す。
        """
        if isinstance(exception, openai.RateLimitError):
            logger.warning(f"LLM API rate limit error: {exception}")
            return self.config.get('rate_limit_error',
                                   "APIの利用制限に達しました。しばらくしてからもう一度お試しください。")

        if isinstance(exception, openai.AuthenticationError):
            logger.error(f"LLM API authentication error: {exception}")
            return self.config.get('auth_error', "APIキーが無効です。開発者に連絡してください。")

        if isinstance(exception, openai.APIConnectionError):
            logger.error(f"LLM API connection error: {exception}")
            return self.config.get('connection_error',
                                   "AIサービスへの接続に失敗しました。ネットワーク状態を確認するか、後でもう一度お試しください。")

        if isinstance(exception, openai.APIStatusError):
            status_code = exception.status_code
            try:
                # エラーレスポンスのボディを取得
                error_body = exception.response.json()
                # 'error'キーの中身を取得
                error_data = error_body.get('error')

                # --- ここからが修正箇所 ---
                # APIからのエラーがリストでラップされている場合に対応
                if isinstance(error_data, list) and error_data:
                    # リストの最初の要素を辞書として扱う
                    error_dict = error_data[0]
                    # さらに 'error' キーでネストされている場合も考慮
                    error_data = error_dict.get('error', error_dict)
                # --- ここまでが修正箇所 ---

                detail = "No details provided."
                if isinstance(error_data, dict):
                    # 詳細メッセージを複数のキーから探す
                    detail = error_data.get('detail') or error_data.get('message') or error_data.get('title') or str(
                        error_data)

                logger.error(f"LLM API status error: {status_code} - {error_body}")

                if status_code == 503:
                    return self.config.get('model_overloaded',
                                           "現在、AIモデルが大変混み合っています。しばらくしてからもう一度お試しください。")

                return self.config.get('api_status_error', "APIから予期せぬエラーが返されました。").format(
                    status_code=status_code, detail=detail)

            except Exception as parse_error:
                logger.error(f"LLM API status error (could not parse response): {status_code} - {exception}",
                             exc_info=True)
                return self.config.get('api_parse_error',
                                       "APIからエラーが返されましたが、内容を解析できませんでした。").format(
                    status_code=status_code)

        # その他の予期せぬエラー
        logger.error(f"An unexpected error occurred during LLM interaction: {exception}", exc_info=True)
        return self.config.get('unexpected_error', "予期せぬエラーが発生しました。開発者に連絡してください。")