"""Cost scoring: turn whatever a model panel publishes into one comparable rate.

Two panels, two shapes:

- **credits** — a per-model multiplier such as ``0.69x``. Cheap to read, and
  meaningless once the panel stops publishing it.
- **price** — per-token unit prices (``prompt`` / ``completion`` /
  ``cache_read``). Slower to read, but it is real money and it survives a
  panel redesign.

Reading the wrong shape does not raise an error. It silently produces ``None``
for every model, the cost term drops out of the ranking, and the ranking keeps
looking plausible while it has stopped discriminating. That is the failure this
module exists to prevent: detect the shape once from the local panel, record the
answer, and read the record on later runs so a reinstall on a different machine
does not quietly switch behaviour.

Nothing here talks to a network. The caller supplies the model list; this module
only normalises it and remembers the decision.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

# Prompt tokens dominate sub-agent and review traffic; completion is expensive
# per token but a small share of the count. 3:1 tracks real invoices better than
# a plain average. Cache reads are excluded by default (weight 0) because panels
# report them inconsistently.
DEFAULT_WEIGHTS = (3.0, 1.0, 0.0)
PRICE_KEYS = ("prompt", "completion", "input_cache_read")

# A panel is treated as price-capable when at least this share of models carries
# usable unit prices. Below the floor a single priced model would flip the mode
# for an otherwise credits-only panel.
PRICE_MIN_COVERAGE = 0.5

MODE_FILE = "scoring-mode.json"
CACHE_TTL_SECONDS = 24 * 3600


def _num(value):
    """Accept numbers and numeric strings; ``None`` stays ``None``.

    A published ``0`` is a real price (free tier) and must not be confused with
    a missing field, so this returns ``0.0`` rather than ``None`` for it.
    """
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def blend(pricing, weights=DEFAULT_WEIGHTS):
    """Collapse unit prices into one number, or ``None`` when a required field is missing."""
    if not isinstance(pricing, dict):
        return None
    total_weight = 0.0
    total = 0.0
    for weight, key in zip(weights, PRICE_KEYS):
        value = _num(pricing.get(key))
        if weight > 0 and value is None:
            return None
        total_weight += weight
        total += weight * (value or 0.0)
    if total_weight <= 0:
        return None
    return total / total_weight


def rate(pricing, anchor_pricing, anchor_rate=1.0, weights=DEFAULT_WEIGHTS):
    """Relative multiplier: this model's blended price divided by the anchor's.

    ``anchor_rate`` is the multiplier the anchor is known to carry. Pass ``1.0``
    to work in pure ratios; pass the anchor's published multiplier to keep the
    numbers comparable with an older credits-based table.
    """
    mine = blend(pricing, weights)
    base = blend(anchor_pricing, weights)
    if mine is None or not base:
        return None
    return round(anchor_rate * mine / base, 2)


def detect(models):
    """Decide which shape the panel speaks. Returns ``(mode, evidence)``.

    ``models`` is an iterable of mappings with at least ``pricing``; ``credits``
    is optional. Local or free endpoints may carry no price at all, so the test
    is a coverage share, not a single hit.
    """
    rows = [m for m in (models or []) if isinstance(m, dict)]
    priced = sum(1 for m in rows if blend(m.get("pricing")) is not None)
    rated = sum(1 for m in rows if _num(m.get("credits")) is not None)
    total = len(rows)
    evidence = {
        "models": total,
        "price_coverage": round(priced / total, 2) if total else 0.0,
        "credits_coverage": round(rated / total, 2) if total else 0.0,
    }
    if not total:
        evidence["note"] = "empty model list; keep whatever mode was already recorded"
        return None, evidence
    if priced / total >= PRICE_MIN_COVERAGE:
        return "price", evidence
    if rated / total >= PRICE_MIN_COVERAGE:
        return "credits", evidence
    # Neither shape covers the list. Price still yields numbers for the models
    # that do publish them, while missing credits means no numbers at all.
    evidence["note"] = "neither shape covers the list; falling back to price"
    return "price", evidence


def home() -> Path:
    """Where the remembered decision lives. ``ENBANC_HOME`` overrides the default."""
    return Path(os.environ.get("ENBANC_HOME") or (Path.home() / ".enbanc"))


def mode_path() -> Path:
    return home() / MODE_FILE


def load_mode():
    """Read the recorded decision, or ``None`` when absent or unreadable."""
    path = mode_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and data.get("mode") in ("price", "credits"):
        return data
    return None


def save_mode(mode, evidence=None, source="detected") -> Path:
    path = mode_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mode": mode,
        "source": source,
        "at": time.time(),
        "at_text": time.strftime("%Y-%m-%d %H:%M:%S"),
        "evidence": evidence or {},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8", newline="\n")
    return path


def resolve(models=None, override=None, refresh=False, ttl=CACHE_TTL_SECONDS):
    """Return ``(mode, evidence)`` and record the answer when it was detected now.

    Order of authority:

    1. ``override`` — an explicit request always wins and is recorded.
    2. a fresh record — read back, no probing.
    3. detection — needs ``models``; without it the caller's record (if any) is
       returned unchanged rather than overwritten by a guess.
    """
    recorded = load_mode()
    if override in ("price", "credits"):
        # An explicit request is a decision, not a temporary view: record it so
        # the next automatic run does not silently revert to the old shape.
        evidence = {"forced": True}
        save_mode(override, evidence, source="override")
        return override, evidence
    if recorded and not refresh:
        age = time.time() - float(recorded.get("at") or 0)
        if age < ttl:
            return recorded["mode"], recorded.get("evidence") or {}
    mode, evidence = detect(models)
    if mode is None:
        if recorded:
            return recorded["mode"], recorded.get("evidence") or {}
        return None, evidence
    save_mode(mode, evidence)
    return mode, evidence


def describe(models=None):
    """One line for diagnostics: what mode is in force and why."""
    recorded = load_mode()
    if recorded is None:
        return "scoring: unset (first run detects price or credits from the host's model list)"
    evidence = recorded.get("evidence") or {}
    detail = ""
    if "price_coverage" in evidence:
        detail = (f" (price coverage {evidence['price_coverage']:.0%}, "
                  f"credits coverage {evidence['credits_coverage']:.0%})")
    return f"scoring: {recorded['mode']} [{recorded.get('source', '?')}]{detail}"
