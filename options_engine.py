"""Compatibility wrapper for the dashboard's existing options_engine import.

The implementation is the reviewed V2 research/paper engine. It does not place
orders and must not be treated as a live execution validator.
"""
from options_engine_v2 import get_option_analysis, INDEX_EXCHANGES

__all__ = ["get_option_analysis", "INDEX_EXCHANGES"]
