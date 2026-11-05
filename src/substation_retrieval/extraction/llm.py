"""LLM client for the extraction passes and chat (vision + strict JSON).

Providers
  openai     OpenAI-compatible endpoint (Responses API, or Chat Completions with
             llm.api_style=chat for gateways). Key: OPENAI_API_KEY / ASTRA_API_KEY.
  anthropic  optional (llm.provider=anthropic). Key: ANTHROPIC_API_KEY.
Responses are cached on disk by (document sha, prompt version, pass, key) so re-running an
extraction or rebuilding the index never pays twice. Keys are read from .env / environment only.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
from pathlib import Path

log = logging.getLogger(__name__)


OPENAI_KEYS = ("ASTRA_API_KEY", "OPENAI_API_KEY", "astra_api_key", "openai_api_key", "AZURE_OPENAI_API_KEY")


class LLMUnavailable(RuntimeError):
    pass


def _jpeg_b64(img_rgb, max_side: int = 2000, quality: int = 85) -> str:
    import cv2
    h, w = img_rgb.shape[:2]
    s = max_side / max(h, w)
    if s < 1:
        img_rgb = cv2.resize(img_rgb, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img_rgb[:, :, ::-1], [cv2.IMWRITE_JPEG_QUALITY, quality])
    return base64.b64encode(buf.tobytes()).decode()


def parse_json(text: str) -> dict:
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j < 0:
        raise ValueError("no JSON object in LLM output")
    return json.loads(text[i:j + 1])


class LLMClient:
    def __init__(self, settings, cache_dir: Path | None = None):
        self.s = settings
        self.provider = str(settings.get("llm.provider", "openai")).lower()
        self.model = settings.get("llm.model")
        self.style = settings.get("llm.api_style", "responses")
        self.timeout = float(settings.get("llm.timeout_s", 240))
        self.max_tokens = int(settings.get("llm.max_output_tokens", 12000))
        self.cache_dir = cache_dir
        self._client = None

    @property
    def available(self) -> bool:
        if self.provider == "none":
            return False
        if self.provider == "anthropic":
            return bool(self.s.secret("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "claude_api_key", "anthropic_api_key"))
        return bool(self.s.secret(*OPENAI_KEYS))

    def cached(self, sha: str) -> bool:
        """True if the on-disk cache holds replies for this document (cache keys start with its sha256)."""
        return bool(self.cache_dir and sha and self.cache_dir.is_dir() and any(self.cache_dir.glob(f"{sha}_*.json")))

    def describe(self) -> dict:
        return {"provider": self.provider, "model": self.model if self.provider != "anthropic" else self.s.get("llm.anthropic_model"),
                "available": self.available, "api_style": self.style}

    def _openai(self):
        if self._client is None:
            from openai import OpenAI
            key = self.s.secret(*OPENAI_KEYS)
            if not key:
                raise LLMUnavailable("no OpenAI-compatible API key in .env / environment")
            kw = {"api_key": key, "timeout": self.timeout}
            if self.s.get("llm.base_url"):
                kw["base_url"] = self.s.get("llm.base_url")
            self._client = OpenAI(**kw)
        return self._client

    def _anthropic(self):
        if self._client is None:
            import anthropic
            key = self.s.secret("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "claude_api_key", "anthropic_api_key")
            if not key:
                raise LLMUnavailable("no Anthropic API key in .env / environment")
            self._client = anthropic.Anthropic(api_key=key, timeout=self.timeout)
        return self._client

    # ------------------------------------------------------------ core call
    def complete(self, system: str, user: str, images: list | None = None, json_mode: bool = True,
                 cache_key: str | None = None, max_tokens: int | None = None) -> dict | str:
        if cache_key and self.cache_dir:
            p = self.cache_dir / (re.sub(r"[^A-Za-z0-9_.-]", "_", cache_key) + ".json")
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))["output"]
        if not self.available:
            raise LLMUnavailable(f"LLM provider '{self.provider}' has no key configured")
        imgs = [_jpeg_b64(im) if not isinstance(im, str) else im for im in (images or [])]
        t0 = time.time()
        text = self._call(system, user, imgs, json_mode, max_tokens or self.max_tokens)
        out = parse_json(text) if json_mode else text
        log.info("llm %s %.1fs", cache_key or "", time.time() - t0)
        if cache_key and self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"model": self.model, "output": out}, ensure_ascii=False), encoding="utf-8")
        return out

    def _call(self, system, user, imgs, json_mode, max_tokens) -> str:
        if self.provider == "anthropic":
            c = self._anthropic()
            content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b}} for b in imgs]
            content.append({"type": "text", "text": user + ("\nReturn only one JSON object." if json_mode else "")})
            r = c.messages.create(model=self.s.get("llm.anthropic_model"), max_tokens=max_tokens, system=system,
                                  messages=[{"role": "user", "content": content}])
            return "".join(getattr(b, "text", "") for b in r.content)
        c = self._openai()
        if self.style == "chat":
            content = [{"type": "text", "text": user}] + [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b}"}} for b in imgs]
            kw = {"model": self.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
                  "max_completion_tokens": max_tokens}
            if json_mode:
                kw["response_format"] = {"type": "json_object"}
            r = c.chat.completions.create(**kw)
            return r.choices[0].message.content or ""
        content = [{"type": "input_text", "text": user}] + [{"type": "input_image", "image_url": f"data:image/jpeg;base64,{b}"} for b in imgs]
        kw = {"model": self.model, "instructions": system, "input": [{"role": "user", "content": content}],
              "max_output_tokens": max_tokens}
        if json_mode:
            kw["text"] = {"format": {"type": "json_object"}}
        r = c.responses.create(**kw)
        return getattr(r, "output_text", "") or ""

    def chat(self, system: str, messages: list[dict], max_tokens: int = 1500) -> str:
        """Plain multi-turn chat (no images) for the assistant."""
        if not self.available:
            raise LLMUnavailable("no LLM key configured")
        if self.provider == "anthropic":
            r = self._anthropic().messages.create(model=self.s.get("llm.anthropic_model"), max_tokens=max_tokens, system=system,
                                                  messages=messages)
            return "".join(getattr(b, "text", "") for b in r.content)
        c = self._openai()
        if self.style == "chat":
            r = c.chat.completions.create(model=self.model, messages=[{"role": "system", "content": system}] + messages,
                                          max_completion_tokens=max_tokens)
            return r.choices[0].message.content or ""
        r = c.responses.create(model=self.model, instructions=system, input=messages, max_output_tokens=max_tokens)
        return getattr(r, "output_text", "") or ""

    def embed(self, texts: list[str]) -> list[list[float]]:
        c = self._openai()
        r = c.embeddings.create(model=self.s.get("engines.rag.embedding_model", "text-embedding-3-small"), input=texts)
        return [d.embedding for d in r.data]
