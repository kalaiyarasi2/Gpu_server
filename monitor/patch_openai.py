def _downscale_messages_images(messages: list, max_dim: int = 2048):
    """Ensures base64 images do not exceed OpenAI's 30,000 patch limit by capping resolution at 2048px."""
    if not isinstance(messages, list):
        return
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "image_url":
                continue
            image_url = part.get("image_url")
            if not isinstance(image_url, dict):
                continue
            url = image_url.get("url", "")
            if isinstance(url, str) and url.startswith("data:image/") and ";base64," in url:
                try:
                    import base64, io
                    from PIL import Image
                    header, b64_str = url.split(";base64,", 1)
                    raw = base64.b64decode(b64_str)
                    img = Image.open(io.BytesIO(raw))
                    if max(img.size) > max_dim:
                        resample = getattr(Image, "Resampling", Image).LANCZOS
                        img.thumbnail((max_dim, max_dim), resample)
                        buf = io.BytesIO()
                        img.save(buf, format="PNG")
                        new_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                        image_url["url"] = f"data:image/png;base64,{new_b64}"
                except Exception:
                    pass

import time
import os
import logging
from typing import Any

logger = logging.getLogger("monitor_openai_patch")

def _sanitize_model_params(kwargs: dict) -> dict:
    _downscale_messages_images(kwargs.get('messages', []))
    """Sanitizes model arguments for newer OpenAI architectures (gpt-5..., o1, o3, etc.).
    These models reject legacy parameters like `temperature=0.0` or `max_tokens`.
    """
    try:
        model_str = str(kwargs.get("model", "")).lower()
        if any(m in model_str for m in ["gpt-5", "o1", "o3", "o4"]):
            if "max_tokens" in kwargs:
                if "max_completion_tokens" not in kwargs:
                    kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
                else:
                    kwargs.pop("max_tokens")
            if "temperature" in kwargs:
                temp = kwargs.get("temperature")
                if temp is not None and (temp == 0 or temp == 0.0 or temp != 1):
                    kwargs.pop("temperature", None)
            if "top_p" in kwargs:
                top_p = kwargs.get("top_p")
                if top_p is not None and top_p != 1:
                    kwargs.pop("top_p", None)
    except Exception:
        pass
    return kwargs

def patch_openai_for_monitoring():
    """Globally hooks into openai.resources.chat.completions.Completions.create
    to automatically record prompt tokens, completion tokens, model, latency, and cost
    for every AI call executed across all modules, without requiring manual instrumentation.
    Also auto-adapts legacy parameters (max_tokens, temperature) for newer OpenAI model families.
    """
    try:
        from openai.resources.chat.completions import Completions, AsyncCompletions
        if getattr(Completions, "_is_monitored_patched", False):
            return

        original_create = Completions.create

        def monitored_create(self, *args, **kwargs):
            _sanitize_model_params(kwargs)
            start_time = time.time()
            response = original_create(self, *args, **kwargs)
            elapsed = time.time() - start_time

            try:
                # Retrieve request_id from kwargs or global environment context
                request_id = kwargs.pop("_request_id", None) or os.environ.get("AI_MONITOR_REQUEST_ID")
                
                # Check if response has valid usage stats and request_id is available
                if request_id and response and hasattr(response, "usage") and response.usage:
                    # Prevent double recording if already recorded
                    if getattr(response, "_monitored_recorded", False):
                        return response
                    response._monitored_recorded = True

                    prompt_tokens = getattr(response.usage, "prompt_tokens", 0) or 0
                    completion_tokens = getattr(response.usage, "completion_tokens", 0) or 0
                    model = kwargs.get("model", "unknown")

                    from .service import request_monitor
                    request_monitor.record_ai_usage(
                        request_id=request_id,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        processing_time=elapsed,
                        model=model
                    )
            except Exception as e:
                logger.debug(f"Failed to auto-record OpenAI call: {e}")

            return response

        Completions.create = monitored_create
        Completions._is_monitored_patched = True

        if hasattr(AsyncCompletions, "create"):
            original_async_create = AsyncCompletions.create

            async def monitored_async_create(self, *args, **kwargs):
                _sanitize_model_params(kwargs)
                start_time = time.time()
                response = await original_async_create(self, *args, **kwargs)
                elapsed = time.time() - start_time
                try:
                    request_id = kwargs.pop("_request_id", None) or os.environ.get("AI_MONITOR_REQUEST_ID")
                    if request_id and response and hasattr(response, "usage") and response.usage:
                        if getattr(response, "_monitored_recorded", False):
                            return response
                        response._monitored_recorded = True
                        prompt_tokens = getattr(response.usage, "prompt_tokens", 0) or 0
                        completion_tokens = getattr(response.usage, "completion_tokens", 0) or 0
                        model = kwargs.get("model", "unknown")
                        from .service import request_monitor
                        request_monitor.record_ai_usage(
                            request_id=request_id,
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            processing_time=elapsed,
                            model=model
                        )
                except Exception as e:
                    logger.debug(f"Failed to auto-record async OpenAI call: {e}")
                return response

            AsyncCompletions.create = monitored_async_create

        logger.info("OpenAI chat completions automatically patched for real-time monitoring and model compatibility.")
    except Exception as e:
        logger.warning(f"Could not automatically patch OpenAI chat completions: {e}")
