#!/usr/bin/env python3
"""Validate docs/legal/processors.json (C04, DP-9). Stdlib only, offline.

Fails (exit 1) when
  * a processor lacks a required field or has an empty value / bad enum;
  * an integration from SR section 9 / the ADRs has no entry;
  * docs/ops/secrets-inventory.json (F09) holds a server_secret or public_client_key that no
    processor lists in secret_names, or a name is claimed by two processors;
  * an id is missing from processor-register.md.

Usage: python3 docs/legal/check-processors.py [processors.json] [--inventory PATH] [--no-inventory]
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_INVENTORY = HERE.parent / "ops" / "secrets-inventory.json"

REQUIRED_FIELDS = [
    "id", "name", "role", "phase", "purpose", "data_categories", "regions", "cross_border",
    "cross_border_note", "secret_names", "agreement_status", "owner_blocked", "dpa_link",
    "sub_processors_note", "exit_procedure", "if_dpa_refused", "breach_contact",
]
NONEMPTY = ["id", "name", "role", "phase", "purpose", "data_categories", "regions", "cross_border_note",
            "dpa_link", "sub_processors_note", "exit_procedure", "if_dpa_refused", "breach_contact"]
DATA_CATEGORIES = {
    "child-data", "parent-data", "teacher-data", "teacher-documents", "phone", "email", "location",
    "payment-data", "safeguarding", "device-identifiers", "credentials-tokens", "logs-metadata",
    "source-code-only",
}
AGREEMENT_STATUS = {"not signed", "in negotiation", "signed", "risk-accepted", "not required"}
# SR section 9 integrations plus hosting/backend (ADR 0003, 0012), monitoring (F08), source (GitHub).
REQUIRED_IDS = ["codemagic", "apple-app-store", "google-play", "appwrite-cloud", "fly-io", "resend", "africas-talking", "firebase-fcm-apns", "google-maps",
                "sentry", "mtn-momo", "airtel-money", "card-gateway", "uptime-vendor", "github"]
INVENTORY_KINDS = {"server_secret", "public_client_key"}
# Inventory consumers that are third-party vendors' consoles/services: a secret used there can never be
# declared first-party. Likewise secrets whose name starts with a vendor prefix.
VENDOR_CONSUMERS = {"app-stores", "codemagic", "firebase-console"}
VENDOR_NAME_PREFIXES = ("APPWRITE_", "FLY_", "RESEND_", "AFRICASTALKING_", "FCM_", "APNS_", "GOOGLE_MAPS_",
                        "SENTRY_", "MOMO_", "AIRTEL_", "CARD_GATEWAY_", "HS_HEARTBEAT", "PLAY_", "IOS_",
                        "APP_STORE_", "CODEMAGIC_")


def validate(register, inventory=None, register_md=None, check_stale=False):
    errors = []
    procs = register.get("processors")
    if not isinstance(procs, list) or not procs:
        return ["processors.json: 'processors' must be a non-empty list"]
    seen, claimed = set(), {}
    for p in procs:
        pid = p.get("id", "?")
        if pid in seen:
            errors.append(f"{pid}: duplicate id")
        seen.add(pid)
        for f in REQUIRED_FIELDS:
            if f not in p:
                errors.append(f"{pid}: missing field {f}")
        for f in NONEMPTY:
            if f in p and (p[f] in ("", [], None) or (isinstance(p[f], str) and not p[f].strip())):
                errors.append(f"{pid}: empty {f}")
        for c in p.get("data_categories") or []:
            if c not in DATA_CATEGORIES:
                errors.append(f"{pid}: unknown data category '{c}'")
        st = p.get("agreement_status")
        if "agreement_status" in p and st not in AGREEMENT_STATUS:
            errors.append(f"{pid}: agreement_status '{st}' not in {sorted(AGREEMENT_STATUS)}")
        if st == "signed":
            link, date = str(p.get("dpa_link", "")), p.get("signed_date")
            if not link.startswith(("http://", "https://")) or not date:
                errors.append(f"{pid}: status signed needs a real dpa_link (http/https) and signed_date")
        if st == "risk-accepted" and not p.get("risk_acceptance_ref"):
            errors.append(f"{pid}: status risk-accepted needs risk_acceptance_ref (dated owner decision)")
        if st in ("not signed", "in negotiation") and p.get("owner_blocked") is not True:
            errors.append(f"{pid}: unsigned agreement must be marked owner_blocked true")
        if p.get("cross_border") is True and not str(p.get("cross_border_note", "")).strip():
            errors.append(f"{pid}: cross_border_note required when cross_border is true")
        if "cross_border" in p and not isinstance(p["cross_border"], bool):
            errors.append(f"{pid}: cross_border must be true or false")
        if "unverified" not in str(p.get("cross_border_note", "")).lower():
            errors.append(f"{pid}: cross_border_note must label legal claims 'unverified, for lawyer confirmation'")
        for n in p.get("secret_names") or []:
            claimed.setdefault(n, []).append(pid)
    for need in REQUIRED_IDS:
        if need not in seen:
            errors.append(f"{need}: required processor entry missing (SR section 9 / ADRs)")
    for n, owners in claimed.items():
        if len(set(owners)) > 1:
            errors.append(f"{n}: claimed by more than one processor {sorted(set(owners))}")
    first_party = {}
    for fp in register.get("first_party_secrets", []):
        n = fp.get("name", "?")
        first_party[n] = fp
        if not str(fp.get("reason", "")).strip():
            errors.append(f"{n}: first_party_secrets entry needs a reason")
        if n in claimed:
            errors.append(f"{n}: listed as first-party and also held by a processor (both is wrong)")
        if n.startswith(VENDOR_NAME_PREFIXES):
            errors.append(f"{n}: vendor-prefixed name cannot be declared first-party")
    if inventory is not None:
        names = set()
        for e in inventory.get("entries", []):
            names.add(e["name"])
            if e["name"] in first_party and e.get("consumer") in VENDOR_CONSUMERS:
                errors.append(f"{e['name']}: consumer {e['consumer']} is a vendor; cannot be first-party, a processor must hold it")
            if e.get("kind") in INVENTORY_KINDS and e["name"] not in claimed and e["name"] not in first_party:
                errors.append(f"{e['name']}: in secrets inventory (consumer {e.get('consumer')}) but no processor lists it in secret_names")
        if check_stale:
            for n in first_party:
                if n not in names:
                    errors.append(f"{n}: first-party secret not in the secrets inventory (stale)")
            for n, owners in claimed.items():
                if n not in names:
                    errors.append(f"{n}: listed by {owners[0]} but not in the secrets inventory (stale)")
    if register_md is not None:
        for pid in sorted(seen):
            if f"`{pid}`" not in register_md:
                errors.append(f"{pid}: not mentioned (as `{pid}`) in processor-register.md")
    return errors


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    inv_path, use_inv = DEFAULT_INVENTORY, True
    if "--no-inventory" in args:
        args.remove("--no-inventory")
        use_inv = False
    if "--inventory" in args:
        i = args.index("--inventory")
        inv_path = Path(args[i + 1])
        del args[i:i + 2]
    path = Path(args[0]) if args else HERE / "processors.json"
    register = json.loads(path.read_text(encoding="utf-8"))
    md_path = path.with_name("processor-register.md")
    md = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
    inventory = None
    if use_inv and inv_path.exists():
        inventory = json.loads(inv_path.read_text(encoding="utf-8"))
    errors = validate(register, inventory, md, check_stale=inventory is not None)
    for e in errors:
        print("FAIL:", e)
    if errors:
        return 1
    print(f"ok: {path.name} ({len(register['processors'])} processors; inventory {'checked' if inventory else 'not present, skipped'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
