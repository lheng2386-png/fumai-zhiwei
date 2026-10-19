"""Server-only model configuration. Credentials never enter API responses."""
import os
from urllib.parse import urlsplit

from .model import ChatCompletionsModel, ResponsesModel


def configured_model(environ=None):
    env = os.environ if environ is None else environ
    key = (env.get("AGENT_API_KEY") or env.get("OPENAI_API_KEY") or "").strip()
    name = env.get("AGENT_MODEL", "").strip()
    protocol = env.get("AGENT_MODEL_API", "responses").strip().lower()
    if protocol not in {"responses", "chat_completions"}:
        raise ValueError("AGENT_MODEL_API must be responses or chat_completions")
    if bool(key) != bool(name):
        raise ValueError("Set both AGENT_API_KEY and AGENT_MODEL, or neither")
    if not key:
        return None
    if protocol == "responses":
        return ResponsesModel(key, name, env.get("AGENT_RESPONSES_URL", "https://api.openai.com/v1/responses"))
    base = env.get("AGENT_BASE_URL", "").strip().rstrip("/")
    url = urlsplit(base)
    if url.scheme != "https" or not url.netloc or url.username or url.password or url.query or url.fragment:
        raise ValueError("AGENT_BASE_URL must be an HTTPS API root without credentials or query parameters")
    if url.path.endswith(("/chat/completions", "/responses")):
        raise ValueError("AGENT_BASE_URL must be the API root, for example https://host/v1")
    return ChatCompletionsModel(key, name, base + "/chat/completions")
