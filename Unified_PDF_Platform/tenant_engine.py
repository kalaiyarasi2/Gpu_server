"""
TenantExtractionEngine
======================
A single, config-driven extraction engine that supports unlimited tenants.

Adding a new tenant requires ZERO Python code.
Just create:
    config/tenants/<tenant_code>/extraction.json

The engine reads the JSON config and:
1. Runs the base extractor (WORK_COMP / INSURANCE)
2. Applies tenant-specific schema extensions via targeted GPT calls
3. Tags and returns the enriched result

Usage
-----
    engine = TenantExtractionEngine("WCUW")
    result = await engine.enrich(base_json_path, extracted_text, source_doc_type="WORK_COMP")
"""

import os
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional
from openai import OpenAI

logger = logging.getLogger("tenant_engine")

# Root of the config/tenants/ directory — resolved relative to this file's location:
# This file lives at: Gpu_server/Unified_PDF_Platform/tenant_engine.py
# Workspace root:     Sales team - Copy/
# Config dir:        Sales team - Copy/config/tenants/
_THIS_FILE = Path(__file__).resolve()
_GPU_SERVER_DIR = _THIS_FILE.parent.parent        # Gpu_server/
_WORKSPACE_DIR = _GPU_SERVER_DIR.parent           # Sales team - Copy/
TENANTS_CONFIG_DIR = _WORKSPACE_DIR / "config" / "tenants"


# ---------------------------------------------------------------------------
# Default (no-op) extraction config — used when no extraction.json exists
# ---------------------------------------------------------------------------
_DEFAULT_CONFIG: Dict[str, Any] = {
    "tenant_code": None,
    "base_engines": [],
    "output_tag": None,
    "output_subfolder": None,
    "schema_extensions": [],
    "field_overrides": {},
    "frontend": {}
}


