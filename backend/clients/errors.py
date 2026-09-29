"""Configuration errors shared by external API clients."""


class MissingAPIKeyError(RuntimeError):
    def __init__(self, setting: str):
        self.setting = setting
        super().__init__(f"未配置 {setting}，请在设置页面填写后重试")
