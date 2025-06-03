class AronaError(Exception):
    """ARONA系共通の基底例外"""
    pass

class AronaConfigNotFoundError(AronaError):
    HOW_TO_FIX = (
        "[HOW TO FIX]: 通常は起動時に自動的に設定ファイルが生成されます。"
        "基底コンフィグファイルが欠如している可能性があります。"
    )

    def __init__(self, message: str):
        super().__init__(f"{message}\n {self.HOW_TO_FIX}")

class AronaDefaultConfigNotFound(AronaError):
    HOW_TO_FIX = (
        "[HOW TO FIX]: 基底コンフィグファイルが欠如しています。再インストールするか、リポジトリから取得してください。"
    )
    def __init__(self, message: str):
        super().__init__(f"{message}\n {self.HOW_TO_FIX}")

class AronaFirstRunWarning(Warning):
    def __init__(self, message: str):
        super().__init__(message)