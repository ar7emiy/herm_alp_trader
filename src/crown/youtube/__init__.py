"""Stub for the v1 build: YouTube transcript context is out of scope for the
letter-only backtest (per the agreed v1 scope). A future ContextSource
interface would live here — parse_letter's Claude call would take an
optional list of pre-letter-date video transcripts as extra context,
matching the "replay must not leak post-simulated-time information" rule
that also governs why web search is disabled during backtests.
"""
