"""LLM provider — routes calls to OpenAI or Google Gemini based on model config.

Uses langchain ChatModels for unified interface. Handles:
- Model routing per AGENT_LLM_MODELS
- Prompt template rendering
- JSON response parsing with fallback
- Token counting and cost tracking
- Rate limiting (basic)
"""

import json
import re
import time
from functools import lru_cache

from config import AGENT_LLM_MODELS, OPENAI_API_KEY, GOOGLE_API_KEY
from tools.logger import get_agent_logger

logger = get_agent_logger("llm")

# Rate limiting: track last call time per model
_last_call: dict[str, float] = {}
_MIN_INTERVAL = 0.5  # seconds between calls to same model


@lru_cache(maxsize=8)
def _get_openai_model(model_name: str):
    """Lazy-init OpenAI ChatModel."""
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=model_name,
        api_key=OPENAI_API_KEY,
        temperature=0.3,
        max_tokens=1024,
        request_timeout=30,
    )


@lru_cache(maxsize=4)
def _get_gemini_model(model_name: str):
    """Lazy-init Google Gemini ChatModel."""
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(
        model=model_name,
        google_api_key=GOOGLE_API_KEY,
        temperature=0.3,
        max_output_tokens=1024,
    )


def get_chat_model(agent_id: str):
    """Get the appropriate ChatModel for an agent based on AGENT_LLM_MODELS."""
    model_key = AGENT_LLM_MODELS.get(agent_id, "gpt-4o-mini")

    if model_key == "gpt-4o":
        return _get_openai_model("gpt-4o")
    elif model_key == "gpt-4o-mini":
        return _get_openai_model("gpt-4o-mini")
    elif model_key == "gemini-flash":
        return _get_gemini_model("gemini-2.5-flash")
    else:
        return _get_openai_model(model_key)


def render_prompt(template: str, variables: dict) -> str:
    """Render a prompt template by substituting {variable} placeholders.

    Handles missing variables gracefully — leaves placeholder as-is
    if not provided.
    """
    result = template
    for key, value in variables.items():
        result = result.replace(f"{{{key}}}", str(value))
    return result


def call_llm(agent_id: str, system_prompt: str, user_prompt: str,
             expect_json: bool = True) -> dict | str:
    """Call the LLM for an agent and return the response.

    Args:
        agent_id: Agent making the call (for model routing)
        system_prompt: System message content
        user_prompt: User/human message content
        expect_json: If True, parse response as JSON

    Returns:
        Parsed JSON dict if expect_json, else raw string
    """
    from langchain_core.messages import SystemMessage, HumanMessage

    # Basic rate limiting
    now = time.time()
    model_key = AGENT_LLM_MODELS.get(agent_id, "gpt-4o-mini")
    last = _last_call.get(model_key, 0)
    if now - last < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - (now - last))
    _last_call[model_key] = time.time()

    model = get_chat_model(agent_id)

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    try:
        response = model.invoke(messages)
        raw_text = response.content

        logger.debug(
            f"[{agent_id}] LLM call ({model_key}) — "
            f"prompt_len={len(system_prompt) + len(user_prompt)}, "
            f"response_len={len(raw_text)}"
        )

        if not expect_json:
            return raw_text

        return parse_json_response(raw_text)

    except Exception as e:
        logger.error(f"[{agent_id}] LLM call failed: {e}")
        raise


def parse_json_response(text: str) -> dict:
    """Extract and parse JSON from LLM response text.

    Handles common LLM quirks:
    - JSON wrapped in ```json ... ``` fences
    - Extra text before/after JSON
    - Trailing commas (best effort)
    """
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from code fences
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try finding first { ... } block
    brace_match = re.search(r"\{[\s\S]*\}", text)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    # Last resort: return as error dict
    logger.warning(f"Failed to parse JSON from LLM response: {text[:200]}")
    return {"_raw": text, "_parse_error": True}
