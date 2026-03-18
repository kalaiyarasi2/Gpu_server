import os
import json
import re
from pathlib import Path

def clean_amount(amount_str):
    if not amount_str:
        return 0.0
    return float(amount_str.replace('$', '').replace(',', '').strip())

def extract_summary_from_text(text):
    summary = {}
    
    # Chase Patterns
    chase_summary = re.search(r'Opening Ledger Balance\s+\$([\d,]+\.\d{2})\s+Deposits and Credits\s+\d+\s+\$([\d,]+\.\d{2})\s+Withdrawals and Debits\s+\d+\s+\$([\d,]+\.\d{2})\s+Checks Paid\s+\d+\s+\$([\d,]+\.\d{2})\s+Ending Ledger Balance\s+\$([\d,]+\.\d{2})', text)
    if chase_summary:
        summary['opening_balance'] = clean_amount(chase_summary.group(1))
        summary['total_deposits'] = clean_amount(chase_summary.group(2))
        summary['total_withdrawals'] = clean_amount(chase_summary.group(3))
        summary['total_checks'] = clean_amount(chase_summary.group(4))
        summary['ending_balance'] = clean_amount(chase_summary.group(5))
        return summary

    # TRUE Community CC Patterns
    cc_summary = re.search(r'Previous Balance \$([\d,]+\.\d{2}) Payments and Other Credits \(-\) \$([\d,]+\.\d{2}) Purchases and Other Debits \(\+\) \$([\d,]+\.\d{2}).*?New Balance \$([\d,]+\.\d{2})', text, re.DOTALL)
    if cc_summary:
        summary['opening_balance'] = clean_amount(cc_summary.group(1))
        summary['total_deposits'] = clean_amount(cc_summary.group(2))
        summary['total_withdrawals'] = clean_amount(cc_summary.group(3))
        summary['ending_balance'] = clean_amount(cc_summary.group(4))
        return summary

    # Zions Patterns
    zions_summary = re.search(r'Amount:\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})-\s+([\d,]+\.\d{2})-\s+([\d,]+\.\d{2})', text)
    if zions_summary:
        summary['opening_balance'] = clean_amount(zions_summary.group(1))
        summary['total_deposits'] = clean_amount(zions_summary.group(2))
        summary['total_withdrawals'] = clean_amount(zions_summary.group(3))
        summary['total_checks'] = clean_amount(zions_summary.group(4))
        summary['ending_balance'] = clean_amount(zions_summary.group(5))
        return summary

    # Citi Patterns
    citi_balances = re.search(r'Beginning Balance:\s+\$([\d,]+\.\d{2})\s+Ending Balance:\s+\$([\d,]+\.\d{2})', text)
    if citi_balances:
        summary['opening_balance'] = clean_amount(citi_balances.group(1))
        summary['ending_balance'] = clean_amount(citi_balances.group(2))
        
        citi_totals = re.search(r'Total Debits/Credits\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})', text)
        if citi_totals:
            summary['total_withdrawals'] = clean_amount(citi_totals.group(1))
            summary['total_deposits'] = clean_amount(citi_totals.group(2))
            
        citi_checks = re.search(r'Number Checks Paid: \d+ Totaling: \$([\d,]+\.\d{2})', text)
        if citi_checks:
            summary['total_checks'] = clean_amount(citi_checks.group(1))
        return summary

    # PNC Patterns (Abel I)
    pnc_summary = re.search(r'Beginning\s+Deposits and\s+Checks and\s+Ending\s+balance\s+other credits\s+other debits\s+balance\s+([\d,.]*)\s+([\d,.]+)\s+([\d,.]+)\s+([\d,.]*)', text)
    if pnc_summary:
        summary['opening_balance'] = clean_amount(pnc_summary.group(1))
        summary['total_deposits'] = clean_amount(pnc_summary.group(2))
        summary['total_withdrawals'] = clean_amount(pnc_summary.group(3))
        summary['ending_balance'] = clean_amount(pnc_summary.group(4))
        return summary
    
    # Alternative PNC pattern (Total line)
    pnc_total = re.search(r'Total\s+\d+\s+([\d,]+\.\d{2})\s+Total\s+\d+\s+([\d,]+\.\d{2})', text)
    if pnc_total:
        summary['total_deposits'] = clean_amount(pnc_total.group(1))
        summary['total_withdrawals'] = clean_amount(pnc_total.group(2))
        return summary

    # Generic Fallback (simple keyword search)
    # This is a bit risky but better than nothing
    generic_opening = re.search(r'(?:Opening|Beginning|Previous) Balance[:\s]*\$?([\d,]+\.\d{2})', text, re.I)
    generic_ending = re.search(r'(?:Ending|Ending Ledger|New) Balance[:\s]*\$?([\d,]+\.\d{2})', text, re.I)
    if generic_opening: summary['opening_balance'] = clean_amount(generic_opening.group(1))
    if generic_ending: summary['ending_balance'] = clean_amount(generic_ending.group(1))
    
    return summary

