"""Unified runtime coordination layer.

The UI reads this layer instead of independently polling semantic, player,
map, and transport decoders.  This keeps BizHawk RAM access single-flight and
separates transport health from semantic decode health.
"""
