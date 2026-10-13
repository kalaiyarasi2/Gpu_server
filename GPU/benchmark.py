"""
Verify / benchmark the GPU PaddleOCR pool against both POC enhancers.

Usage (from pdf_extractor root, using the venv with paddlepaddle-gpu):
    venv\\Scripts\\python.exe -m GPU.benchmark <pdf> [--project insurance|wc|both] [--compare-cpu]

--compare-cpu also runs the legacy CPU subprocess path on the same PDF and
reports speed-up and text similarity.
"""
from __future__ import annotations

import argparse
import difflib
import importlib.util
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _setup_poppler() -> None:
    """Mirror the server: put POPPLER_PATH (env or root .env) on PATH for pdf2image."""
    poppler = os.environ.get("POPPLER_PATH")
    env_file = ROOT / ".env"
    if not poppler and env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().startswith("POPPLER_PATH="):
                poppler = line.split("=", 1)[1].strip().strip('"').strip("'")
    if poppler and os.path.isdir(poppler):
        os.environ["PATH"] = poppler + os.pathsep + os.environ.get("PATH", "")


_setup_poppler()

ENHANCERS = {
    "insurance": ROOT / "Insurance_pdf_extractor-main" / "backend" / "paddleocr_enhancer.py",
    "wc": ROOT / "work_compenstaion" / "backend" / "paddleocr_enhancer.py",
}


def _load(name: str):
    path = ENHANCERS[name]
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(f"_bench_{name}_enhancer", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(mod, pdf: str, use_gpu: bool):
    t0 = time.perf_counter()
    text, meta = mod.extract_with_paddleocr(pdf, use_gpu=use_gpu, enable_table=False)
    dt = time.perf_counter() - t0
    devices = [m.get("ocr_device", "?") for m in meta]
    return text, meta, dt, devices


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--project", choices=["insurance", "wc", "both"], default="both")
    ap.add_argument("--compare-cpu", action="store_true")
    args = ap.parse_args()

    import GPU

    cfg = GPU.get_gpu_config()
    print("=" * 80)
    print(cfg.summary())
    print("=" * 80)

    projects = ["insurance", "wc"] if args.project == "both" else [args.project]
    for name in projects:
        mod = _load(name)
        print(f"\n### {name.upper()} — GPU pool (cold: includes model load) ###")
        text, meta, dt, dev = _run(mod, args.pdf, use_gpu=True)
        print(f"-> {len(meta)} pages, {len(text)} chars, {dt:.1f}s, devices={dev}")

        print(f"\n### {name.upper()} — GPU pool (warm) ###")
        text_w, meta_w, dt_w, dev_w = _run(mod, args.pdf, use_gpu=True)
        print(f"-> {len(meta_w)} pages, {len(text_w)} chars, {dt_w:.1f}s, "
              f"{dt_w / max(1, len(meta_w)):.2f}s/page")

        if args.compare_cpu:
            GPU.shutdown()
            os.environ["GPU_OCR_ENABLED"] = "0"
            GPU.get_gpu_config.cache_clear()
            print(f"\n### {name.upper()} — legacy CPU subprocess path ###")
            text_c, meta_c, dt_c, _ = _run(mod, args.pdf, use_gpu=False)
            sim = difflib.SequenceMatcher(None, text_w, text_c).ratio()
            print(f"-> {len(meta_c)} pages, {len(text_c)} chars, {dt_c:.1f}s")
            print(f"== Speed-up (warm GPU vs CPU): {dt_c / max(dt_w, 1e-6):.1f}x | "
                  f"text similarity {sim:.3f}")
            os.environ["GPU_OCR_ENABLED"] = "1"
            GPU.get_gpu_config.cache_clear()

        GPU.shutdown()
        for k in list(sys.modules.keys()):
            if "paddleocr_enhancer" in k:
                del sys.modules[k]

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
