import json
import os
import re
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

from openai import OpenAI

BACKENDS = {
    "local": {"base_url": "http://localhost:11434/v1", "key_env": None},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY"},
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key_env": "GEMINI_API_KEY",
    },
}

# Change a line here to switch which model an agent uses.
# For Gemini use ("gemini", "<model name copied from Google AI Studio>").
ROLES = {
    "orchestrator": ("groq", "openai/gpt-oss-120b"),
    "reviewer": ("groq", "openai/gpt-oss-120b"),
    "researcher": ("groq", "openai/gpt-oss-120b"),
    "writer": ("groq", "openai/gpt-oss-120b"),
    "tester": ("groq", "openai/gpt-oss-120b"),
}

_clients = {}


def _client(name):
    if name not in _clients:
        cfg = BACKENDS[name]
        key = "ollama"
        if cfg["key_env"]:
            key = os.environ.get(cfg["key_env"])
            if not key:
                raise RuntimeError(f"Set the {cfg['key_env']} environment variable first")
        _clients[name] = OpenAI(base_url=cfg["base_url"], api_key=key, max_retries=5)
    return _clients[name]


def _parse_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start : end + 1])


def call_llm(role, system, user, json_mode=False):
    """Send one prompt to the model assigned to `role`.

    With json_mode=True, say "JSON" in your system prompt and get a dict back.
    """
    backend, model = ROLES[role]
    if model.startswith("qwen3"):
        user += " /no_think"  # skips Qwen3's slow reasoning mode on CPU
    kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
    resp = _client(backend).chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
        **kwargs,
    )
    text = resp.choices[0].message.content or ""
    return _parse_json(text) if json_mode else text


if __name__ == "__main__":
    print(call_llm("researcher", "You are concise.", "Name one use of a capacitor."))
    print(call_llm("orchestrator", "Reply with JSON only.", 'Return {"ok": true}', json_mode=True))