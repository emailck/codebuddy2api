import json
import os
import time
import uuid
from typing import AsyncGenerator, Optional

import httpx

from . import accounts, db, keys

PASSTHROUGH_BODY_KEYS = {
    "model",
    "messages",
    "tools",
    "tool_choice",
    "temperature",
    "max_tokens",
    "max_completion_tokens",
    "top_p",
    "stream",
    "stream_options",
    "stop",
    "presence_penalty",
    "frequency_penalty",
    "n",
    "response_format",
    "seed",
    "user",
    "reasoning_effort",
    "verbosity",
    "reasoning_summary",
}

MODEL_ALIASES = {
    "gpt-4o": "glm-5.2",
    "gpt-4o-mini": "glm-5.1",
    "gpt-4": "glm-5.2",
    "gpt-3.5-turbo": "glm-5.1",
    "deepseek-chat": "deepseek-v4-pro",
    "deepseek-coder": "deepseek-v4-pro",
    "moonshot-v1-128k": "kimi-k2.7",
}


def resolve_model(model: str) -> str:
    aliases = db.get_setting("model_aliases", {}) or {}
    merged = {**MODEL_ALIASES, **aliases}
    return merged.get(model, model)


def build_chat_body(payload: dict) -> dict:
    body = {k: payload[k] for k in PASSTHROUGH_BODY_KEYS if k in payload}
    body["model"] = resolve_model(str(body.get("model") or "auto-chat"))
    body["stream"] = True
    if "stream_options" not in body:
        body["stream_options"] = {"include_usage": True}
    messages = body.get("messages") or []
    if len(messages) == 1 and messages[0].get("role") == "user":
        body["messages"] = [{"role": "system", "content": "You are a helpful assistant."}] + messages
    return body


def responses_to_chat(payload: dict) -> dict:
    messages = []
    instructions = payload.get("instructions")
    if instructions:
        messages.append({"role": "system", "content": instructions})
    raw_input = payload.get("input", "")
    if isinstance(raw_input, str):
        messages.append({"role": "user", "content": raw_input})
    elif isinstance(raw_input, list):
        for item in raw_input:
            if isinstance(item, str):
                messages.append({"role": "user", "content": item})
            elif isinstance(item, dict):
                typ = item.get("type")
                if typ == "message":
                    role = item.get("role") or "user"
                    content = item.get("content", "")
                    msg = {"role": role, "content": normalize_content(content)}
                    if item.get("tool_calls"):
                        msg["tool_calls"] = item["tool_calls"]
                    messages.append(msg)
                elif typ == "function_call":
                    call_id = item.get("call_id") or item.get("id") or "call_" + os.urandom(8).hex()
                    messages.append(
                        {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": call_id,
                                    "type": "function",
                                    "function": {
                                        "name": item.get("name") or "",
                                        "arguments": item.get("arguments") or "",
                                    },
                                }
                            ],
                        }
                    )
                elif typ in ("function_call_output", "tool_result"):
                    msg = {"role": "tool", "content": item.get("output") or item.get("content") or ""}
                    call_id = item.get("call_id") or item.get("tool_call_id")
                    if call_id:
                        msg["tool_call_id"] = call_id
                    messages.append(msg)
    if not messages:
        messages = [{"role": "user", "content": ""}]
    out = {
        "model": payload.get("model") or "auto-chat",
        "messages": messages,
        "stream": bool(payload.get("stream", False)),
    }
    if payload.get("tools"):
        out["tools"] = normalize_tools_for_chat(payload["tools"])
    if payload.get("tool_choice") is not None:
        out["tool_choice"] = normalize_tool_choice_for_chat(payload["tool_choice"])
    if payload.get("temperature") is not None:
        out["temperature"] = payload["temperature"]
    if payload.get("top_p") is not None:
        out["top_p"] = payload["top_p"]
    if payload.get("max_output_tokens") is not None:
        out["max_tokens"] = payload["max_output_tokens"]
    return out


def normalize_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") in ("input_text", "output_text", "text"):
                    parts.append(str(part.get("text") or ""))
                else:
                    parts.append(json.dumps(part, ensure_ascii=False))
            else:
                parts.append(str(part))
        return "".join(parts)
    return "" if content is None else str(content)


