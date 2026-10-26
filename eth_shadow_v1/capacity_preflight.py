"""Conservative offline capacity preflight for Railway Free 24h collection.

Uses actual 3-minute report, not fabricated orderbook sizes. No GET, no DB writes.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING

SECONDS_DAY = 86400
SAMPLE_INTERVAL = 5
EXPECTED = SECONDS_DAY // SAMPLE_INTERVAL
RESERVE = 256 * 1024 * 1024
SAFETY_FACTOR = Decimal("1.25")


def assess_24h_capacity(probe: dict, *, free_bytes: int,
                        offsite_export_configured: bool = False,
                        remote_restore_verified: bool = False) -> dict:
    reasons = []
    count = probe.get("sqlite_book_rows")
    accepted = probe.get("accepted_rows")
    observed = probe.get("sqlite_all_files_bytes")
    total_payload = probe.get("accepted_payload_total_bytes")
    if (type(count) is not int or count < 30 or count > 36
            or type(accepted) is not int or accepted != count
            or type(observed) is not int or observed <= 0
            or type(total_payload) is not int or total_payload <= 0):
        reasons.append("INSUFFICIENT_CALIBRATION")
    audit = probe.get("full_chain_audit")
    if (not isinstance(audit, dict) or audit.get("status") != "PASS"
            or probe.get("cause") != "TIMEBOX_COMPLETED"
            or probe.get("status") != "REVIEW_REQUIRED"):
        reasons.append("UNVERIFIED_PROBE")
    if type(free_bytes) is not int or free_bytes < 0:
        reasons.append("INVALID_VOLUME_FREE_SPACE")
    projected = conservative = required_free = extra_free_needed = None
    headroom = max(0, free_bytes - RESERVE) if type(free_bytes) is int else None
    if not reasons:
        # Use total SQLite footprint including schema/index/WAL, not only the
        # smaller raw payload. Scaling even initial fixed costs is conservative.
        projected = int((Decimal(observed) * EXPECTED / Decimal(count)).to_integral_value(rounding=ROUND_CEILING))
        conservative = int((Decimal(projected) * SAFETY_FACTOR).to_integral_value(rounding=ROUND_CEILING))
        required_free = RESERVE + conservative
        extra_free_needed = max(0, required_free - free_bytes)
        if extra_free_needed:
            reasons.append("25PCT_MARGIN_EXCEEDS_DISK_HEADROOM")
    if not offsite_export_configured:
        reasons.append("OFFSITE_DESTINATION_NOT_CONFIGURED")
    if not remote_restore_verified:
        reasons.append("OFFSITE_RESTORE_NOT_VERIFIED")
    reasons.append("24H_CONTINUITY_UNTESTED")
    return {
        "schema": "revx-24h-capacity-assessment-v1",
        "status": "NO_GO",  # never automatically authorizes capture
        "source": "MEASURED_3MIN_PUBLIC_GET",
        "observed_books": count,
        "observed_payload_bytes": total_payload,
        "observed_sqlite_footprint_bytes": observed,
        "target_books": EXPECTED,
        "projected_db_bytes": projected,
        "projected_db_with_25pct_margin_bytes": conservative,
        "free_volume_bytes": free_bytes,
        "required_reserve_bytes": RESERVE,
        "budget_above_reserve_bytes": headroom,
        "required_free_volume_bytes": required_free,
        "additional_free_bytes_needed": extra_free_needed,
        "capacity_margin_pass": extra_free_needed == 0 if extra_free_needed is not None else False,
        "backup_architecture": "TEMP_SCRATCH_TO_REMOTE_HTTPS_NO_DUPLICATE_ON_VOLUME",
        "local_full_backup_required": False,
        "offsite_export_configured": offsite_export_configured,
        "remote_restore_verified": remote_restore_verified,
        "reasons": sorted(set(reasons)),
        "economic_approval": "NO_GO",
        "live_trading": "DISABLED",
    }
