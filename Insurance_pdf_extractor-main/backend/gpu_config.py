"""
GPU Configuration & Concurrency Manager
Provides gpu_manager and gpu_concurrency_config for the Insurance PDF Extractor.

On machines without a CUDA-capable GPU (or without PyTorch installed) this
module falls back gracefully to CPU mode, which is identical in behaviour but
uses a smaller ThreadPool to avoid overwhelming the system.
"""

import os


# ── Hardware detection ────────────────────────────────────────────────────────

def _detect_mode():
    """Return 'GPU' if a CUDA device is available, else 'CPU'."""
    try:
        import torch
        if torch.cuda.is_available():
            vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
            return "GPU", vram_gb
    except ImportError:
        pass
    return "CPU", 0.0


_MODE, _VRAM_GB = _detect_mode()


# ── Concurrency config ────────────────────────────────────────────────────────
#
# Worker counts are intentionally conservative so the OCR pipeline does not
# starve other processes running on the same machine.  Increase max_workers
# if you have a GPU with ≥ 8 GB VRAM.

def _build_concurrency_config(mode: str, vram_gb: float) -> dict:
    if mode == "GPU" and vram_gb >= 8:
        max_workers = 4
    elif mode == "GPU":
        max_workers = 2
    else:
        # CPU fallback – use half the logical cores, minimum 1
        import multiprocessing
        max_workers = max(1, multiprocessing.cpu_count() // 2)

    return {
        "mode": mode,
        "vram_gb": round(vram_gb, 1),
        "rostaing_ocr": {
            "max_workers": max_workers,
        },
        "tesseract": {
            "max_workers": max_workers,
        },
    }


gpu_concurrency_config = _build_concurrency_config(_MODE, _VRAM_GB)


# ── GPU Manager ───────────────────────────────────────────────────────────────

class _GPUManager:
    """
    Thin wrapper that executes rostaing-ocr (or any callable) with optional
    GPU VRAM protection.  On CPU-only machines it simply calls the function
    directly without any torch context.
    """

    def __init__(self, mode: str):
        self.mode = mode

    def execute_with_rostaing(self, pdf_path: str, fn):
        """
        Execute *fn(pdf_path)* with GPU VRAM protection when available.

        Args:
            pdf_path: Path to the PDF file (passed verbatim to *fn*).
            fn:       Callable that accepts a single str argument (the PDF path)
                      and returns extracted text.

        Returns:
            str: Extracted text returned by *fn*.
        """
        if self.mode == "GPU":
            try:
                import torch
                with torch.cuda.device(0):
                    result = fn(pdf_path)
                # Free the CUDA cache after each document to avoid OOM on
                # long batch runs.
                torch.cuda.empty_cache()
                return result
            except Exception:
                # Any GPU error → fall through to plain CPU execution
                pass

        # CPU path (also used as GPU fallback)
        return fn(pdf_path)

    def __repr__(self):
        return f"<GPUManager mode={self.mode} vram={_VRAM_GB:.1f}GB>"


gpu_manager = _GPUManager(_MODE)

# ── Startup log ───────────────────────────────────────────────────────────────
print(
    f"[gpu_config] Hardware mode: {_MODE}"
    + (f" ({_VRAM_GB:.1f} GB VRAM)" if _MODE == "GPU" else " (no GPU detected)")
    + f" | OCR workers: {gpu_concurrency_config['rostaing_ocr']['max_workers']}"
)
