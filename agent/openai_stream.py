"""Bridge the existing HTTP integration to LangGraph's native messages channel.

No extra SDK or model call: BaseChatModel publishes provider chunks through its
callbacks. https://docs.langchain.com/oss/python/langgraph/streaming#llm-tokens
"""
import json

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field, SecretStr

URL = "https://api.openai.com/v1/chat/completions"


class OpenAIStreamModel(BaseChatModel):
    model: str
    api_key: SecretStr = Field(exclude=True, repr=False)

    @property
    def _llm_type(self):
        return "openai-http-stream"

    @property
    def _identifying_params(self):
        return {"model": self.model}

    def _get_ls_params(self, stop=None, **kwargs):
        return {**super()._get_ls_params(stop=stop, **kwargs), "ls_provider": "openai"}

    def _request(self, messages, stop):
        roles = {"human": "user", "ai": "assistant", "system": "system"}
        payload = {"model": self.model, "temperature": 0,
                   "messages": [{"role": roles[m.type], "content": m.content} for m in messages]}
        if stop:
            payload["stop"] = stop
        headers = {"Authorization": "Bearer " + self.api_key.get_secret_value()}
        return payload, headers

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        payload, headers = self._request(messages, stop)
        response = httpx.post(URL, json=payload, headers=headers, timeout=120)
        response.raise_for_status()
        data = response.json()
        return ChatResult(generations=[ChatGeneration(message=AIMessage(
            content=data["choices"][0]["message"]["content"]))], llm_output={"token_usage": data.get("usage", {})})

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        payload, headers = self._request(messages, stop)
        payload.update(stream=True, stream_options={"include_usage": True})
        finished = False
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", URL, json=payload, headers=headers) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        finished = True
                        break
                    data = json.loads(raw)
                    if data.get("error"):
                        raise RuntimeError("openai_stream_error")
                    choices = data.get("choices", [])
                    text = choices[0].get("delta", {}).get("content") if choices else None
                    if text:
                        yield ChatGenerationChunk(message=AIMessageChunk(content=text))
                    usage = data.get("usage")
                    if usage:
                        yield ChatGenerationChunk(message=AIMessageChunk(content="",
                            response_metadata={"model_name": data.get("model")},
                            usage_metadata={"input_tokens": usage["prompt_tokens"],
                                            "output_tokens": usage["completion_tokens"],
                                            "total_tokens": usage["total_tokens"]}))
        if not finished:
            raise RuntimeError("openai_stream_incomplete")
