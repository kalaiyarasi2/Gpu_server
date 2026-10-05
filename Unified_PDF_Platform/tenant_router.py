"""
Tenant Router (Universal Multi-Tenant Router)
=============================================
A SINGLE generic router for all 50+ tenants.
ZERO per-tenant Python files required.

Adding a new tenant:
  1. Create config/tenants/<tenant_code>/extraction.json
  2. Done. No Python code. No backend restart.

Dynamic Endpoints
-----------------
POST /api/tenant/{tenant}/extract-acord    — ACORD 130 + tenant enrichment (WORK_COMP)
POST /api/tenant/{tenant}/extract-lossrun  — Loss Run + tenant enrichment (INSURANCE)
POST /api/tenant/{tenant}/extract          — Universal extraction (auto-detects type)
POST /api/tenant/{tenant}/enrich           — Re-enrich an existing extraction session
GET  /api/tenant/{tenant}/schema           — Return extraction.json config for tenant
GET  /api/tenant/list                      — List all configured tenants

Also supports non-parameterized routes where tenant is resolved via:
  - Header: X-Tenant-ID
  - Query param: ?tenant=WCUW
  - Fallback: WCUW or DEFAULT
"""

import os
import json
import shutil
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, UploadFile, HTTPException, Request, Query
from fastapi.responses import JSONResponse
from fastapi.concurrency import run_in_threadpool
from starlette.requests import Request as StarletteRequest

logger = logging.getLogger("tenant_router")

# Path resolution
_THIS_DIR = Path(__file__).parent.resolve()          # Unified_PDF_Platform/
_GPU_SERVER_DIR = _THIS_DIR.parent                    # Gpu_server/
_WORKSPACE_DIR = _GPU_SERVER_DIR.parent               # Sales team - Copy/

UPLOAD_DIR = _THIS_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

WC_OUTPUTS_DIR = _GPU_SERVER_DIR / "work_compenstaion" / "backend" / "outputs"
INS_OUTPUTS_DIR = _GPU_SERVER_DIR / "Insurance_pdf_extractor-main" / "backend" / "outputs"
TENANTS_CONFIG_DIR = _WORKSPACE_DIR / "config" / "tenants"
MODIFIER_POC_DIR = _WORKSPACE_DIR / "Modifier_Poc"

router = APIRouter(tags=["Multi-Tenant Engine"])


# ---------------------------------------------------------------------------
# Lazy-load TenantExtractionEngine
# ---------------------------------------------------------------------------
def _get_engine(tenant_code: str):
    """Import and instantiate TenantExtractionEngine for any tenant."""
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "_tenant_engine_mod",
        str(_THIS_DIR / "tenant_engine.py")
    )
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    return _mod.TenantExtractionEngine(tenant_code)


# ---------------------------------------------------------------------------
# Resolve tenant code from path, header, query, or JWT
# ---------------------------------------------------------------------------
def _resolve_tenant(request: Request, path_or_query_tenant: Optional[str] = None) -> str:
    """
    Priority:
    1. Explicit parameter (from URL path {tenant} or query ?tenant=)
    2. HTTP header: X-Tenant-ID
    3. JWT authorization bearer claim
    4. Fallback: WCUW
    """
    if path_or_query_tenant and path_or_query_tenant.strip():
        return path_or_query_tenant.upper().strip()

    header_tenant = request.headers.get("x-tenant-id", "").strip()
    if header_tenant:
        return header_tenant.upper()

    try:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            import jwt
            token = auth.split(" ", 1)[1]
            secret = os.getenv("JWT_SECRET_KEY", "fc_sales_team_super_secret_jwt_key_2026")
            payload = jwt.decode(token, secret, algorithms=["HS256"])
            t = payload.get("tenant") or payload.get("tenant_code")
            if t:
                return str(t).upper().strip()
    except Exception:
        pass

    logger.warning("[TenantRouter] No tenant in path/header/token — falling back to 'DEFAULT'.")
    return "DEFAULT"


