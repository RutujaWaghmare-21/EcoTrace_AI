"""
EcoTrace AI - LLM client wrapper (Google Gemini)

Single place where all Gemini API calls happen: chat (including
tool/function calling) and embeddings. Every agent goes through this
module so the rest of the codebase never touches the google-genai SDK
directly, and never needs to know it isn't OpenAI under the hood.

Uses Gemini's free tier (Gemini API / Google AI Studio) - no credit card
required. Get a key at https://aistudio.google.com/apikey
"""
import json
from typing import Any, Callable, Optional

from google import genai
from google.genai import types

import config

_client: Optional[genai.Client] = None


def get_client() -> genai.Client:
    global _client
    if _client is None:
        if not config.GEMINI_API_KEY:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Copy .env.example to .env and "
                "add your key (free, no credit card - get one at "
                "https://aistudio.google.com/apikey), or set it as an "
                "environment variable."
            )
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of strings using the configured Gemini embedding model."""
    if not texts:
        return []
    client = get_client()
    resp = client.models.embed_content(
        model=config.EMBED_MODEL,
        contents=texts,
        config=types.EmbedContentConfig(output_dimensionality=config.EMBED_DIM),
    )
    return [e.values for e in resp.embeddings]


def _openai_schema_to_gemini_tool(openai_tool: dict) -> types.Tool:
    """
    Convert an OpenAI-style tool schema (as used elsewhere in this codebase,
    e.g. tools/carbon_calculator.py) into a Gemini types.Tool. Keeps the rest
    of the project's tool-schema definitions provider-agnostic.
    """
    fn = openai_tool["function"]
    declaration = types.FunctionDeclaration(
        name=fn["name"],
        description=fn.get("description", ""),
        parameters_json_schema=fn.get("parameters", {"type": "object", "properties": {}}),
    )
    return types.Tool(function_declarations=[declaration])


def _history_to_gemini_contents(messages: list[dict]) -> tuple[Optional[str], list[types.Content]]:
    """
    Convert our internal OpenAI-style message list
    ([{"role": "system"/"user"/"assistant"/"tool", "content": ...}, ...])
    into (system_instruction, gemini_contents).
    """
    system_instruction = None
    contents: list[types.Content] = []

    for msg in messages:
        role = msg["role"]
        if role == "system":
            # Gemini takes system instructions separately, not as a turn
            system_instruction = (system_instruction or "") + msg["content"] + "\n"
        elif role == "user":
            contents.append(types.Content(role="user", parts=[types.Part.from_text(text=msg["content"])]))
        elif role == "assistant":
            contents.append(types.Content(role="model", parts=[types.Part.from_text(text=msg["content"])]))
        elif role == "tool_call":
            # Internal marker we add ourselves when replaying a function call turn
            contents.append(
                types.Content(
                    role="model",
                    parts=[types.Part.from_function_call(name=msg["name"], args=msg["args"])],
                )
            )
        elif role == "tool":
            contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_function_response(
                            name=msg["name"], response={"result": msg["content"]}
                        )
                    ],
                )
            )
    return system_instruction, contents


def chat(
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    tool_executor: Optional[Callable[[str, dict], Any]] = None,
    temperature: float = 0.2,
    max_tool_rounds: int = 6,
) -> dict:
    """
    Run a Gemini chat completion, optionally with tool calling.

    `messages` uses the same OpenAI-style shape the rest of this codebase
    already uses: [{"role": "system"/"user"/"assistant", "content": str}].
    `tools` uses the same OpenAI-style tool schema shape (see
    tools/carbon_calculator.py) and is converted internally to Gemini's
    format, so callers don't need to change.

    If `tools` is provided along with `tool_executor`, this function will
    automatically loop: send messages -> if the model requests a tool call,
    execute it via `tool_executor(tool_name, arguments_dict)` -> feed the
    result back -> repeat, until the model returns a plain text answer or
    `max_tool_rounds` is hit.

    Returns a dict: {
        "content": str,              # final assistant text
        "tool_calls_made": [...],    # log of (name, args, result) for transparency
        "messages": [...]            # updated internal message history
    }
    """
    client = get_client()
    history = list(messages)
    tool_log = []

    gemini_tools = [_openai_schema_to_gemini_tool(t) for t in tools] if tools else None

    for _ in range(max_tool_rounds):
        system_instruction, contents = _history_to_gemini_contents(history)

        gen_config = types.GenerateContentConfig(
            temperature=temperature,
            system_instruction=system_instruction,
        )
        if gemini_tools:
            gen_config.tools = gemini_tools
            # AUTO lets the model decide whether to call a tool or answer directly
            gen_config.tool_config = types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="AUTO")
            )

        response = client.models.generate_content(
            model=config.CHAT_MODEL,
            contents=contents,
            config=gen_config,
        )

        function_calls = response.function_calls or []

        if function_calls and tool_executor:
            for fc in function_calls:
                args = dict(fc.args or {})
                try:
                    result = tool_executor(fc.name, args)
                except Exception as e:  # noqa: BLE001
                    result = {"error": str(e)}

                tool_log.append({"name": fc.name, "args": args, "result": result})
                # Record the model's function-call turn, then our function response turn
                history.append({"role": "tool_call", "name": fc.name, "args": args})
                history.append({"role": "tool", "name": fc.name, "content": json.dumps(result, default=str)})
            continue  # loop again so the model sees the tool results

        # No tool call -> final answer
        text = response.text or ""
        history.append({"role": "assistant", "content": text})
        return {
            "content": text,
            "tool_calls_made": tool_log,
            "messages": history,
        }

    return {
        "content": "I reached the maximum number of tool-call steps without "
        "finishing. Please simplify the request or try again.",
        "tool_calls_made": tool_log,
        "messages": history,
    }
