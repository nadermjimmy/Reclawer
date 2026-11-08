"""Claude calls that must return JSON matching a schema: live (streamed) and through the Message Batches API.

Models: CLAUDE_MODEL reads text (messages, spreadsheet rows); CLAUDE_FILE_MODEL reads PDFs and images, where
layout, scans and tables make accuracy matter most.
"""
import json, os

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
FILE_MODEL = os.getenv("CLAUDE_FILE_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("CLAUDE_EFFORT", "medium")
MAX_TOKENS = 32000  # thinking counts toward this; live calls stream, so no HTTP timeout concern
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACKS = os.getenv("CLAUDE_FALLBACKS", "1") != "0"  # set 0 if the account can't use server-side fallback
_client = None


class LLMError(RuntimeError):
    pass


def client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()
    return _client


def params(system, content, schema, model=None, effort=None, max_tokens=MAX_TOKENS):
    """Request body shared by live calls and batch requests."""
    return {
        "model": model or MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": content}],
        "output_config": {"effort": effort or EFFORT,
                          "format": {"type": "json_schema", "schema": schema}},
    }


def parse_message(msg):
    """Validate a finished message and return its JSON payload."""
    if msg.stop_reason == "refusal":
        raise LLMError("Claude declined this request")
    if msg.stop_reason == "max_tokens":
        raise LLMError("response was cut off (max_tokens); retry with a smaller batch")
    text = "".join(b.text for b in msg.content if b.type == "text")
    try:
        return json.loads(text)
    except ValueError as e:
        raise LLMError(f"invalid JSON from Claude: {e}") from e


def call_json(system, content, schema, max_tokens=MAX_TOKENS, model=None, effort=None):
    """content: a string or a list of content blocks. Returns the parsed JSON object.
    If a safety classifier declines, the API re-runs the request on a fallback model in the same call."""
    global FALLBACKS
    import anthropic
    body = params(system, content, schema, model, effort, max_tokens)
    if FALLBACKS:
        try:
            with client().beta.messages.stream(**body, betas=[FALLBACK_BETA], fallbacks="default") as stream:
                return parse_message(stream.get_final_message())
        except anthropic.BadRequestError as e:
            if "fallback" not in str(e).lower():
                raise
            FALLBACKS = False  # this account/model can't use server-side fallback: send plain requests from now on
    with client().messages.stream(**body) as stream:
        return parse_message(stream.get_final_message())


# ---------- Message Batches (50% cost, results within 24 h, usually under 1 h) ----------
def batch_submit(requests):
    """requests: [(custom_id, params_dict)]. Returns the batch id."""
    batch = client().messages.batches.create(
        requests=[{"custom_id": cid, "params": p} for cid, p in requests])
    return batch.id


def batch_status(batch_id):
    b = client().messages.batches.retrieve(batch_id)
    return b.processing_status, b.request_counts


def batch_results(batch_id):
    """Yield (custom_id, payload or None, error text or None)."""
    for r in client().messages.batches.results(batch_id):
        if r.result.type == "succeeded":
            try:
                yield r.custom_id, parse_message(r.result.message), None
            except LLMError as e:
                yield r.custom_id, None, str(e)
        else:
            yield r.custom_id, None, r.result.type


def obj(props, required=None):
    """Small helper for strict JSON schemas."""
    return {"type": "object", "properties": props, "required": required or list(props),
            "additionalProperties": False}


STR = {"type": "string"}
BOOL = {"type": "boolean"}
