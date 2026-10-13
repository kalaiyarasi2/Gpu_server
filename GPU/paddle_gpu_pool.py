"""
Persistent, crash-safe, parallel PaddleOCR GPU worker pool.

Why this exists
---------------
The original enhancers launched a *fresh Python subprocess for every page*.
Each subprocess re-imported Paddle and re-loaded the detection / angle /
recognition models, so most of the wall-clock time was CPU-bound model loading
and the GPU sat idle.

This pool instead keeps N long-lived worker processes alive. Each worker:
  * pins itself to one GPU (round-robin across GPU_OCR_DEVICES),
  * loads each PaddleOCR engine profile onto the GPU ONCE and caches it,
  * OCRs whole pages that are dispatched to it in parallel with other workers.

Crash safety is preserved: PaddleOCR runs in child processes. If a worker dies
(e.g. Paddle calls sys.exit on OOM) the pool is rebuilt and the affected pages
are retried; pages that still fail are returned as ``None`` so the caller can
fall back to the legacy CPU path.

The pool is a process-wide singleton and is thread-safe, so several FastAPI
requests can share the same GPU workers concurrently.
"""
from __future__ import annotations

import atexit
import json
import math
import multiprocessing as mp
import os
import threading
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from concurrent.futures.process import BrokenProcessPool
from typing import Any, Dict, List, Optional, Sequence

from .gpu_config import GPUConfig, get_gpu_config

# ─────────────────────────────────────────────────────────────────────────────
#  CHILD-PROCESS SIDE  (runs inside each GPU worker)
# ─────────────────────────────────────────────────────────────────────────────
_ENGINES: Dict[str, Any] = {}
_WORKER_SETTINGS: Dict[str, Any] = {}


def _worker_init(devices: List[int], gpu_mem_mb: int, rec_batch_num: int, cpu_threads: int) -> None:
    """Runs once per worker process BEFORE paddle is imported."""
    device = devices[os.getpid() % len(devices)] if devices else 0
    os.environ["CUDA_VISIBLE_DEVICES"] = str(device)
    # Grow GPU memory on demand instead of grabbing a big fixed chunk, so
    # several workers can share one card safely.
    os.environ.setdefault("FLAGS_allocator_strategy", "auto_growth")
    os.environ.setdefault("FLAGS_fraction_of_gpu_memory_to_use", "0")
    # Keep CPU-side pre/post-processing from oversubscribing the cores.
    os.environ["OMP_NUM_THREADS"] = str(cpu_threads)
    os.environ["MKL_NUM_THREADS"] = str(cpu_threads)
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    _WORKER_SETTINGS.update(
        device=device, gpu_mem_mb=gpu_mem_mb, rec_batch_num=rec_batch_num, cpu_threads=cpu_threads
    )


def _get_engine(profile: Dict[str, Any]):
    key = json.dumps(profile, sort_keys=True)
    engine = _ENGINES.get(key)
    if engine is not None:
        return engine

    # Clear previous engine to prevent VRAM accumulation
    if _ENGINES:
        _ENGINES.clear()
        import gc
        gc.collect()
        try:
            import paddle
            if paddle.device.is_compiled_with_cuda():
                paddle.device.cuda.empty_cache()
        except Exception:
            pass

    from paddleocr import PaddleOCR, PPStructure  # heavy import only in workers

    kind = profile.get("kind", "basic")
    kwargs = dict(profile.get("kwargs") or {})
    kwargs.setdefault("lang", "en")
    kwargs.setdefault("show_log", False)
    kwargs.setdefault("gpu_mem", _WORKER_SETTINGS.get("gpu_mem_mb", 500))
    kwargs.setdefault("cpu_threads", _WORKER_SETTINGS.get("cpu_threads", 2))
    kwargs["use_gpu"] = True
    kwargs["gpu_id"] = 0  # CUDA_VISIBLE_DEVICES already remaps the chosen card to 0

    if kind == "structure":
        kwargs.setdefault("table", True)
        kwargs.setdefault("ocr", True)
        kwargs.setdefault("layout", True)
        engine = PPStructure(**kwargs)
    else:
        kwargs.setdefault("rec_batch_num", _WORKER_SETTINGS.get("rec_batch_num", 16))
        engine = PaddleOCR(**kwargs)

    _ENGINES[key] = engine
    return engine


def _to_plain(obj: Any) -> Any:
    """Make Paddle results picklable/small: numpy -> python, drop page crops."""
    try:
        import numpy as np
    except Exception:  # pragma: no cover
        np = None
    if np is not None:
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, np.generic):
            return obj.item()
    if isinstance(obj, dict):
        return {k: _to_plain(v) for k, v in obj.items() if k != "img"}
    if isinstance(obj, (list, tuple)):
        return [_to_plain(v) for v in obj]
    return obj


def _worker_ocr_page(img_path: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    """OCR one page image. Returns raw Paddle output (formatting is done by the caller)."""
    t0 = time.perf_counter()
    try:
        import numpy as np
        from PIL import Image

        with Image.open(img_path) as im:
            img_array = np.array(im.convert("RGB"))

        engine = _get_engine(profile)
        if profile.get("kind") == "structure":
            raw = engine(img_array)
        else:
            raw = engine.ocr(img_array, cls=bool(profile.get("cls", True)))

        return {
            "ok": True,
            "raw": _to_plain(raw),
            "elapsed": round(time.perf_counter() - t0, 3),
            "pid": os.getpid(),
            "device": _WORKER_SETTINGS.get("device"),
        }
    except Exception as exc:  # returned, not raised, so one bad page never breaks the batch
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "pid": os.getpid()}


