#!/usr/bin/env python3
"""Validate an ordinary ASEF draft against independent expectations; no DB/network."""
import argparse
import json
import sys
from pathlib import Path
from decimal import Decimal
from asef.invoice import build_invoice, invoice_summary

def require(condition, message):
    if not condition:
        raise ValueError(message)

def check(payload, expected):
    allowed = {"number","issue_date","currency","seller","buyer","lines",
               "issue_place","sale_date","payment_due_date","payment_method","bank_account"}
    require(isinstance(payload, dict), "Payload must be an object")
    require(not set(payload) - allowed, "Unsupported payload fields")
    require(all(k in expected for k in ("seller_nip","buyer_nip","currency","net_amount","vat_amount","gross_amount")),
            "Missing independent expected parties/currency/totals")
    for role in ("seller","buyer"):
        require(payload[role]["nip"] == expected[role+"_nip"], role+" NIP mismatch")
        if role in expected:
            require(payload[role] == expected[role], role+" data mismatch")
    for key in ("number","issue_date","currency"):
        if key in expected:
            require(payload.get(key) == expected[key], key+" mismatch")
    optional = {"issue_place","sale_date","payment_due_date","payment_method","bank_account"}
    approved = expected.get("optional_fields", {})
    require(isinstance(approved,dict) and set(approved) <= optional, "Invalid optional expectations")
    for key in optional:
        require((key in payload) == (key in approved), "Unexpected or missing optional field: "+key)
        if key in approved:
            require(payload[key] == approved[key], "Optional field mismatch: "+key)
    rows = payload["lines"]
    require(isinstance(rows,list) and len(rows)>0,"Missing lines")
    if "line_count" in expected:
        require(len(rows)==expected["line_count"],"Line count mismatch")
    descriptions=[]
    for line in rows:
        require(isinstance(line,dict) and not set(line)-{"description","quantity","unit","unit_price_net","vat_rate"}, "Unsupported line fields")
        description=line["description"]
        require(isinstance(description,str) and description.strip(), "Empty description")
        require(len(description)<=expected.get("max_description_chars",100),"Description too long")
        rate=line.get("vat_rate")
        require(type(rate) in (int,str) and str(rate) in ("23","8","5"),"VAT must be 23, 8 or 5 without percent sign")
        descriptions.append(description)
    joined=" ".join(descriptions)
    for term in ([expected["source_number"]] if expected.get("source_number") else []) + expected.get("description_terms",[]):
        require(term in joined,"Missing description/source term")
    xml=build_invoice(payload)
    summary=invoice_summary(xml)
    for key in ("net_amount","vat_amount","gross_amount"):
        require(Decimal(summary[key])==Decimal(str(expected[key])),key+" mismatch")
    require(summary["currency"]==expected["currency"],"Currency mismatch")
    return xml,summary,descriptions

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload",type=Path)
    parser.add_argument("--expected",type=Path,required=True)
    parser.add_argument("--xml-out",type=Path)
    args=parser.parse_args()
    try:
        payload=json.loads(args.payload.read_text())
        expected=json.loads(args.expected.read_text())
        xml,summary,descriptions=check(payload,expected)
        if args.xml_out:
            with args.xml_out.open("xb") as handle:
                handle.write(xml)
        result={"ok":True,"validation":"FA(3) and independent expectations",
                "summary":summary,"description_lengths":[len(x) for x in descriptions],
                "xml_out":str(args.xml_out) if args.xml_out else None,
                "database_changed":False,"network_used":False}
        print(json.dumps(result,ensure_ascii=False))
        return 0
    except (ValueError,KeyError,TypeError,OSError,ArithmeticError) as exc:
        print(json.dumps({"ok":False,"error_type":type(exc).__name__,"error":str(exc)},ensure_ascii=False))
        return 1

if __name__=="__main__":
    sys.exit(main())