def normalize_tools_for_chat(tools):
    if not isinstance(tools, list):
        return tools
    out = []
    for tool in tools:
        if not isinstance(tool, dict):
            out.append(tool)
            continue
        if tool.get("type") == "function" and "function" not in tool:
            fn = {
                "name": tool.get("name") or "",
                "description": tool.get("description") or "",
                "parameters": tool.get("parameters") or {},
            }
            if "strict" in tool:
                fn["strict"] = tool["strict"]
            out.append({"type": "function", "function": fn})
        else:
            out.append(tool)
    return out


def normalize_tool_choice_for_chat(choice):
    if not isinstance(choice, dict):
        return choice
    if choice.get("type") == "function" and "function" not in choice:
        return {"type": "function", "function": {"name": choice.get("name") or ""}}
    return choice


def parse_sse_data(line: str) -> Optional[dict]:
    line = line.strip()
    if not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if not data or data == "[DONE]":
        return None
    try:
        return json.loads(data)
    except Exception:
        return None


def sse_error(status: int, message: str) -> bytes:
    payload = json.dumps({"error": {"message": message[:800], "type": "upstream_error", "code": status}}, ensure_ascii=False)
    return f"data: {payload}\n\ndata: [DONE]\n\n".encode("utf-8")


def responses_sse_event(event_type: str, payload: dict) -> bytes:
    payload = dict(payload)
    payload.setdefault("type", event_type)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event_type}\ndata: {raw}\n\n".encode("utf-8")


def responses_usage(chat_usage: dict) -> dict:
    chat_usage = chat_usage or {}
    input_tokens = int(chat_usage.get("input_tokens") or chat_usage.get("prompt_tokens") or 0)
    output_tokens = int(chat_usage.get("output_tokens") or chat_usage.get("completion_tokens") or 0)
    total_tokens = int(chat_usage.get("total_tokens") or input_tokens + output_tokens)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def response_base(response_id: str, model: str, status: str, output: list, usage: Optional[dict] = None) -> dict:
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "model": model,
        "output": output,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "metadata": {},
        "usage": usage,
    }


async def proxy_chat(payload: dict, client_key: Optional[dict]) -> tuple:
    wants_stream = bool(payload.get("stream"))
    body = build_chat_body(payload)
    tried: set[int] = set()
    last_error = None
    for _ in range(3):
        account = accounts.pick_account(tried)
        if not account:
            return ("error", (503, {"error": {"message": "No available CodeBuddy accounts", "type": "server_error"}}))
        tried.add(account["id"])
        if not accounts.maybe_refresh_token(account):
            last_error = "token refresh failed"
            continue
        fresh = accounts.get_account(account["id"], include_secret=True)
        if not fresh:
            continue
        headers = accounts.build_headers(
            fresh,
            {
                "conversation_id": str(uuid.uuid4()),
                "conversation_request_id": os.urandom(16).hex(),
                "conversation_message_id": uuid.uuid4().hex,
                "request_id": uuid.uuid4().hex,
            },
        )
        url = f"{accounts.normalize_endpoint(fresh.get('endpoint'))}/v2/chat/completions"
        started = time.time()
        if wants_stream:
            return ("stream", stream_upstream(url, headers, body, fresh, client_key, payload.get("model", "auto-chat"), started))
        result = await collect_upstream(url, headers, body, fresh, client_key, payload.get("model", "auto-chat"), started)
        if result[0] == "error" and result[1][0] in (401, 403):
            last_error = result[1][1]
            accounts.mark_error(fresh["id"], json.dumps(last_error, ensure_ascii=False)[:500])
            continue
        return result
    return ("error", (502, {"error": {"message": f"All CodeBuddy accounts failed: {last_error}", "type": "server_error"}}))


