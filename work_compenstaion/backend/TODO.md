# PremiumCalculation Fix Plan - Approved
Status: ✅ Approved by user | Now Implementing

## Step-by-Step Execution Plan

### Step 1: ✅ Aligned schema.txt ✓

### Step 2: ✅ Enhanced work_compensation.py prompt & post-process ✓

### Step 3: [TODO] Implement table preprocessing (TODO Phase 1)
- Add `_preprocess_tables()`: Markdown → structured for ratingByState/priors

### Step 4: [TODO] Update chunked_extractor.py merge logic
- Sum premiums across chunks in `_merge_chunks()`

### Step 5: [TODO] Re-extract this PDF
- cd server_2/drive_server2/work_compenstaion/backend
- Run: python work_compensation.py \"../Unified_PDF_Platform/uploads/A Total Solutions, Inc. - Acord.pdf\"
- Verify: totalEstimatedAnnualPremium ≈893.0 (181+712)

### Step 6: [TODO] Test BRIDGEFORD PDF (TODO #7)
- Verify ratings/premiums populated

### Step 7: [TODO] Mark complete & attempt_completion

**Next Action**: Implement Step 1 → edit schema.txt
**Progress**: 0/7 complete
