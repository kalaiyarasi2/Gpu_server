"""
Shared GPU-accelerated PaddleOCR package for the PDF extractor POCs.

Used by:
  * Insurance_pdf_extractor-main/backend/paddleocr_enhancer.py
  * work_compenstaion/backend/paddleocr_enhancer.py

Public API
----------
    gpu_ocr_available()            -> bool
    get_gpu_config()               -> GPUConfig
    render_threads()               -> int  (threads to pass to pdf2image)
    ocr_images_parallel(paths, profile) -> list[raw_result | None]
    warmup(profile=None)           -> pre-load models in every GPU worker

A *profile* describes which Paddle engine to build inside the worker, e.g.:

    {"kind": "basic", "cls": True,
     "kwargs": {"use_angle_cls": True, "det_limit_side_len": 4000}}
    {"kind": "structure", "kwargs": {}}

The raw Paddle output is returned unchanged (numpy converted to lists), so each
project keeps using its OWN formatter -> identical text layout to the CPU path.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence

from .gpu_config import GPUConfig, get_gpu_config

__all__ = [
    "GPUConfig",
    "get_gpu_config",
    "gpu_ocr_available",
    "render_threads",
    "ocr_images_parallel",
    "warmup",
    "shutdown",
]


def gpu_ocr_available() -> bool:
    return get_gpu_config().enabled


def render_threads() -> int:
    return get_gpu_config().render_threads


def ocr_images_parallel(
    image_paths: Sequence[str],
    profile: Dict[str, Any],
    on_page_done: Optional[Callable[[int, Dict[str, Any]], None]] = None,
) -> Optional[List[Optional[Any]]]:
    """
    OCR page images in parallel on the GPU worker pool.

    Returns:
        None                      -> GPU OCR unavailable (caller uses CPU path)
        list aligned with input   -> raw Paddle result per page, or None for a
                                     page that failed on GPU (caller falls back
                                     for that page only)
    """
    from .paddle_gpu_pool import get_pool

    pool = get_pool()
    if pool is None:
        return None
    results = pool.ocr_images(image_paths, profile, on_page_done=on_page_done)
    return [r["raw"] if r else None for r in results]


def warmup(profile: Optional[Dict[str, Any]] = None):
    from .paddle_gpu_pool import get_pool

    pool = get_pool()
    return pool.warmup(profile) if pool else []


def shutdown() -> None:
    from .paddle_gpu_pool import shutdown_pool

    shutdown_pool()
