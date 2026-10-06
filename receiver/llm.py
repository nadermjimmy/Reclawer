"""Single entry point for Claude calls that must return JSON matching a schema."""
import json, os

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
_client = None


class LLMError(RuntimeError):
    pass


def call_json(system, content, schema, max_tokens=16000):
    """content: a string or a list of content blocks. Returns the parsed JSON object."""
    global _client
    import anthropic
    if _client is None:
        _client = anthropic.Anthropic()
    resp = _client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
        extra_body={"output_config": {"format": {"type": "json_schema", "schema": schema}}},
    )
    if resp.stop_reason == "refusal":
        raise LLMError("Claude declined this request")
    if resp.stop_reason == "max_tokens":
        raise LLMError("response was cut off (max_tokens); retry with a smaller batch")
    text = "".join(b.text for b in resp.content if b.type == "text")
    try:
        return json.loads(text)
    except ValueError as e:
        raise LLMError(f"invalid JSON from Claude: {e}") from e


def obj(props, required=None):
    """Small helper for strict JSON schemas."""
    return {"type": "object", "properties": props, "required": required or list(props),
            "additionalProperties": False}


STR = {"type": "string"}
BOOL = {"type": "boolean"}
