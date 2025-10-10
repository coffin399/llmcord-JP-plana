#PLANA/tracker/error/errors.py
"""
TRN Discord Bot用のカスタムエラークラス
"""

class TRNBotError(Exception):
    """全てのTRN Bot関連エラーの基底クラス"""
    pass


class TRNAPIError(TRNBotError):
    """TRN APIからのレスポンスエラー"""
    def __init__(self, status_code_or_message):
        if isinstance(status_code_or_message, int):
            self.status_code = status_code_or_message
            self.message = f"TRN API returned status code {status_code_or_message}"
        else:
            self.status_code = None
            self.message = str(status_code_or_message)
        super().__init__(self.message)


class PlayerNotFoundError(TRNBotError):
    """プレイヤーが見つからない場合のエラー"""
    def __init__(self, username: str, game: str):
        self.username = username
        self.game = game
        self.message = f"Player '{username}' not found in {game}"
        super().__init__(self.message)


class GameNotSupportedError(TRNBotError):
    """サポートされていないゲームが指定された場合のエラー"""
    def __init__(self, game: str):
        self.game = game
        self.message = f"Game '{game}' is not supported"
        super().__init__(self.message)


class RateLimitError(TRNBotError):
    """APIレート制限に達した場合のエラー"""
    def __init__(self, retry_after: int = None):
        self.retry_after = retry_after
        if retry_after:
            self.message = f"Rate limit exceeded. Please try again after {retry_after} seconds"
        else:
            self.message = "Rate limit exceeded. Please try again later"
        super().__init__(self.message)


class InvalidPlatformError(TRNBotError):
    """無効なプラットフォームが指定された場合のエラー"""
    def __init__(self, platform: str, valid_platforms: list):
        self.platform = platform
        self.valid_platforms = valid_platforms
        self.message = f"Invalid platform '{platform}'. Valid platforms: {', '.join(valid_platforms)}"
        super().__init__(self.message)


class APIKeyError(TRNBotError):
    """APIキーが無効または設定されていない場合のエラー"""
    def __init__(self):
        self.message = "TRN API key is not set or invalid"
        super().__init__(self.message)


class DataParseError(TRNBotError):
    """APIレスポンスのパースに失敗した場合のエラー"""
    def __init__(self, details: str = None):
        self.details = details
        self.message = f"Failed to parse API response data{': ' + details if details else ''}"
        super().__init__(self.message)


class NetworkError(TRNBotError):
    """ネットワーク接続エラー"""
    def __init__(self, details: str = None):
        self.details = details
        self.message = f"Network connection error{': ' + details if details else ''}"
        super().__init__(self.message)


class TimeoutError(TRNBotError):
    """リクエストタイムアウトエラー"""
    def __init__(self, timeout_seconds: int = None):
        self.timeout_seconds = timeout_seconds
        if timeout_seconds:
            self.message = f"Request timed out after {timeout_seconds} seconds"
        else:
            self.message = "Request timed out"
        super().__init__(self.message)