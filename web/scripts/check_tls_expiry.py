"""Read PEM certificate validity without opening a key, socket or database."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys

from cryptography import x509
from cryptography.hazmat.primitives import hashes


def inspect_certificates(path: Path, minimum_days: int, *, now: datetime | None = None) -> list[dict]:
    if type(minimum_days) is not int or not 0 <= minimum_days <= 3650:
        raise ValueError("minimum_days must be an integer in 0..3650")
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("certificate file exceeds 2 MiB")
    raw = path.read_bytes()
    if b"PRIVATE KEY-----" in raw:
        raise ValueError("certificate inspection refuses a file containing a private key")
    blocks = re.findall(br"-----BEGIN CERTIFICATE-----\s+.*?-----END CERTIFICATE-----", raw, re.S)
    if not blocks or len(blocks) > 64:
        raise ValueError("expected 1..64 PEM certificates")
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ValueError("now must include a timezone")
    deadline = moment + timedelta(days=minimum_days)
    result = []
    for index, block in enumerate(blocks):
        cert = x509.load_pem_x509_certificate(block)
        start, end = cert.not_valid_before_utc, cert.not_valid_after_utc
        state = "not_yet_valid" if moment < start else "expired" if moment >= end else "expiring" if end <= deadline else "valid"
        result.append({
            "certificate_index": index,
            "sha256": cert.fingerprint(hashes.SHA256()).hex(),
            "not_before": start.isoformat(),
            "not_after": end.isoformat(),
            "remaining_days": round((end - moment).total_seconds() / 86400, 3),
            "status": state,
        })
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cert", type=Path, action="append", required=True,
                        help="PEM leaf/chain/CA file; repeat to inspect separate files")
    parser.add_argument("--minimum-days", type=int, default=30,
                        help="Fail when any certificate expires within this many days (default: 30)")
    args = parser.parse_args(argv)
    reports = []
    try:
        for path in args.cert:
            reports.append({"file": str(path), "certificates": inspect_certificates(path, args.minimum_days)})
    except (OSError, ValueError, OverflowError):
        print(json.dumps({"ok": False, "error": "certificate_input_invalid_or_unreadable"}))
        return 2
    passed = all(cert["status"] == "valid" for report in reports for cert in report["certificates"])
    print(json.dumps({"ok": passed, "minimum_days": args.minimum_days, "files": reports,
                      "scope": "Validity dates only; no chain, hostname, revocation or private-key verification"},
                     ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
