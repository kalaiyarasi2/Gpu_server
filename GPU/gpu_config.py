"""
GPU configuration for the shared PaddleOCR GPU worker pool.

Detection is done WITHOUT creating a CUDA context in the calling (server)
process: we use `nvidia-smi` for device/VRAM info and only ask Paddle whether
it was compiled with CUDA (which does not touch the GPU).

Environment overrides (all optional):
    GPU_OCR_ENABLED          "1" (default) / "0"  -> force-disable the GPU pool
    GPU_OCR_WORKERS          int  -> number of parallel GPU worker processes
    GPU_OCR_DEVICES          "0" or "0,1" -> GPU ids to spread workers across
    GPU_OCR_VRAM_PER_WORKER  float GB used for auto-sizing (default 4.0)
    GPU_OCR_GPU_MEM_MB       int  -> initial Paddle GPU memory per engine (default 500)
    GPU_OCR_REC_BATCH        int  -> recognition batch size on GPU (default 16)
    GPU_OCR_CPU_THREADS      int  -> CPU threads per worker for pre/post-processing (default 2)
    GPU_OCR_RENDER_THREADS   int  -> pdf2image rasterisation threads (default min(4, cores//2))
    GPU_OCR_PAGE_TIMEOUT     int  -> seconds per page before the pool is reset (default 300)
    GPU_OCR_MAX_TASKS_PER_CHILD int -> recycle a worker after N pages (default 0 = never)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from typing import List


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _query_nvidia_smi() -> List[dict]:
    """Return [{'index': 0, 'name': 'Tesla T4', 'vram_gb': 15.0}, ...] or []."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run(
            [exe, "--query-gpu=index,name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except Exception:
        return []
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            try:
                gpus.append({"index": int(parts[0]), "name": parts[1],
                             "vram_gb": round(float(parts[2]) / 1024.0, 1)})
            except ValueError:
                continue
    return gpus


def _paddle_has_cuda() -> bool:
    # If torch is already loaded in this process, importing paddle on Windows causes a fatal
    # pybind11 collision ("generic_type: type '_gpuDeviceProperties' is already registered!").
    # In that case, safely inspect installed distribution metadata.
    if "torch" in sys.modules:
        try:
            import importlib.metadata
            installed = {d.metadata["Name"].lower() for d in importlib.metadata.distributions()}
            return "paddlepaddle-gpu" in installed
        except Exception:
            return False
    try:
        import paddle  # noqa: WPS433 (import inside function on purpose)
        return bool(paddle.device.is_compiled_with_cuda())
    except Exception:
        return False


@dataclass
class GPUConfig:
    enabled: bool
    reason: str
    devices: List[int] = field(default_factory=list)
    device_names: List[str] = field(default_factory=list)
    vram_gb: float = 0.0              # smallest VRAM among selected devices
    workers: int = 0
    gpu_mem_mb: int = 500
    rec_batch_num: int = 16
    cpu_threads: int = 2
    render_threads: int = 2
    page_timeout: int = 300
    max_tasks_per_child: int = 0

    def summary(self) -> str:
        if not self.enabled:
            return f"GPU OCR disabled ({self.reason})"
        names = ", ".join(f"{i}:{n}" for i, n in zip(self.devices, self.device_names))
        return (f"GPU OCR enabled on [{names}] | VRAM {self.vram_gb} GB | "
                f"{self.workers} parallel workers | rec_batch={self.rec_batch_num} | "
                f"render_threads={self.render_threads}")


@lru_cache(maxsize=1)
def get_gpu_config() -> GPUConfig:
    cores = os.cpu_count() or 4
    render_threads = max(1, _env_int("GPU_OCR_RENDER_THREADS", min(4, max(1, cores // 2))))
    common = dict(
        gpu_mem_mb=_env_int("GPU_OCR_GPU_MEM_MB", 500),
        rec_batch_num=_env_int("GPU_OCR_REC_BATCH", 16),
        cpu_threads=max(1, _env_int("GPU_OCR_CPU_THREADS", 2)),
        render_threads=render_threads,
        page_timeout=max(30, _env_int("GPU_OCR_PAGE_TIMEOUT", 300)),
        max_tasks_per_child=max(0, _env_int("GPU_OCR_MAX_TASKS_PER_CHILD", 0)),
    )

    if os.environ.get("GPU_OCR_ENABLED", "1").strip().lower() in ("0", "false", "no", "off"):
        return GPUConfig(enabled=False, reason="GPU_OCR_ENABLED=0", **common)

    gpus = _query_nvidia_smi()
    if not gpus:
        return GPUConfig(enabled=False, reason="no NVIDIA GPU detected (nvidia-smi)", **common)

    if not _paddle_has_cuda():
        return GPUConfig(enabled=False,
                         reason="installed paddle is CPU-only (install paddlepaddle-gpu)", **common)

    wanted = os.environ.get("GPU_OCR_DEVICES", "").strip()
    if wanted:
        ids = {int(x) for x in wanted.split(",") if x.strip().isdigit()}
        gpus = [g for g in gpus if g["index"] in ids] or gpus

    vram = min(g["vram_gb"] for g in gpus)
    per_worker = max(1.0, _env_float("GPU_OCR_VRAM_PER_WORKER", 8.5))
    # Leave ~1 GB headroom per GPU for the driver / other models.
    auto_per_gpu = max(1, int((vram - 1.0) // per_worker))
    auto_workers = auto_per_gpu * len(gpus)
    # Each worker also burns CPU for pre/post-processing -> cap by cores.
    auto_workers = max(1, min(auto_workers, max(1, cores // common["cpu_threads"])))
    workers = max(1, _env_int("GPU_OCR_WORKERS", auto_workers))

    return GPUConfig(
        enabled=True,
        reason="ok",
        devices=[g["index"] for g in gpus],
        device_names=[g["name"] for g in gpus],
        vram_gb=vram,
        workers=workers,
        **common,
    )
