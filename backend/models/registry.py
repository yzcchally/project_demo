"""Maps a YAML kind name to the class that implements it."""


class Registry:
    def __init__(self):
        self._kinds: dict[str, type] = {}

    def register(self, kind: str):
        def decorator(cls):
            self._kinds[kind] = cls
            cls.kind = kind
            return cls
        return decorator

    def create(self, kind: str, options: dict):
        cls = self._kinds.get(kind)
        if cls is None:
            names = "、".join(sorted(self._kinds)) or "（空）"
            raise ValueError(f"未知模型类型 {kind}，已注册的类型: {names}")
        return cls(options)

    def kinds(self) -> list[str]:
        return sorted(self._kinds)


registry = Registry()
