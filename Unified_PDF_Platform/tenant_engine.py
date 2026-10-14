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
import re
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import requests
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

        # Run generic API verifications (if defined in extraction.json)
        verifications = self._run_api_verifications(result, source_doc_type)
        if verifications:
            if "data" in result and isinstance(result["data"], dict):
                result["data"]["verifications"] = verifications
            result["verifications"] = verifications

        # Tag the output (only when the tenant config defines a tag)
        if self.output_tag:
            result["tenant"] = self.output_tag

        ext_keys = list(extensions_added.keys())
        ver_keys = list(verifications.keys()) if verifications else []
        log_msg = f"[TenantEngine][{self.tenant_code}] Enrichment complete. Extensions added: {ext_keys}"
        if ver_keys:
            log_msg += f", Verifications: {ver_keys}"
        logger.info(log_msg)

        return result

    # ------------------------------------------------------------------
    # Nested token & API verification resolution helpers
    # ------------------------------------------------------------------
    def _extract_nested_val(self, obj: Any, path: str) -> Any:
        if not path or obj is None:
            return None

        def _traverse(target: Any, parts: List[str]) -> Any:
            curr = target
            for part in parts:
                if isinstance(curr, dict):
                    if part in curr:
                        curr = curr[part]
                    elif part.isdigit() and int(part) < len(curr):
                        curr = curr[part]
                    else:
                        return None
                elif isinstance(curr, list) and part.isdigit():
                    idx = int(part)
                    if 0 <= idx < len(curr):
                        curr = curr[idx]
                    else:
                        return None
                else:
                    return None
            return curr

        parts = path.strip().split(".")
        val = _traverse(obj, parts)
        if val is not None:
            return val

        # Fallback: search inside 'data' if obj wraps data
        if isinstance(obj, dict) and "data" in obj and isinstance(obj["data"], dict):
            val = _traverse(obj["data"], parts)
            if val is not None:
                return val

        return None

    def _evaluate_token_expr(self, expr: str, context: Dict[str, Any]) -> Any:
        """
        Evaluates a token expression like 'demographics.fein | digits_only' or 'demographics.state | default:demographics.wcStates'.
        """
        parts = [p.strip() for p in expr.split("|")]
        path = parts[0]
        filters = parts[1:]

        val = self._extract_nested_val(context, path)

        for f in filters:
            f_clean = f.strip()
            if f_clean == "digits_only":
                val = re.sub(r"[^\d]", "", str(val or ""))
            elif f_clean == "uppercase":
                val = str(val or "").upper()
            elif f_clean == "lowercase":
                val = str(val or "").lower()
            elif f_clean in ("trim", "strip"):
                val = str(val or "").strip()
            elif f_clean.startswith("default:"):
                if val is None or str(val).strip() == "":
                    fallback = f_clean.split(":", 1)[1].strip()
                    fb_val = self._extract_nested_val(context, fallback)
                    val = fb_val if fb_val not in (None, "") else fallback

        return val

    def _resolve_token_string(self, text: str, context: Dict[str, Any]) -> Any:
        """
        Resolves tokens like ${demographics.fein | digits_only} or ${demographics.applicantName}.
        If the entire string is just one token, returns the resolved value directly (preserving type).
        If embedded in text, interpolates string representation.
        """
        if not isinstance(text, str) or "${" not in text:
            return text

        token_regex = re.compile(r"\$\{([^}]+)\}")

        # Check if the entire string is a single token expression e.g. "${demographics.fein | digits_only}"
        single_match = token_regex.fullmatch(text.strip())
        if single_match:
            return self._evaluate_token_expr(single_match.group(1), context)

        # Embedded tokens
        def _replace(match):
            res = self._evaluate_token_expr(match.group(1), context)
            return "" if res is None else str(res)

        return token_regex.sub(_replace, text)

    def _resolve_template(self, obj: Any, context: Dict[str, Any]) -> Any:
        """Recursively resolves token expressions in dicts, lists, and strings."""
        if isinstance(obj, dict):
            return {k: self._resolve_template(v, context) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._resolve_template(v, context) for v in obj]
        elif isinstance(obj, str):
            return self._resolve_token_string(obj, context)
        return obj

    def _run_api_verifications(
        self,
        extracted_data: Dict[str, Any],
        source_doc_type: str,
    ) -> Dict[str, Any]:
        """
        Executes generic config-driven external API verifications defined in extraction.json.
        Returns a dict mapping output keys to their API responses.
        Zero hardcoding: every endpoint, auth header, body, and parameter comes from JSON.
        """
        api_configs = self.config.get("api_verifications", [])
        if not api_configs:
            return {}

        verifications: Dict[str, Any] = {}

        for item in api_configs:
            if not item.get("enabled", True):
                continue

            item_id = item.get("id") or item.get("output_key") or "verification"
            item_name = item.get("name", item_id)
            output_key = item.get("output_key") or item_id

            # Check doc type trigger
            trigger_type = str(item.get("trigger_on_doc_type", "")).upper()
            if trigger_type and trigger_type != source_doc_type.upper():
                logger.info(
                    f"[TenantEngine][{self.tenant_code}] Skipping verification '{item_name}' "
                    f"(trigger={trigger_type}, current={source_doc_type})"
                )
                continue

            # Check required fields
            req_fields = item.get("required_fields") or item.get("required_input_fields") or []
            missing_req = False
            for req in req_fields:
                val = self._extract_nested_val(extracted_data, req)
                if val is None or str(val).strip() == "":
                    logger.warning(
                        f"[TenantEngine][{self.tenant_code}] Verification '{item_name}' skipped: "
                        f"required field '{req}' is missing or empty."
                    )
                    verifications[output_key] = {
                        "status": "SKIPPED",
                        "name": item_name,
                        "reason": f"Required field '{req}' missing or empty",
                        "data": None
                    }
                    missing_req = True
                    break
            if missing_req:
                continue

            # Resolve url, headers, params, body
            raw_url = item.get("url", "")
            if not raw_url:
                continue

            resolved_url = str(self._resolve_template(raw_url, extracted_data))
            resolved_headers = self._resolve_template(item.get("headers", {}), extracted_data) or {}
            resolved_params = self._resolve_template(item.get("params", {}), extracted_data) or {}
            resolved_body = self._resolve_template(item.get("body", None), extracted_data)
            method = str(item.get("method", "GET")).upper()
            timeout_sec = float(item.get("timeout_seconds", 15.0))

            logger.info(
                f"[TenantEngine][{self.tenant_code}] Executing API verification '{item_name}' ({method} {resolved_url})..."
            )

            try:
                if method == "POST":
                    if isinstance(resolved_body, (dict, list)):
                        resp = requests.post(
                            resolved_url,
                            headers=resolved_headers,
                            json=resolved_body,
                            timeout=timeout_sec
                        )
                    else:
                        resp = requests.post(
                            resolved_url,
                            headers=resolved_headers,
                            data=resolved_body,
                            timeout=timeout_sec
                        )
                elif method == "GET":
                    resp = requests.get(
                        resolved_url,
                        headers=resolved_headers,
                        params=resolved_params,
                        timeout=timeout_sec
                    )
                else:
                    resp = requests.request(
                        method,
                        resolved_url,
                        headers=resolved_headers,
                        params=resolved_params,
                        json=resolved_body if isinstance(resolved_body, (dict, list)) else None,
                        data=resolved_body if not isinstance(resolved_body, (dict, list)) else None,
                        timeout=timeout_sec
                    )

                # Parse response
                resp_json = None
                try:
                    resp_json = resp.json()
                except Exception:
                    resp_json = {"raw_text": resp.text[:2000]}

                if resp.status_code in (200, 201, 202):
                    logger.info(f"[TenantEngine][{self.tenant_code}] Verification '{item_name}' succeeded (HTTP {resp.status_code}).")
                    verifications[output_key] = resp_json
                else:
                    logger.warning(
                        f"[TenantEngine][{self.tenant_code}] Verification '{item_name}' returned HTTP {resp.status_code}: {resp.text[:300]}"
                    )
                    verifications[output_key] = {
                        "status": "HTTP_ERROR",
                        "status_code": resp.status_code,
                        "name": item_name,
                        "response": resp_json,
                    }

            except requests.exceptions.Timeout:
                logger.error(f"[TenantEngine][{self.tenant_code}] Verification '{item_name}' timed out after {timeout_sec}s.")
                verifications[output_key] = {
                    "status": "TIMEOUT",
                    "name": item_name,
                    "error": f"Request timed out after {timeout_sec}s",
                    "data": None
                }
            except Exception as req_err:
                logger.error(f"[TenantEngine][{self.tenant_code}] Verification '{item_name}' failed: {req_err}", exc_info=True)
                verifications[output_key] = {
                    "status": "ERROR",
                    "name": item_name,
                    "error": str(req_err),
                    "data": None
                }

        return verifications

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