async def stream_upstream(url: str, headers: dict, body: dict, account: dict, client_key: Optional[dict], model: str, started: float) -> AsyncGenerator[bytes, None]:
    usage = {}
    finish_reason = "stop"
    status_code = 200
    error = ""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=15, read=300, write=30, pool=10)) as client:
            async with client.stream("POST", url, headers=headers, json=body) as resp:
                status_code = resp.status_code
                if resp.status_code < 200 or resp.status_code >= 300:
                    raw = await resp.aread()
                    error = raw.decode("utf-8", "replace")[:500]
                    yield sse_error(resp.status_code, error)
                    return
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    obj = parse_sse_data(line)
                    if obj:
                        if obj.get("usage"):
                            usage.update(obj["usage"])
                        for choice in obj.get("choices") or []:
                            if choice.get("finish_reason"):
                                finish_reason = choice["finish_reason"]
                    if line.strip() == "data: [DONE]":
                        yield b"data: [DONE]\n\n"
                    elif line.startswith("data:"):
                        yield (line + "\n\n").encode("utf-8")
    except Exception as exc:
        status_code = 502
        error = str(exc)[:500]
        yield sse_error(502, error)
    finally:
        total = int(usage.get("total_tokens") or 0)
        log_request(client_key, account, model, True, usage, finish_reason, status_code, error, started)
        if status_code == 200:
            accounts.mark_success(account["id"], total)
            if client_key:
                keys.touch_key(client_key["id"], total)


