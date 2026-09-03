"""
ai_metrics.py
=============
AI token usage, cost, and efficiency calculator.

Supports: gpt-4o, gpt-4o-mini, gpt-5, gpt-5-mini, gpt-5-nano
Default model: gpt-4o

Usage
-----
    from ai_metrics import compute_metrics

    result = compute_metrics(
        prompt_tokens=4200,
        completion_tokens=850,
        processing_time_sec=3.2,
        pages=5,
        model="gpt-4o",
    )
    print(result)                        # MetricsResult dataclass
    print(result.to_json())              # Strict JSON string
    print(result.to_dict())              # Plain dict
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Pricing table  (USD per 1 000 tokens)
# ---------------------------------------------------------------------------

PRICING: dict[str, dict[str, float]] = {
    "gpt-4o":      {"input": 0.0025,  "output": 0.01},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-5":       {"input": 0.00125, "output": 0.01},
    "gpt-5-mini":  {"input": 0.00025, "output": 0.002},
    "gpt-5-nano":  {"input": 0.00005, "output": 0.0004},
}

DEFAULT_MODEL = "gpt-4o"

CostLevel = Literal["low", "medium", "high"]
TimeLevel = Literal["fast", "average", "slow"]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class MetricsResult:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    input_cost: float
    output_cost: float
    total_cost: float
    processing_time_sec: float
    pages: int
    cost_per_page: float
    time_per_page: float
    tokens_per_page: float
    model: str
    analysis: str

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a plain dict (all floats already rounded to 6 dp)."""
        return asdict(self)

    def to_json(self, indent: int | None = 2) -> str:
        """Return strict JSON string matching the module contract."""
        return json.dumps(self.to_dict(), indent=indent)

    def __str__(self) -> str:
        return self.to_json()


# ---------------------------------------------------------------------------
# Core calculation
# ---------------------------------------------------------------------------

def _r6(value: float) -> float:
    """Round to 6 decimal places."""
    return round(value, 6)


def _classify_cost(total_cost: float) -> CostLevel:
    if total_cost <= 0.005:
        return "low"
    if total_cost <= 0.05:
        return "medium"
    return "high"


def _classify_time(time_per_page: float) -> TimeLevel:
    if time_per_page <= 1.0:
        return "fast"
    if time_per_page <= 3.0:
        return "average"
    return "slow"


def compute_metrics(
    prompt_tokens: int,
    completion_tokens: int,
    processing_time_sec: float,
    pages: int,
    model: str = DEFAULT_MODEL,
) -> MetricsResult:
    """
    Compute token usage, cost, and efficiency metrics.

    Parameters
    ----------
    prompt_tokens       : Number of input/prompt tokens consumed.
    completion_tokens   : Number of output/completion tokens generated.
    processing_time_sec : Wall-clock processing time in seconds.
    pages               : Number of document pages processed.
    model               : Model identifier.  Unknown models fall back to
                          the gpt-4o pricing tier.

    Returns
    -------
    MetricsResult dataclass with all fields populated.

    Raises
    ------
    ValueError  If any numeric input is negative.
    """
    # ---- Validate inputs --------------------------------------------------
    if prompt_tokens < 0:
        raise ValueError(f"prompt_tokens must be >= 0, got {prompt_tokens}")
    if completion_tokens < 0:
        raise ValueError(f"completion_tokens must be >= 0, got {completion_tokens}")
    if processing_time_sec < 0:
        raise ValueError(f"processing_time_sec must be >= 0, got {processing_time_sec}")

    # ---- Pricing ----------------------------------------------------------
    price = PRICING.get(model, PRICING[DEFAULT_MODEL])

    # ---- Core calculations ------------------------------------------------
    total_tokens = prompt_tokens + completion_tokens
    input_cost   = _r6((prompt_tokens   / 1_000) * price["input"])
    output_cost  = _r6((completion_tokens / 1_000) * price["output"])
    total_cost   = _r6(input_cost + output_cost)

    # ---- Per-page metrics (guard against zero pages) ----------------------
    safe_pages       = max(pages, 1)
    cost_per_page    = _r6(total_cost   / safe_pages)
    time_per_page    = _r6(processing_time_sec / safe_pages)
    tokens_per_page  = _r6(total_tokens / safe_pages)

    # ---- Analysis ---------------------------------------------------------
    cost_level   = _classify_cost(total_cost)
    time_level   = _classify_time(time_per_page)
    dominant     = "input" if input_cost >= output_cost else "output"

    analysis = (
        f"Cost is {cost_level} (${total_cost:.6f} total, "
        f"${cost_per_page:.6f}/page). "
        f"Processing is {time_level} ({time_per_page:.3f}s/page). "
        f"{dominant.capitalize()} cost is higher "
        f"(${input_cost:.6f} input vs ${output_cost:.6f} output)."
    )

    return MetricsResult(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        input_cost=input_cost,
        output_cost=output_cost,
        total_cost=total_cost,
        processing_time_sec=_r6(processing_time_sec),
        pages=pages,
        cost_per_page=cost_per_page,
        time_per_page=time_per_page,
        tokens_per_page=tokens_per_page,
        model=model,
        analysis=analysis,
    )


# ---------------------------------------------------------------------------
# Batch helper
# ---------------------------------------------------------------------------

def compute_batch(records: list[dict]) -> list[MetricsResult]:
    """
    Process a list of metric records in one call.

    Each record is a dict accepted by compute_metrics() as keyword args.

    Example
    -------
        results = compute_batch([
            {"prompt_tokens": 1000, "completion_tokens": 200,
             "processing_time_sec": 1.5, "pages": 2, "model": "gpt-4o-mini"},
            {"prompt_tokens": 8000, "completion_tokens": 1500,
             "processing_time_sec": 6.0, "pages": 10},
        ])
    """
    return [compute_metrics(**r) for r in records]


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="AI Metrics & Cost Calculator",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--prompt-tokens",       type=int,   required=True)
    parser.add_argument("--completion-tokens",   type=int,   required=True)
    parser.add_argument("--processing-time-sec", type=float, required=True)
    parser.add_argument("--pages",               type=int,   required=True)
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        choices=list(PRICING.keys()),
    )
    parser.add_argument(
        "--no-indent",
        action="store_true",
        help="Output compact JSON (no indentation)",
    )

    args = parser.parse_args()
    result = compute_metrics(
        prompt_tokens=args.prompt_tokens,
        completion_tokens=args.completion_tokens,
        processing_time_sec=args.processing_time_sec,
        pages=args.pages,
        model=args.model,
    )
    indent = None if args.no_indent else 2
    print(result.to_json(indent=indent))


if __name__ == "__main__":
    _cli()
