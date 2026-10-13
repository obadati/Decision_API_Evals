"""Offline transport exercises the real adapters with synthetic vendor JSON."""

from __future__ import annotations

import asyncio
import json

import httpx


def demo_transport(provider: str) -> httpx.MockTransport:
    async def handle(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        await asyncio.sleep(0.01)
        if provider == "openai":
            choices = data["questions"][0]["choices"]
            answer = {"type": "choice", "name": "decision", "choice": choices[0]["value"]}
            answers = [answer]
        else:
            criteria = data["questions"]["decision"]["criteria"]
            answer = {"type": "choice", "choice": next(iter(criteria))}
            answers = {"decision": answer}
        return httpx.Response(
            200,
            json={
                "model": data["model"],
                "answers": answers,
                "usage": {"input_tokens": 0, "output_tokens": 0},
                "synthetic": True,
            },
        )

    return httpx.MockTransport(handle)
