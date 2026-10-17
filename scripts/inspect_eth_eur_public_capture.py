"""Replay ONE archived public capture locally against isolated shadow state.

This never calls the collector or any network API and never implies 30-day proof.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from eth_shadow_v1.config import Config
from eth_shadow_v1.grid import PairRules
from eth_shadow_v1.journal import Journal, canonical
from eth_shadow_v1.market import parse_book

CONFIG = Path(__file__).resolve().parent.parent / "configs" / "eth_eur_grid_shadow_v1.toml"


def inspect_capture(data: dict, cfg: Config, journal_path: Path) -> dict:
    """Verify manifest, source metadata, actual pair rules and replay bounded book events."""
    if not isinstance(data, dict) or data.get("mode") != "READ_ONLY_CAPTURE":
        raise ValueError("CAPTURE_MODE_INVALID")
    if data.get("config_hash") != cfg.sha256:
        raise ValueError("CAPTURE_CONFIG_CHANGED")
    digest = data.get("capture_sha256")
    unsigned = {k: v for k,v in data.items() if k != "capture_sha256"}
    if (not isinstance(digest, str)
        or digest != hashlib.sha256(canonical(unsigned).encode()).hexdigest()):
        raise ValueError("CAPTURE_HASH_MISMATCH")
    events = data.get("events")
    if (not isinstance(events, list) or not (1 <= len(events) <= 3)
            or data.get("errors") or data.get("region_requested") != "EEA"
            or data.get("symbol") != "ETH-EUR" or data.get("synthetic") is not False):
        raise ValueError("CAPTURE_INCOMPLETE_OR_UNTRUSTED")
    if not isinstance(data.get("candles"), list) or len(data["candles"]) < 200:
        raise ValueError("CAPTURE_CANDLES_MISSING")
    for event in events:
        if event.get("candles") != data["candles"]:
            raise ValueError("CAPTURE_CANDLE_PROVENANCE_DIFFERENT")
        parse_book(event, cfg)
    first = events[0]
    first_book = parse_book(first, cfg)
    rules = PairRules.from_public_config(
        data["pair_configuration"], resolved_region=first["metadata"]["region"],
        book_mid=first_book.mid)
    if not rules.active():
        raise ValueError("CAPTURE_PAIR_NOT_ACTIVE")
    # This is an offline analysis of a captured file, not an authenticated
    # market-data source of truth. No continuous collector is activated.
    with Journal(journal_path, "eth-eur-public-capture-readonly", cfg, rules) as journal:
        for event in events:
            journal.process(event)
        report = journal.report()
    report.update({
        "input_observations": len(events),
        "capture_sha256": digest,
        "pair_current_base_step": str(rules.base_step),
        "pair_current_quote_step": str(rules.quote_step),
        "pair_current_min_base": str(rules.min_base_qty),
        "pair_current_max_base": str(rules.max_base_qty),
        "pair_current_min_quote_eur": str(rules.min_notional),
        "validation_30d": "NOT_RUN",
        "adapter_type": "OFFLINE_REPLAY_OF_SAVED_PUBLIC_GET",
    })
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline public capture inspection; no network")
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = inspect_capture(
        json.loads(args.capture.read_text(encoding="utf-8")),
        Config.load(CONFIG), args.journal,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    print("OFFLINE_CAPTURE_REPLAY " + json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