async def collect_upstream(url: str, headers: dict, body: dict, account: dict, client_key: Optional[dict], model: str, started: float) -> tuple:
    content_parts = []
    reasoning_parts = []
    tool_calls: dict[int, dict] = {}
    usage = {}
    finish_reason = "stop"
    response_model = model
    status_code = 200
    error = ""
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream("POST", url, headers=headers, json=body) as resp:
                status_code = resp.status_code
                if resp.status_code < 200 or resp.status_code >= 300:
                    raw = await resp.aread()
                    error = raw.decode("utf-8", "replace")[:500]
                    log_request(client_key, account, model, False, {}, "error", status_code, error, started)
                    return ("error", (status_code, {"error": {"message": error, "type": "upstream_error"}}))
                async for line in resp.aiter_lines():
                    obj = parse_sse_data(line)
                    if not obj:
                        continue
                    response_model = obj.get("model") or response_model
                    if obj.get("usage"):
                        usage.update(obj["usage"])
                    for choice in obj.get("choices") or []:
                        finish_reason = choice.get("finish_reason") or finish_reason
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            content_parts.append(delta["content"])
                        if delta.get("reasoning_content"):
                            reasoning_parts.append(delta["reasoning_content"])
                        for tc in delta.get("tool_calls") or []:
                            idx = int(tc.get("index") or 0)
                            slot = tool_calls.setdefault(idx, {"id": None, "type": "function", "function": {"name": "", "arguments": ""}})
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["function"]["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["function"]["arguments"] += fn["arguments"]
    except Exception as exc:
        error = str(exc)[:500]
        log_request(client_key, account, model, False, {}, "network_error", 502, error, started)
        return ("error", (502, {"error": {"message": error, "type": "upstream_error"}}))

    message = {"role": "assistant", "content": "".join(content_parts) or None}
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    if tool_calls:
        message["tool_calls"] = [v for _, v in sorted(tool_calls.items())]
        finish_reason = finish_reason or "tool_calls"
    result = {
        "id": "chatcmpl-" + os.urandom(12).hex(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": response_model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason or "stop"}],
        "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
    total = int((usage or {}).get("total_tokens") or 0)
    log_request(client_key, account, model, False, usage, finish_reason, 200, "", started)
    accounts.mark_success(account["id"], total)
    if client_key:
        keys.touch_key(client_key["id"], total)
    return ("json", result)


async def proxy_responses(payload: dict, client_key: Optional[dict]) -> tuple:
    chat_payload = responses_to_chat(payload)
    if payload.get("stream"):
        chat_payload["stream"] = True
        return await proxy_responses_stream(payload, chat_payload, client_key)

    chat_payload["stream"] = False
    result = await proxy_chat(chat_payload, client_key)
    if result[0] != "json":
        return result
    return ("json", chat_to_response(result[1]))


async def proxy_responses_stream(payload: dict, chat_payload: dict, client_key: Optional[dict]) -> tuple:
    body = build_chat_body(chat_payload)
    tried: set[int] = set()
    last_error = None
    for _ in range(3):
        account = accounts.pick_account(tried)
        if not account:
            return ("error", (503, {"error": {"message": "No available CodeBuddy accounts", "type": "server_error"}}))
        tried.add(account["id"])
        if not accounts.maybe_refresh_token(account):
            last_error = "token refresh failed"
            continue
        fresh = accounts.get_account(account["id"], include_secret=True)
        if not fresh:
            continue
        headers = accounts.build_headers(
            fresh,
            {
                "conversation_id": str(uuid.uuid4()),
                "conversation_request_id": os.urandom(16).hex(),
                "conversation_message_id": uuid.uuid4().hex,
                "request_id": uuid.uuid4().hex,
            },
        )
        url = f"{accounts.normalize_endpoint(fresh.get('endpoint'))}/v2/chat/completions"
        started = time.time()
        model = str(payload.get("model") or chat_payload.get("model") or "auto-chat")
        return ("stream", stream_responses_upstream(url, headers, body, fresh, client_key, model, started))
    return ("error", (502, {"error": {"message": f"All CodeBuddy accounts failed: {last_error}", "type": "server_error"}}))


def chat_to_response(chat: dict) -> dict:
    message = (chat.get("choices") or [{}])[0].get("message") or {}
    text = message.get("content") or ""
    response_id = "resp_" + os.urandom(12).hex()
    output = []
    if text or not message.get("tool_calls"):
        output.append(message_output_item("msg_" + os.urandom(8).hex(), "completed", text))
    for tc in message.get("tool_calls") or []:
        output.append(tool_call_output_item(tc, "completed"))
    usage = responses_usage(chat.get("usage"))
    response = {
        **response_base(response_id, chat.get("model") or "auto-chat", "completed", output, usage),
        "output_text": text,
        "error": None,
        "incomplete_details": None,
    }
    return response


def message_output_item(item_id: str, status: str, text: str) -> dict:
    return {
        "id": item_id,
        "type": "message",
        "status": status,
        "role": "assistant",
        "content": [{"type": "output_text", "text": text, "annotations": [], "logprobs": []}],
    }


def tool_call_output_item(tool_call: dict, status: str) -> dict:
    fn = tool_call.get("function") or {}
    call_id = tool_call.get("id") or "call_" + os.urandom(8).hex()
    item_id = tool_call.get("_response_item_id") or "fc_" + os.urandom(8).hex()
    return {
        "id": item_id,
        "type": "function_call",
        "status": status,
        "call_id": call_id,
        "name": fn.get("name") or "",
        "arguments": fn.get("arguments") or "",
    }


async def stream_responses_upstream(url: str, headers: dict, body: dict, account: dict, client_key: Optional[dict], model: str, started: float) -> AsyncGenerator[bytes, None]:
    response_id = "resp_" + os.urandom(12).hex()
    msg_id = "msg_" + os.urandom(8).hex()
    content_parts = []
    tool_calls: dict[int, dict] = {}
    usage = {}
    finish_reason = "stop"
    response_model = resolve_model(model)
    status_code = 200
    error = ""
    message_opened = False
    sequence = 0

    def emit(event_type: str, payload: dict) -> bytes:
        nonlocal sequence
        payload.setdefault("sequence_number", sequence)
        sequence += 1
        return responses_sse_event(event_type, payload)

    def completed_response(status: str = "completed") -> dict:
        text = "".join(content_parts)
        output = []
        if message_opened or text or not tool_calls:
            output.append(message_output_item(msg_id, "completed", text))
        for _, tc in sorted(tool_calls.items()):
            output.append(tool_call_output_item(tc, "completed"))
        resp = response_base(response_id, response_model, status, output, responses_usage(usage))
        resp["error"] = None
        resp["incomplete_details"] = {"reason": "max_output_tokens"} if finish_reason == "length" else None
        return resp

    try:
        yield emit(
            "response.created",
            {"response": response_base(response_id, response_model, "in_progress", [], None)},
        )
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=15, read=300, write=30, pool=10)) as client:
            async with client.stream("POST", url, headers=headers, json=body) as resp:
                status_code = resp.status_code
                if resp.status_code < 200 or resp.status_code >= 300:
                    raw = await resp.aread()
                    error = raw.decode("utf-8", "replace")[:500]
                    failed = response_base(response_id, response_model, "failed", [], None)
                    failed["error"] = {"code": str(resp.status_code), "message": error}
                    yield emit("response.failed", {"response": failed})
                    yield b"data: [DONE]\n\n"
                    return
                async for line in resp.aiter_lines():
                    obj = parse_sse_data(line)
                    if not obj:
                        continue
                    response_model = obj.get("model") or response_model
                    if obj.get("usage"):
                        usage.update(obj["usage"])
                    for choice in obj.get("choices") or []:
                        finish_reason = choice.get("finish_reason") or finish_reason
                        delta = choice.get("delta") or {}
                        text_delta = delta.get("content") or ""
                        if text_delta:
                            if not message_opened:
                                message_opened = True
                                yield emit("response.output_item.added", {"output_index": 0, "item": message_output_item(msg_id, "in_progress", "")})
                                yield emit(
                                    "response.content_part.added",
                                    {
                                        "output_index": 0,
                                        "content_index": 0,
                                        "item_id": msg_id,
                                        "part": {"type": "output_text", "text": "", "annotations": [], "logprobs": []},
                                    },
                                )
                            content_parts.append(text_delta)
                            yield emit(
                                "response.output_text.delta",
                                {
                                    "output_index": 0,
                                    "content_index": 0,
                                    "item_id": msg_id,
                                    "delta": text_delta,
                                },
                            )
                        for tc in delta.get("tool_calls") or []:
                            idx = int(tc.get("index") or 0)
                            slot = tool_calls.setdefault(idx, {"id": None, "type": "function", "function": {"name": "", "arguments": ""}})
                            slot.setdefault("_response_item_id", "fc_" + os.urandom(8).hex())
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["function"]["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["function"]["arguments"] += fn["arguments"]
    except Exception as exc:
        status_code = 502
        error = str(exc)[:500]
        failed = response_base(response_id, response_model, "failed", [], None)
        failed["error"] = {"code": "upstream_error", "message": error}
        yield emit("response.failed", {"response": failed})
        yield b"data: [DONE]\n\n"
    else:
        text = "".join(content_parts)
        if message_opened:
            yield emit(
                "response.output_text.done",
                {"output_index": 0, "content_index": 0, "item_id": msg_id, "text": text},
            )
            yield emit(
                "response.content_part.done",
                {
                    "output_index": 0,
                    "content_index": 0,
                    "item_id": msg_id,
                    "part": {"type": "output_text", "text": text, "annotations": [], "logprobs": []},
                },
            )
            yield emit("response.output_item.done", {"output_index": 0, "item": message_output_item(msg_id, "completed", text)})
        next_output_index = 1 if message_opened else 0
        for _, tc in sorted(tool_calls.items()):
            item = tool_call_output_item(tc, "completed")
            yield emit("response.output_item.added", {"output_index": next_output_index, "item": {**item, "status": "in_progress", "arguments": ""}})
            yield emit(
                "response.function_call_arguments.done",
                {
                    "output_index": next_output_index,
                    "item_id": item["id"],
                    "call_id": item["call_id"],
                    "name": item["name"],
                    "arguments": item["arguments"],
                },
            )
            yield emit("response.output_item.done", {"output_index": next_output_index, "item": item})
            next_output_index += 1
        status = "incomplete" if finish_reason == "length" else "completed"
        yield emit("response.completed", {"response": completed_response(status)})
        yield b"data: [DONE]\n\n"
    finally:
        total = int((usage or {}).get("total_tokens") or 0)
        log_request(client_key, account, model, True, usage, finish_reason, status_code, error, started)
        if status_code == 200:
            accounts.mark_success(account["id"], total)
            if client_key:
                keys.touch_key(client_key["id"], total)


def log_request(client_key, account, model, stream, usage, finish_reason, status_code, error, started):
    db.add_log(
        {
            "client_key_id": client_key.get("id") if client_key else None,
            "client_key_name": client_key.get("name") if client_key else None,
            "account_id": account.get("id") if account else None,
            "account_name": account.get("name") if account else None,
            "model": model,
            "stream": stream,
            "prompt_tokens": int((usage or {}).get("prompt_tokens") or 0),
            "completion_tokens": int((usage or {}).get("completion_tokens") or 0),
            "total_tokens": int((usage or {}).get("total_tokens") or 0),
            "status_code": status_code,
            "finish_reason": finish_reason,
            "error": error,
            "duration_ms": int((time.time() - started) * 1000),
        }
    )
