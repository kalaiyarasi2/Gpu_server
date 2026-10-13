# GPU — Shared parallel PaddleOCR on GPU

Shared GPU OCR layer used by both POCs:

| Project                                 | Connected file                                                 |
| --------------------------------------- | -------------------------------------------------------------- |
| Insurance                               | `Insurance_pdf_extractor-main/backend/paddleocr_enhancer.py` |
| Workers Comp                            | `0work_compenstaion/backend/paddleocr_enhancer.py`           |
| Unified router (classification snippet) | uses the Insurance enhancer → GPU                             |

## How it works

```
PDF ──pdf2image (multi-threaded)──► page PNGs
                                     │
                     ┌───────────────┼───────────────┐   persistent worker processes
                     ▼               ▼               ▼   (spawned once, models loaded
                 GPU worker 1    GPU worker 2    GPU worker N   onto the GPU once)
                     │               │               │
                     └──────── raw Paddle results ───┘
                                     │
                 each project's OWN formatter (_format_basic_result / _format_structure_result)
                                     ▼
                         same text layout as before
```

* **Before:** one new Python process *per page*, re-importing Paddle and reloading
  det/cls/rec models every time → CPU-bound, GPU idle.
* **Now:** N long-lived GPU workers, models stay resident, pages of a PDF are
  OCR'd **in parallel**, and concurrent API requests share the same pool.
* **Crash-safe:** if a worker dies (OOM / `sys.exit`) the pool is rebuilt and the
  pages are retried; pages that still fail fall back to the original CPU
  subprocess path automatically. No GPU / CPU-only Paddle → original path.

## Files

| File                   | Purpose                                                                                   |
| ---------------------- | ----------------------------------------------------------------------------------------- |
| `gpu_config.py`      | GPU detection (nvidia-smi + paddle CUDA build check), auto worker sizing, env overrides   |
| `paddle_gpu_pool.py` | Persistent, thread-safe, crash-safe multi-process GPU worker pool                         |
| `__init__.py`        | Public API:`gpu_ocr_available`, `ocr_images_parallel`, `warmup`, `render_threads` |
| `benchmark.py`       | Verify GPU usage, cold/warm timing, CPU vs GPU parity                                     |

## Tuning (environment variables)

| Var                             | Default              | Meaning                                                |
| ------------------------------- | -------------------- | ------------------------------------------------------ |
| `GPU_OCR_ENABLED`             | `1`                | `0` forces the legacy CPU path                       |
| `GPU_OCR_WORKERS`             | auto (T4 15 GB → 3) | parallel GPU worker processes                          |
| `GPU_OCR_VRAM_PER_WORKER`     | `4.0`              | GB budget per worker used for auto-sizing              |
| `GPU_OCR_DEVICES`             | all                  | e.g.`0,1` to spread workers over GPUs                |
| `GPU_OCR_REC_BATCH`           | `16`               | recognition batch size                                 |
| `GPU_OCR_GPU_MEM_MB`          | `500`              | initial Paddle GPU memory per engine (grows on demand) |
| `GPU_OCR_CPU_THREADS`         | `2`                | CPU threads per worker (pre/post-processing)           |
| `GPU_OCR_RENDER_THREADS`      | `min(4, cores/2)`  | pdf2image rasterisation threads                        |
| `GPU_OCR_PAGE_TIMEOUT`        | `300`              | seconds per page before the pool is reset              |
| `GPU_OCR_MAX_TASKS_PER_CHILD` | `0`                | recycle a worker after N pages (0 = never)             |

> Each server **process** owns its own pool. If you run several uvicorn
> processes/workers on the same GPU, lower `GPU_OCR_WORKERS` accordingly.

## Requirements

`paddlepaddle-gpu==2.6.2` (CUDA 11.8 / cuDNN 8.9) and `paddleocr==2.8.1` in the
venv that runs the server (already pinned in the root `requirements.txt`).

## Verify

```powershell
venv\Scripts\python.exe -m GPU.benchmark "<some.pdf>" --project both --compare-cpu
```