# ---------------------------------------------------------------------------
# TenantExtractionEngine
# ---------------------------------------------------------------------------
class TenantExtractionEngine:
    """
    Config-driven extraction enrichment engine.

    One instance per request (cheap to create — just loads a JSON file).
    """

    def __init__(self, tenant_code: str):
        self.tenant_code = tenant_code.upper().strip()
        self.config = self._load_config()
        self._openai_client: Optional[OpenAI] = None

    # ------------------------------------------------------------------
    # Config loading
    # ------------------------------------------------------------------
    def _load_config(self) -> Dict[str, Any]:
        """Load extraction.json for this tenant. Falls back to default."""
        config_path = TENANTS_CONFIG_DIR / self.tenant_code.lower() / "extraction.json"
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                logger.info(f"[TenantEngine] Loaded config for '{self.tenant_code}' from {config_path}")
                return cfg
            except Exception as e:
                logger.warning(f"[TenantEngine] Failed to parse extraction.json for '{self.tenant_code}': {e}")
        else:
            logger.info(f"[TenantEngine] No extraction.json found for '{self.tenant_code}', using default (no extensions).")
        return dict(_DEFAULT_CONFIG)

    @property
    def has_extensions(self) -> bool:
        """True if this tenant has at least one enabled schema extension."""
        return any(
            ext.get("enabled", False)
            for ext in self.config.get("schema_extensions", [])
        )

    @property
    def output_tag(self) -> Optional[str]:
        return self.config.get("output_tag") or self.config.get("tenant_code")

    # ------------------------------------------------------------------
    # OpenAI client (lazy, cached)
    # ------------------------------------------------------------------
    def _get_openai_client(self) -> OpenAI:
        if self._openai_client is None:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY environment variable is not set.")
            self._openai_client = OpenAI(api_key=api_key)
        return self._openai_client

    # ------------------------------------------------------------------
    # Core: run a single schema extension via GPT
    # ------------------------------------------------------------------
    def _run_gpt_extension(
        self,
        extension: Dict[str, Any],
        extracted_text: str,
    ) -> Any:
        """
        Runs one schema_extension via a targeted GPT call.
        Returns parsed JSON (array or object) or None on failure.
        """
        ext_name = extension.get("name", "unknown")
        prompt_instruction = extension.get("extraction_prompt", "")
        output_type = extension.get("output_type", "array")  # "array" | "object"

        if not prompt_instruction:
            logger.warning(f"[TenantEngine][{self.tenant_code}] Extension '{ext_name}' has no extraction_prompt — skipping.")
            return None

        # Build the full prompt
        system_prompt = (
            "You are a precise document data extraction assistant. "
            "You always return ONLY valid JSON — no markdown fences, no explanation text. "
            f"The output must be a JSON {output_type}."
        )

        user_prompt = (
            f"{prompt_instruction}\n\n"
            f"---BEGIN DOCUMENT TEXT---\n{extracted_text[:60000]}\n---END DOCUMENT TEXT---\n\n"
            f"Return ONLY a valid JSON {output_type}. No markdown. No extra text."
        )

        logger.info(f"[TenantEngine][{self.tenant_code}] Running extension '{ext_name}' with {len(extracted_text)} chars of text...")
        try:
            client = self._get_openai_client()
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
                max_tokens=2000,
            )
            raw = response.choices[0].message.content.strip()

            # Strip accidental markdown fences
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(
                    line for line in lines
                    if not line.strip().startswith("```")
                )

            parsed = json.loads(raw)
            logger.info(f"[TenantEngine][{self.tenant_code}] Extension '{ext_name}' extracted successfully.")
            return parsed

        except json.JSONDecodeError as e:
            logger.warning(f"[TenantEngine][{self.tenant_code}] Extension '{ext_name}' returned invalid JSON: {e}")
            return None
        except Exception as e:
            logger.error(f"[TenantEngine][{self.tenant_code}] Extension '{ext_name}' GPT call failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Apply field_overrides (currently: log-level only, extend as needed)
    # ------------------------------------------------------------------
    def _apply_field_overrides(self, data: Dict[str, Any], source_doc_type: str = "WORK_COMP") -> Dict[str, Any]:
        """
        Apply field-level overrides from config.
        Currently validates required fields and logs warnings.
        """
        # If this is INSURANCE (Loss Run), demographics fields do not apply
        if source_doc_type.upper() == "INSURANCE":
            return data

        overrides = self.config.get("field_overrides", {})
        inner = data.get("data", data)

        for field_path, rules in overrides.items():
            if rules.get("required"):
                # Walk nested keys e.g. "demographics.applicantName"
                keys = field_path.split(".")
                val = inner
                for k in keys:
                    val = val.get(k, None) if isinstance(val, dict) else None
                if not val:
                    logger.warning(
                        f"[TenantEngine][{self.tenant_code}] Required field '{field_path}' is missing or empty in extracted output."
                    )
        return data

    # ------------------------------------------------------------------
    # Public: enrich(base_json_path, extracted_text, source_doc_type)
    # ------------------------------------------------------------------
    def enrich(
        self,
        base_json_path: str,
        extracted_text: str,
        source_doc_type: str = "WORK_COMP",
    ) -> Dict[str, Any]:
        """
        Load the base extracted JSON, run all enabled schema extensions
        that match source_doc_type, and return the enriched dict.

        Parameters
        ----------
        base_json_path : str
            Absolute path to the base extracted_schema.json
        extracted_text : str
            The full OCR/extracted text from the PDF (used for GPT prompts)
        source_doc_type : str
            Doc type that was used for base extraction ("WORK_COMP" or "INSURANCE")

        Returns
        -------
        dict
            The base JSON data enriched with tenant-specific extension fields,
            tagged with { "tenant": "<TENANT_CODE>", ... }
        """
        # Load base JSON
        base_path = Path(base_json_path)
        if not base_path.exists():
            logger.error(f"[TenantEngine] Base JSON not found: {base_json_path}")
            return {"error": f"Base extraction JSON not found: {base_json_path}"}

        with open(base_path, "r", encoding="utf-8") as f:
            result = json.load(f)

        if not self.has_extensions:
            logger.info(f"[TenantEngine] No extensions for tenant '{self.tenant_code}' — returning base result.")
            if self.output_tag:
                result["tenant"] = self.output_tag
            return result

        # Run each enabled extension
        extensions_added = {}
        for ext in self.config.get("schema_extensions", []):
            if not ext.get("enabled", False):
                continue

            # Only run extensions relevant to this doc type
            ext_source = ext.get("source_doc_type", "").upper()
            if ext_source and ext_source != source_doc_type.upper():
                logger.info(
                    f"[TenantEngine] Skipping extension '{ext.get('name')}' "
                    f"(source={ext_source}, current={source_doc_type})"
                )
                continue

            ext_result = self._run_gpt_extension(ext, extracted_text)
            if ext_result is not None:
                extensions_added[ext["name"]] = ext_result
            else:
                logger.warning(
                    f"[TenantEngine][{self.tenant_code}] Extension '{ext.get('name')}' returned no result — "
                    f"writing empty placeholder."
                )
                extensions_added[ext["name"]] = [] if str(ext.get("output_type", "")).lower() == "array" else {}

        # Merge extensions into the result under data{}
        if extensions_added:
            if "data" in result and isinstance(result["data"], dict):
                result["data"].update(extensions_added)
            else:
                result.update(extensions_added)

        # Apply field overrides / validation
        result = self._apply_field_overrides(result, source_doc_type)

        # Tag the output (only when the tenant config defines a tag)
        if self.output_tag:
            result["tenant"] = self.output_tag

        logger.info(
            f"[TenantEngine][{self.tenant_code}] Enrichment complete. "
            f"Extensions added: {list(extensions_added.keys())}"
        )

        return result

    # ------------------------------------------------------------------
    # Convenience: load extracted_text from the extraction session folder
    # ------------------------------------------------------------------
    @staticmethod
    def load_extracted_text_from_session(session_output_dir: str) -> str:
        """
        Given a session output directory (e.g. outputs/extraction_XXXX/),
        load the extracted_text.txt if it exists.
        """
        text_path = Path(session_output_dir) / "extracted_text.txt"
        if text_path.exists():
            try:
                return text_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(f"[TenantEngine] Could not read extracted_text.txt: {e}")
        return ""

    # ------------------------------------------------------------------
    # Class-level: list all configured tenants
    # ------------------------------------------------------------------
    @classmethod
    def list_tenants(cls) -> list:
        """Return a list of tenant codes that have an extraction.json."""
        if not TENANTS_CONFIG_DIR.exists():
            return []
        return [
            d.name.upper()
            for d in TENANTS_CONFIG_DIR.iterdir()
            if d.is_dir() and (d / "extraction.json").exists()
        ]
