"""Read models.yaml and build the one model named by active."""
import os
import re
from pathlib import Path

import yaml

from models.registry import registry

BACKEND = Path(__file__).resolve().parents[1]
SPEC = BACKEND / "models.yaml"
_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def load_model(name: str | None = None):
    spec = _read_spec()
    chosen = name or str(spec.get("active") or "").strip()
    models = spec.get("models") or {}
    if chosen not in models:
        names = "、".join(models) or "（空）"
        raise RuntimeError(f"models.yaml 里没有模型 {chosen}，可选: {names}")
    entry = dict(models[chosen] or {})
    kind = str(entry.pop("kind", "") or "").strip()
    if not kind:
        raise RuntimeError(f"模型 {chosen} 没有填写 kind")
    entry["name"] = chosen
    model = registry.create(kind, entry)
    print(f"当前模型: {model.name}（{model.kind}）")
    return model


def _read_spec() -> dict:
    _load_dotenv(BACKEND.parent / ".env")
    if not SPEC.exists():
        raise RuntimeError(f"找不到 {SPEC}")
    data = yaml.safe_load(SPEC.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise RuntimeError("models.yaml 需要是一个映射")
    return _substitute(data)


def _substitute(value):
    if isinstance(value, str):
        return _ENV.sub(lambda match: os.environ.get(match.group(1), ""), value)
    if isinstance(value, dict):
        return {key: _substitute(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute(item) for item in value]
    return value


def _load_dotenv(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:]
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
