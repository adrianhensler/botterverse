from __future__ import annotations

import hashlib
import hmac
import json
import secrets


def unsigned_payload(payload: dict) -> dict:
    metadata = dict(payload.get("metadata", {}))
    metadata.pop("signature", None)
    return {**payload, "metadata": metadata}


def export_signature(payload: dict, secret: str) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def attach_signature(payload: dict, secret: str) -> None:
    signature = export_signature(unsigned_payload(payload), secret)
    payload.setdefault("metadata", {})["signature"] = {
        "algorithm": "hmac-sha256",
        "digest": signature,
    }


def verify_signature(payload: dict, secret: str) -> None:
    metadata = payload.get("metadata", {})
    signature = metadata.get("signature")
    if not signature:
        return
    expected = export_signature(unsigned_payload(payload), secret)
    digest = signature.get("digest")
    if not digest or not secrets.compare_digest(expected, digest):
        raise ValueError("export signature verification failed")