# ---------------------------------------------------------------------------
# Locate session output directory
# ---------------------------------------------------------------------------
def _find_session_dir(request_id: str, outputs_root: Path) -> Optional[Path]:
    if not outputs_root.exists():
        return None
    for d in outputs_root.iterdir():
        if d.is_dir() and request_id in d.name:
            return d
    prefix = request_id[:4] if len(request_id) >= 4 else request_id
    matches = sorted(
        [d for d in outputs_root.iterdir() if d.is_dir() and prefix in d.name],
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# Call base extraction (untouched core)
# ---------------------------------------------------------------------------
async def _call_base_extraction(file: UploadFile, request: Request, doc_type_hint: str) -> dict:
    from shared_configs import _perform_extraction
    scope = dict(request.scope)
    existing_headers = list(scope.get("headers", []))
    existing_headers.append((b"x-document-type", doc_type_hint.encode()))
    scope["headers"] = existing_headers
    patched_request = StarletteRequest(scope, request._receive)
    return await _perform_extraction(file, patched_request)


# ---------------------------------------------------------------------------
# Core pipeline: Base extraction + Tenant Enrichment
# ---------------------------------------------------------------------------
async def _execute_tenant_extraction(
    file: UploadFile,
    request: Request,
    doc_type: str,            # "WORK_COMP" or "INSURANCE"
    tenant_code: str,
    outputs_root: Path,
) -> dict:
    filename = file.filename or ""
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    logger.info(f"[TenantRouter] Processing '{filename}' for tenant '{tenant_code}' (type: {doc_type})")

    # 1. Base extraction (runs existing core extractor)
    await file.seek(0)
    base_result = await _call_base_extraction(file, request, doc_type_hint=doc_type)

    if "error" in base_result:
        raise HTTPException(status_code=422, detail=base_result["error"])

    output_json_filename = base_result.get("output_json")
    request_id = base_result.get("requestId", "")

    # 2. Locate base JSON
    from shared_configs import file_path_cache, _load_cache, _save_cache
    disk_cache = _load_cache()
    file_path_cache.update(disk_cache)
    base_json_path = file_path_cache.get(output_json_filename, "")

    # 3. Locate session dir to get extracted_text.txt
    session_dir = _find_session_dir(request_id, outputs_root) if request_id else None
    if not session_dir and base_json_path:
        session_dir = Path(base_json_path).parent

    if not base_json_path and session_dir:
        candidate = session_dir / "extracted_schema.json"
        if candidate.exists():
            base_json_path = str(candidate)

    if not base_json_path:
        logger.warning(f"[TenantRouter][{tenant_code}] Base JSON not found — returning base result.")
        base_result["tenant"] = tenant_code
        return base_result

    # 4. Read full extracted OCR text
    extracted_text = ""
    if session_dir:
        text_path = session_dir / "extracted_text.txt"
        if text_path.exists():
            try:
                extracted_text = text_path.read_text(encoding="utf-8")
                logger.info(f"[TenantRouter][{tenant_code}] Loaded extracted_text.txt ({len(extracted_text)} chars) from {text_path}")
            except Exception as e:
                logger.warning(f"[TenantRouter] Could not read extracted_text.txt: {e}")

    # Fallback to Unified_PDF_Platform/extracted_text if still empty
    if not extracted_text:
        uni_text_dir = _THIS_DIR / "extracted_text"
        if uni_text_dir.exists():
            clean_stem = Path(filename).stem
            for txt_cand in sorted(uni_text_dir.glob(f"*{clean_stem}*.txt"), key=lambda p: p.stat().st_mtime, reverse=True):
                try:
                    extracted_text = txt_cand.read_text(encoding="utf-8")
                    if extracted_text.strip():
                        logger.info(f"[TenantRouter][{tenant_code}] Fallback loaded extracted text ({len(extracted_text)} chars) from {txt_cand.name}")
                        break
                except Exception:
                    pass

    # 5. Run TenantExtractionEngine (reads config/tenants/<tenant_code>/extraction.json)
    engine = _get_engine(tenant_code)
    try:
        enriched = await run_in_threadpool(
            engine.enrich,
            base_json_path,
            extracted_text,
            doc_type
        )
    except Exception as e:
        logger.error(f"[TenantRouter][{tenant_code}] Enrichment failed: {e}")
        base_result["tenant"] = tenant_code
        base_result["enrichment_error"] = str(e)
        return base_result

    # 6. Save enriched JSON
    try:
        enriched_path = Path(base_json_path).parent / f"extracted_schema_{tenant_code.lower()}.json"
        with open(enriched_path, "w", encoding="utf-8") as f:
            json.dump(enriched, f, indent=2)

        enriched_fname = enriched_path.name
        file_path_cache[enriched_fname] = str(enriched_path)
        _save_cache(file_path_cache)
        base_result[f"output_json_{tenant_code.lower()}"] = enriched_fname
    except Exception as e:
        logger.warning(f"[TenantRouter][{tenant_code}] Could not write enriched JSON: {e}")

    # 7. Merge enriched response
    base_result["enriched_schema"] = enriched
    base_result["tenant"] = enriched.get("tenant", tenant_code)
    base_result["extensions_applied"] = list(
        (enriched.get("data") or {}).keys()
    )

    return base_result


# ---------------------------------------------------------------------------
# Modifier POC extraction execution
# ---------------------------------------------------------------------------
async def _execute_modifier_extraction(
    file: UploadFile,
    request: Request,
    year: int = 2026,
) -> dict:
    filename = file.filename or "modifier_document.pdf"
    suffix = Path(filename).suffix.lower()
    allowed = {".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
    if suffix not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}'. Allowed: {', '.join(sorted(allowed))}",
        )

    input_dir = MODIFIER_POC_DIR / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    out_dir = MODIFIER_POC_DIR / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    extracted_text_dir = out_dir / "extracted_text"
    extracted_text_dir.mkdir(parents=True, exist_ok=True)

    input_path = input_dir / filename
    await file.seek(0)
    with open(input_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    def _run_extract():
        import sys
        if str(MODIFIER_POC_DIR) not in sys.path:
            sys.path.insert(0, str(MODIFIER_POC_DIR))
        from extract_xmod import process_file, load_llm_client
        env_file = MODIFIER_POC_DIR / ".env"
        if not env_file.exists():
            env_file = _WORKSPACE_DIR / ".env"
        llm = load_llm_client(env_file)
        return process_file(
            ocr=None,
            path=input_path,
            year=year,
            dpi=300,
            dump_dir=extracted_text_dir,
            llm=llm,
            use_llm=(llm is not None),
        )

    try:
        res = await run_in_threadpool(_run_extract)
    except Exception as e:
        logger.error(f"[TenantRouter] Modifier extraction error: {e}", exc_info=True)
        return {
            "filename": filename,
            "experience_mod": 1.0,
            "error": str(e),
            "status": "error",
        }

    exp_mod = res.get("experience_mod")
    if exp_mod is not None:
        try:
            exp_mod = float(exp_mod)
        except (ValueError, TypeError):
            pass

    return {
        "filename": filename,
        "experience_mod": exp_mod if exp_mod is not None else 1.0,
        "experience_mod_raw": res.get("experience_mod_raw"),
        "extraction_method": res.get("extraction_method"),
        "extraction_source": res.get("extraction_source"),
        "confidence": res.get("confidence", 1.0),
        "status": res.get("status", "success"),
    }


# ===========================================================================
# PARAMETERIZED ROUTES: /api/tenant/{tenant}/...
# ===========================================================================

@router.post("/{tenant}/extract-acord", summary="Extract ACORD 130 with tenant extensions")
async def tenant_extract_acord_param(
    tenant: str,
    request: Request,
    file: UploadFile = File(...),
):
    tenant_code = _resolve_tenant(request, tenant)
    result = await _execute_tenant_extraction(file, request, "WORK_COMP", tenant_code, WC_OUTPUTS_DIR)
    return JSONResponse(content=result)


@router.post("/{tenant}/extract-lossrun", summary="Extract Loss Run with tenant extensions")
async def tenant_extract_lossrun_param(
    tenant: str,
    request: Request,
    file: UploadFile = File(...),
):
    tenant_code = _resolve_tenant(request, tenant)
    result = await _execute_tenant_extraction(file, request, "INSURANCE", tenant_code, INS_OUTPUTS_DIR)
    return JSONResponse(content=result)


@router.post("/{tenant}/extract-modifier", summary="Extract Experience Modifier (X-Mod) with Modifier POC")
async def tenant_extract_modifier_param(
    tenant: str,
    request: Request,
    file: UploadFile = File(...),
    year: int = Query(default=2026),
):
    result = await _execute_modifier_extraction(file, request, year=year)
    return JSONResponse(content=result)


@router.post("/{tenant}/extract", summary="Universal extraction for tenant")
async def tenant_extract_universal_param(
    tenant: str,
    request: Request,
    file: UploadFile = File(...),
    doc_type: Optional[str] = Query(default="WORK_COMP"),
):
    tenant_code = _resolve_tenant(request, tenant)
    hint = (doc_type or "WORK_COMP").upper()
    outputs_root = WC_OUTPUTS_DIR if hint == "WORK_COMP" else INS_OUTPUTS_DIR
    result = await _execute_tenant_extraction(file, request, hint, tenant_code, outputs_root)
    return JSONResponse(content=result)


@router.get("/{tenant}/schema", summary="Get tenant extraction configuration")
async def tenant_get_schema(tenant: str):
    tenant_code = tenant.upper().strip()
    config_path = TENANTS_CONFIG_DIR / tenant_code.lower() / "extraction.json"
    if not config_path.exists():
        raise HTTPException(status_code=404, detail=f"No extraction.json found for tenant '{tenant_code}'")
    with open(config_path, "r", encoding="utf-8") as f:
        return JSONResponse(content=json.load(f))


# ===========================================================================
# NON-PARAMETERIZED ROUTES (Supports ?tenant= or Header: X-Tenant-ID)
# ===========================================================================

@router.post("/extract-acord", summary="ACORD extraction (tenant from header or query)")
async def tenant_extract_acord(
    request: Request,
    file: UploadFile = File(...),
    tenant: Optional[str] = Query(default=None),
):
    tenant_code = _resolve_tenant(request, tenant)
    result = await _execute_tenant_extraction(file, request, "WORK_COMP", tenant_code, WC_OUTPUTS_DIR)
    return JSONResponse(content=result)


@router.post("/extract-lossrun", summary="Loss Run extraction (tenant from header or query)")
async def tenant_extract_lossrun(
    request: Request,
    file: UploadFile = File(...),
    tenant: Optional[str] = Query(default=None),
):
    tenant_code = _resolve_tenant(request, tenant)
    result = await _execute_tenant_extraction(file, request, "INSURANCE", tenant_code, INS_OUTPUTS_DIR)
    return JSONResponse(content=result)


@router.post("/extract-modifier", summary="Extract Experience Modifier (tenant from header or query)")
async def tenant_extract_modifier(
    request: Request,
    file: UploadFile = File(...),
    tenant: Optional[str] = Query(default=None),
    year: int = Query(default=2026),
):
    result = await _execute_modifier_extraction(file, request, year=year)
    return JSONResponse(content=result)


@router.post("/extract", summary="Universal extraction (tenant from header or query)")
async def tenant_extract_universal(
    request: Request,
    file: UploadFile = File(...),
    tenant: Optional[str] = Query(default=None),
    doc_type: Optional[str] = Query(default="WORK_COMP"),
):
    tenant_code = _resolve_tenant(request, tenant)
    hint = (doc_type or "WORK_COMP").upper()
    outputs_root = WC_OUTPUTS_DIR if hint == "WORK_COMP" else INS_OUTPUTS_DIR
    result = await _execute_tenant_extraction(file, request, hint, tenant_code, outputs_root)
    return JSONResponse(content=result)


@router.post("/enrich", summary="Re-enrich an existing extraction session")
async def tenant_enrich(
    request: Request,
    session_output_dir: str = Query(..., description="Absolute path to extraction session directory"),
    doc_type: str = Query(default="WORK_COMP", description="WORK_COMP or INSURANCE"),
    tenant: Optional[str] = Query(default=None),
):
    tenant_code = _resolve_tenant(request, tenant)
    session_path = Path(session_output_dir)

    if not session_path.exists():
        raise HTTPException(status_code=404, detail=f"Session directory not found: {session_output_dir}")

    base_json = session_path / "extracted_schema.json"
    if not base_json.exists():
        raise HTTPException(status_code=404, detail="extracted_schema.json not found in session directory.")

    extracted_text = ""
    text_path = session_path / "extracted_text.txt"
    if text_path.exists():
        try:
            extracted_text = text_path.read_text(encoding="utf-8")
        except Exception:
            pass

    engine = _get_engine(tenant_code)
    try:
        enriched = await run_in_threadpool(
            engine.enrich,
            str(base_json),
            extracted_text,
            doc_type
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Enrichment failed: {e}")

    enriched_path = session_path / f"extracted_schema_{tenant_code.lower()}.json"
    with open(enriched_path, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=2)

    return JSONResponse(content={
        "success": True,
        "tenant": tenant_code,
        "enriched_json_path": str(enriched_path),
        "data": enriched
    })


@router.get("/list", summary="List all configured tenants")
async def tenant_list():
    if not TENANTS_CONFIG_DIR.exists():
        return JSONResponse(content={"count": 0, "tenants": []})
    tenants = []
    for d in TENANTS_CONFIG_DIR.iterdir():
        if not d.is_dir():
            continue
        entry = {"tenant_code": d.name.upper()}
        for fname in ("tenant.json", "extraction.json", "submission.json"):
            fpath = d / fname
            if fpath.exists():
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        entry[fname.replace(".json", "")] = json.load(f)
                except Exception:
                    entry[fname.replace(".json", "")] = {}
        tenants.append(entry)
    return JSONResponse(content={"count": len(tenants), "tenants": tenants})


@router.post("/{tenant}/save", summary="Save unified submission JSON to backend disk")
async def tenant_save_submission(tenant: str, request: Request):
    tenant_code = _resolve_tenant(request, tenant)
    payload = await request.json()

    # Apply the same shared finalize step used by the mail-to-mail flow
    try:
        import sys
        if str(_WORKSPACE_DIR) not in sys.path:
            sys.path.insert(0, str(_WORKSPACE_DIR))
        from core.tenant.loaders import TenantConfigLoader
        from core.submission.payload_transformer import PayloadTransformer

        sub_cfg = TenantConfigLoader(_WORKSPACE_DIR).load_submission_config(tenant_code.lower())
        if sub_cfg.transform_rules.unified_acord_lossrun:
            payload = PayloadTransformer(sub_cfg).finalize_unified_payload(payload, sub_cfg)
    except Exception as e:
        logger.warning(f"[TenantRouter][{tenant_code}] finalize_unified_payload skipped: {e}")
    
    import datetime
    from pathlib import Path
    
    # Path to output directory
    tenant_sub_dir = Path(__file__).resolve().parent.parent.parent / "output" / tenant_code.lower() / "submissions"
    tenant_sub_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    email = payload.get("email", "SYSTEM")
    if not email:
        email = "SYSTEM"
    safe_email = "".join(c if c.isalnum() or c in "._-" else "_" for c in email)
    
    out_file = tenant_sub_dir / f"submission_{timestamp}_{safe_email}.json"
    out_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    
    latest_file = tenant_sub_dir / "latest_submission.json"
    latest_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    
    return JSONResponse(content={"success": True, "saved_path": str(out_file)})
