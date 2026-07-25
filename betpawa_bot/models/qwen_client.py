"""Client for Qwen2.5-VL-3B on model server."""
import httpx
import json
from config import MODEL_SERVER, MODEL_NAME

async def query(prompt, temperature=0.3, max_tokens=200):
    """Send text prompt to Qwen and get response."""
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{MODEL_SERVER}/v1/chat/completions", json={
            "model": MODEL_NAME,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        })
        return r.json()["choices"][0]["message"]["content"]

async def analyze_screenshot(image_base64, instruction):
    """Send a screenshot + instruction to Qwen vision model."""
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{MODEL_SERVER}/v1/chat/completions", json={
            "model": MODEL_NAME,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}},
                    {"type": "text", "text": instruction}
                ]
            }],
            "temperature": 0.1,
            "max_tokens": 500,
        })
        return r.json()["choices"][0]["message"]["content"]