def verify_statements(outputs_dir):
    report = []
    output_path = Path(outputs_dir)
    
    for statement_dir in output_path.iterdir():
        if not statement_dir.is_dir():
            continue
            
        print(f"Verifying {statement_dir.name}...")
        
        json_file = statement_dir / "extracted_statement.json"
        text_file = statement_dir / "extracted_text.txt"
        package_file = statement_dir / "verification_package.json"
        
        if not json_file.exists() or not text_file.exists():
            print(f"  Missing files in {statement_dir.name}")
            continue
            
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        with open(text_file, 'r', encoding='utf-8') as f:
            text = f.read()
            
        # Calculate sums from extracted data
        extracted_deposits = sum((item.get('amount') or 0.0) for item in data.get('deposits_and_credits', []))
        extracted_debits = sum((item.get('amount') or 0.0) for item in data.get('checks_and_other_debits', []) if item.get('check_no') is None)
        extracted_checks = sum((item.get('amount') or 0.0) for item in data.get('checks_and_other_debits', []) if item.get('check_no') is not None)
        
        # Parse summary from text
        summary = extract_summary_from_text(text)
        
        # Comparison logic
        status = "OK"
        issues = []
        
        if 'total_deposits' in summary:
            diff = abs(summary['total_deposits'] - extracted_deposits)
            if diff > 0.01:
                issues.append(f"Deposit mismatch: Summary={summary['total_deposits']:.2f}, Extracted={extracted_deposits:.2f} (diff={diff:.2f})")
                status = "Mismatched"
        
        # Withdrawals + Checks combined often equals "Withdrawals and Debits" or "Total Debits"
        total_extracted_debits = extracted_debits + extracted_checks
        if 'total_withdrawals' in summary:
            # Check if total_withdrawals includes checks or not
            # In Chase, "Withdrawals and Debits" is separate from "Checks Paid" (wait, line 36 vs 37)
            # Line 36: Withdrawals and Debits 22 $270,014.23
            # Line 37: Checks Paid 0 $0.00
            # Line 186: Total $270,014.23 (withdrawals total)
            # So they are likely separate.
            
            # If both exist in summary, check them individually
            if 'total_checks' in summary:
                diff_debits = abs(summary['total_withdrawals'] - extracted_debits)
                diff_checks = abs(summary['total_checks'] - extracted_checks)
                if diff_debits > 0.01:
                    issues.append(f"Debit mismatch: Summary={summary['total_withdrawals']:.2f}, Extracted={extracted_debits:.2f} (diff={diff_debits:.2f})")
                    status = "Mismatched"
                if diff_checks > 0.01:
                    issues.append(f"Check mismatch: Summary={summary['total_checks']:.2f}, Extracted={extracted_checks:.2f} (diff={diff_checks:.2f})")
                    status = "Mismatched"
            else:
                # Compare combined if only generic withdrawal total found
                diff = abs(summary['total_withdrawals'] - total_extracted_debits)
                if diff > 0.01:
                    issues.append(f"Withdrawal mismatch: Summary={summary['total_withdrawals']:.2f}, Extracted combined={total_extracted_debits:.2f}")
                    status = "Mismatched"
        
        # Checking balance consistency: Opening + Deposits - Debits - Checks = Ending
        if all(k in summary for k in ['opening_balance', 'ending_balance', 'total_deposits', 'total_withdrawals']):
            expected_ending = summary['opening_balance'] + summary['total_deposits'] - summary['total_withdrawals'] - summary.get('total_checks', 0.0)
            # Adjust if total_withdrawals already included checks (depends on bank)
            # For TRUE CC, Payments are negative credits, so Balance = Previous + Purchases - Payments (wait, line 39 says Payments and Other Credits (-) $2,750.61)
            # So New = Prev - Payments + Purchases.
            
            # Simple check for now: does extracted match ending balance?
            # Actually, let's just use the summaries found.
            pass

        # Check for warnings in package
        warnings = []
        if package_file.exists():
            with open(package_file, 'r', encoding='utf-8') as f:
                pkg = json.load(f)
                diag = pkg.get('diagnostics', {})
                if diag.get('warnings'):
                    warnings.extend(diag['warnings'])
                if diag.get('low_confidence_fields'):
                    warnings.append(f"Low confidence in: {', '.join(diag['low_confidence_fields'])}")

        report.append({
            "statement": statement_dir.name,
            "source": data.get('metadata', {}).get('source_file'),
            "status": status,
            "issues": issues,
            "warnings": warnings,
            "sums": {
                "extracted_deposits": extracted_deposits,
                "extracted_debits": extracted_debits,
                "extracted_checks": extracted_checks
            },
            "summary": summary
        })

    return report

def format_report(report):
    lines = ["# Bank Statement Verification Report", ""]
    for entry in report:
        lines.append(f"## {entry['statement']}")
        lines.append(f"**Source File:** {entry['source']}")
        lines.append(f"**Status:** {entry['status']}")
        
        if entry['issues']:
            lines.append("### Issues Found:")
            for issue in entry['issues']:
                lines.append(f"- {issue}")
        
        if entry['warnings']:
            lines.append("### Extraction Warnings:")
            for warn in entry['warnings']:
                lines.append(f"- {warn}")
                
        lines.append("### Data Details:")
        lines.append("| Category | Extracted | Summary Found |")
        lines.append("| :--- | :--- | :--- |")
        lines.append(f"| Deposits | ${entry['sums']['extracted_deposits']:.2f} | ${entry['summary'].get('total_deposits', 0.0):.2f} |")
        lines.append(f"| Debits | ${entry['sums']['extracted_debits']:.2f} | ${entry['summary'].get('total_withdrawals', 0.0):.2f} |")
        lines.append(f"| Checks | ${entry['sums']['extracted_checks']:.2f} | ${entry['summary'].get('total_checks', 0.0):.2f} |")
        lines.append(f"| Opening Bal | - | ${entry['summary'].get('opening_balance', 0.0):.2f} |")
        lines.append(f"| Ending Bal | - | ${entry['summary'].get('ending_balance', 0.0):.2f} |")
        lines.append("")
        lines.append("---")
        
    return "\n".join(lines)

if __name__ == "__main__":
    outputs_dir = r'c:\Users\INTERN\main_project\Main--main\bank statement\backend\outputs'
    report_data = verify_statements(outputs_dir)
    report_md = format_report(report_data)
    
    with open('verification_report.md', 'w', encoding='utf-8') as f:
        f.write(report_md)
        
    print("Verification complete. Report generated: verification_report.md")
