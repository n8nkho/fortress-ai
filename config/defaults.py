"""Fortress AI default configuration constants (env vars override at runtime)."""

from config.risk_params import FORTRESS_MAX_ORDER_NOTIONAL_USD, FORTRESS_MAX_POSITION_NOTIONAL_USD

ENFORCE_POSITION_DEDUPLICATION: bool = True
POSITION_DEDUPLICATION_ENABLED: bool = True
FLATTEN_LEGACY_ON_STARTUP: bool = True
