# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "litellm>=1.98,<2",
# ]
# ///
"""Provider-neutral live-model driver for the agent token-cost benchmark."""

from __future__ import annotations

import json
import sys
from typing import Any, cast

from litellm import completion


def _usage(response: Any) -> dict[str, int]:  # noqa: ANN401
    """Return provider-reported usage normalized by LiteLLM."""
    usage = response.usage
    if usage is None:
        message = "LiteLLM response did not include provider token usage"
        raise RuntimeError(message)
    input_tokens = getattr(usage, "prompt_tokens", None)
    output_tokens = getattr(usage, "completion_tokens", None)
    total_tokens = getattr(usage, "total_tokens", None)
    if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
        message = "LiteLLM provider usage must include integer prompt/completion tokens"
        raise RuntimeError(message)
    if total_tokens is None:
        total_tokens = input_tokens + output_tokens
    if not isinstance(total_tokens, int):
        message = "LiteLLM provider usage total_tokens must be an integer"
        raise RuntimeError(message)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "calls": 1,
    }


def main() -> None:
    """Run one benchmark prompt through any LiteLLM-supported model/provider."""
    request = json.load(sys.stdin)
    if not isinstance(request, dict):
        message = "driver input must be one JSON object"
        raise TypeError(message)
    model = request.get("model")
    prompt = request.get("prompt")
    if not isinstance(model, str) or not model:
        message = "driver input must contain a non-empty LiteLLM model id"
        raise ValueError(message)
    if not isinstance(prompt, str):
        message = "driver input must contain a prompt string"
        raise TypeError(message)

    response = completion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        num_retries=0,
    )
    content = response.choices[0].message.content
    if not isinstance(content, str):
        message = "LiteLLM model output must contain text"
        raise TypeError(message)
    answer = json.loads(content)
    if not isinstance(answer, dict):
        message = "model output must be one JSON object"
        raise TypeError(message)

    payload = {
        "answer": cast("dict[str, object]", answer),
        "usage": _usage(response),
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
