import time
import os
import logging
from typing import Any

logger = logging.getLogger("monitor_openai_patch")

def patch_openai_for_monitoring():
    """Globally hooks into openai.resources.chat.completions.Completions.create
    to automatically record prompt tokens, completion tokens, model, latency, and cost
    for every AI call executed across all modules, without requiring manual instrumentation.
    """
    try:
        from openai.resources.chat.completions import Completions
        if getattr(Completions, "_is_monitored_patched", False):
            return

        original_create = Completions.create

        def monitored_create(self, *args, **kwargs):
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
        logger.info("OpenAI chat completions automatically patched for real-time monitoring.")
    except Exception as e:
        logger.warning(f"Could not automatically patch OpenAI chat completions: {e}")