def _worker_ping(profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Warm-up: optionally pre-load an engine profile."""
    if profile:
        _get_engine(profile)
    return {"pid": os.getpid(), "device": _WORKER_SETTINGS.get("device"), "engines": len(_ENGINES)}


# ─────────────────────────────────────────────────────────────────────────────
#  PARENT-PROCESS SIDE  (pool manager, used by the extractors)
# ─────────────────────────────────────────────────────────────────────────────
class PaddleGPUPool:
    def __init__(self, config: Optional[GPUConfig] = None):
        self.config = config or get_gpu_config()
        self._executor: Optional[ProcessPoolExecutor] = None
        self._generation = 0
        self._lock = threading.RLock()

    # ── lifecycle ────────────────────────────────────────────────────────────
    def _create(self) -> ProcessPoolExecutor:
        cfg = self.config
        kwargs = dict(
            max_workers=cfg.workers,
            mp_context=mp.get_context("spawn"),  # CUDA is not fork-safe
            initializer=_worker_init,
            initargs=(cfg.devices, cfg.gpu_mem_mb, cfg.rec_batch_num, cfg.cpu_threads),
        )
        if cfg.max_tasks_per_child > 0:
            kwargs["max_tasks_per_child"] = cfg.max_tasks_per_child
        print(f"[GPU-OCR] Starting pool: {cfg.summary()}")
        return ProcessPoolExecutor(**kwargs)

    def _get(self):
        with self._lock:
            if self._executor is None:
                self._executor = self._create()
                self._generation += 1
            return self._executor, self._generation

    def _reset(self, generation: int, reason: str) -> None:
        """Kill and drop the executor (only if nobody else already replaced it)."""
        with self._lock:
            if self._executor is None or generation != self._generation:
                return
            print(f"[GPU-OCR] Resetting worker pool ({reason})")
            ex = self._executor
            self._executor = None
            try:
                ex.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
            procs = list((getattr(ex, "_processes", None) or {}).values())
            for p in procs:
                try:
                    p.kill()
                except Exception:
                    pass

    def shutdown(self) -> None:
        with self._lock:
            if self._executor is not None:
                try:
                    self._executor.shutdown(wait=False, cancel_futures=True)
                except Exception:
                    pass
                self._executor = None

    def warmup(self, profile: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Load models in every worker up-front (avoids first-request latency)."""
        ex, _ = self._get()
        futs = [ex.submit(_worker_ping, profile) for _ in range(self.config.workers)]
        return [f.result(timeout=600) for f in futs]

    # ── main API ─────────────────────────────────────────────────────────────
    def ocr_images(
        self,
        image_paths: Sequence[str],
        profile: Dict[str, Any],
        retries: int = 1,
        on_page_done=None,
    ) -> List[Optional[Dict[str, Any]]]:
        """
        OCR all images in parallel across the GPU workers.

        Returns a list aligned with ``image_paths``; each item is the worker
        result dict (``{"ok": True, "raw": ...}``) or ``None`` if the page
        could not be processed on the GPU (caller should fall back).
        """
        n = len(image_paths)
        results: List[Optional[Dict[str, Any]]] = [None] * n
        pending = list(range(n))
        attempt = 0

        while pending and attempt <= retries:
            attempt += 1
            ex, gen = self._get()
            futures = {}
            try:
                for i in pending:
                    futures[ex.submit(_worker_ocr_page, str(image_paths[i]), profile)] = i
            except (BrokenProcessPool, RuntimeError) as exc:
                self._reset(gen, f"submit failed: {exc}")
                continue

            # Overall budget: pages are spread over the workers.
            waves = math.ceil(len(pending) / max(1, self.config.workers))
            budget = self.config.page_timeout * max(1, waves) + 120
            failed: List[int] = []
            try:
                for fut in as_completed(futures, timeout=budget):
                    i = futures[fut]
                    try:
                        res = fut.result()
                    except BrokenProcessPool:
                        failed.append(i)
                        continue
                    except Exception as exc:
                        res = {"ok": False, "error": str(exc)}
                    if res.get("ok"):
                        results[i] = res
                        if on_page_done:
                            try:
                                on_page_done(i, res)
                            except Exception:
                                pass
                    else:
                        print(f"[GPU-OCR] Page {i + 1} failed on GPU: {res.get('error')}")
                        # A Python-level OCR error is deterministic; don't retry it on GPU.
            except FuturesTimeout:
                failed.extend(i for f, i in futures.items() if not f.done())
                self._reset(gen, f"timeout after {budget}s")

            if any(isinstance(f.exception(), BrokenProcessPool) for f in futures if f.done() and not f.cancelled()):
                self._reset(gen, "a worker process crashed (OOM / sys.exit)")

            pending = sorted(set(failed))

        if pending:
            print(f"[GPU-OCR] {len(pending)} page(s) could not be processed on GPU: "
                  f"{[p + 1 for p in pending]}")
        return results


# ─────────────────────────────────────────────────────────────────────────────
#  SINGLETON HELPERS
# ─────────────────────────────────────────────────────────────────────────────
_POOL: Optional[PaddleGPUPool] = None
_POOL_LOCK = threading.Lock()


def get_pool() -> Optional[PaddleGPUPool]:
    """Return the shared pool, or None if GPU OCR is unavailable/disabled."""
    global _POOL
    cfg = get_gpu_config()
    if not cfg.enabled:
        return None
    with _POOL_LOCK:
        if _POOL is None or _POOL.config != cfg:
            if _POOL is not None:
                _POOL.shutdown()
            _POOL = PaddleGPUPool(cfg)
        return _POOL


def shutdown_pool() -> None:
    global _POOL
    with _POOL_LOCK:
        if _POOL is not None:
            _POOL.shutdown()
            _POOL = None


atexit.register(shutdown_pool)
