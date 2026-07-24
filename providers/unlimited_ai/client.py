import json
import uuid
import httpx
from collections.abc import AsyncIterator
from typing import Any

from providers.base import BaseProvider, ProviderConfig
from core.anthropic.sse import SSEBuilder
from providers.model_listing import ProviderModelInfo

class UnlimitedAIProvider(BaseProvider):
    """Provider that routes standard Anthropic Message requests to the unlimited-ai Cloudflare worker proxy."""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._client = httpx.AsyncClient(timeout=60.0)

    async def cleanup(self) -> None:
        await self._client.aclose()

    async def list_model_ids(self) -> frozenset[str]:
        # Expose default model identifiers
        return frozenset(["gateway-claude-opus-4-7", "gateway-gpt-5-5", "gateway-gpt-o3", "gateway-deepseek-r1", "gateway-gemini-3-pro"])

    async def list_model_infos(self) -> frozenset[ProviderModelInfo]:
        from providers.model_listing import model_infos_from_ids
        return model_infos_from_ids(await self.list_model_ids())

    async def stream_response(
        self,
        request: Any,
        input_tokens: int = 0,
        *,
        request_id: str | None = None,
        thinking_enabled: bool | None = None,
    ) -> AsyncIterator[str]:
        # 1. Consolidate messages and system prompt into a single flat conversation history
        system_prompt = request.system or ""
        messages = request.messages or []
        
        conversation = ""
        if system_prompt:
            conversation += f"{system_prompt}\n\n"
            
        for msg in messages:
            role_label = "User" if msg.role == "user" else "Assistant"
            content_text = ""
            if isinstance(msg.content, str):
                content_text = msg.content
            elif isinstance(msg.content, list):
                for block in msg.content:
                    if block.get("type") == "text":
                        content_text += block.get("text", "")
                    elif block.get("type") == "tool_use":
                        content_text += f"\n<{block.get('name')} id=\"{block.get('id')}\">\n{json.dumps(block.get('input'))}\n</{block.get('name')}>"
                    elif block.get("type") == "tool_result":
                        content_text += f"\n[Tool Output]\n{block.get('content', '')}"
            conversation += f"{role_label}: {content_text}\n\n"
            
        conversation += "Assistant: "

        # 2. Map target model name based on requested model
        model_name = request.model
        if "opus" in model_name.lower() or "sonnet" in model_name.lower() or "haiku" in model_name.lower() or "claude" in model_name.lower():
            model_name = "gateway-claude-opus-4-7"
        
        payload = {
            "message": conversation,
            "model": model_name
        }

        # 3. Stream from the unlimited-ai worker proxy
        req_id = request_id or f"msg_{uuid.uuid4()}"
        builder = SSEBuilder(message_id=req_id, model=request.model, input_tokens=input_tokens)
        
        yield builder.message_start()
        yield builder.start_text_block()

        try:
            async with self._client.stream(
                "POST",
                "https://unlimited-ai-proxy.sportsmoments97.workers.dev/api/chat",
                json=payload,
                headers={"Content-Type": "application/json"}
            ) as response:
                if response.status_code != 200:
                    err_msg = f"Worker proxy returned status {response.status_code}"
                    yield builder.emit_top_level_error(err_msg)
                    return

                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        json_str = line[6:].strip()
                        if not json_str:
                            continue
                        try:
                            data = json.loads(json_str)
                            if "delta" in data and isinstance(data["delta"], str):
                                yield builder.emit_text_delta(data["delta"])
                            elif "error" in data:
                                yield builder.emit_top_level_error(data["error"])
                        except Exception:
                            pass
        except Exception as e:
            yield builder.emit_top_level_error(str(e))
            
        yield builder.stop_text_block()
        yield builder.message_delta("end_turn", builder.estimate_output_tokens())
        yield builder.message_stop()
