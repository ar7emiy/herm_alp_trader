# 14 - Robinhood MCP and data-source implementation research

Researched 2026-10-19 against official documentation. Robinhood is not connected; no
account, tool response, entitlement, authentication session, or latency has been tested.
Recommendations below are a proposed integration, not confirmed account capabilities.

## Official connection

Robinhood documents its Trading MCP at `https://agent.robinhood.com/mcp/trading`, including
Streamable HTTP setup and interactive authentication. Its onboarding may require a
dedicated MCP account. A connection can expose private account data and trading tools;
it must not be assumed to be a public, read-only data API.
[Official onboarding](https://robinhood.com/us/en/support/articles/agentic-trading-overview/).

Use the official server. No scraped credentials, password automation, or unofficial
Robinhood client is needed. Connecting it in an IDE is useful for exploration, but does
not establish that a Python worker on a VPS has an authorized, renewable session.

## Documented tools relevant to this system

The external-agent catalog lists equity news, quotes/history, financials/fundamentals,
earnings calendars/results, SEC filings, index quotes/history, and option chains,
quotes/history. Relevant names include `get_equity_news`, `get_equity_quotes`,
`get_equity_historicals`, `get_earnings_calendar`, `get_earnings_results`,
`get_index_quotes`, `get_index_historicals`, `get_option_chains`, and `get_option_quotes`.
Earnings transcripts are not marked available to external agents in the catalog.
[Official tool catalog](https://robinhood.com/us/en/support/articles/trading-with-your-agent/).

These are promising for validating events, monitoring index levels, researching ETF
candidates, and reviewing news tied to an existing Crown thesis. This does not establish
that the news is complete, quotes are consolidated/executable, or historical options data
is sufficient to reconstruct old bid/ask markets. Do not promise institutional COT,
dealer-gamma, futures, FX, or economic-surprise coverage without an actual supported feed.

## Proposed research adapter

1. Establish an official authorized session using the server's supported flow. For a
   standalone client, discover authorization metadata and use standard token handling;
   confirm the server's supported refresh behavior. The MCP authorization standard defines
   the HTTP/OAuth discovery and security contract, not Robinhood-specific entitlements.
   [MCP authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization).
2. Discover and version the real tool list and schemas. Build a positive allowlist of
   research tools. Reject every other call before dispatch, including trading, exercising,
   cancellation, account-setting changes, and watchlist mutation. The LLM receives our
   typed research interface, never a generic arbitrary MCP-call tool.
3. Keep tokens outside git and out of LLM input. Use the minimum available scope; if the
   server lacks a data-only scope, enforce the narrow interface in code and disclose the
   underlying connection's broader access. Avoid fetching personal portfolio data.
4. Normalize outputs into internal Quote, Bar, NewsItem, EarningsEvent, ReferenceLevel,
   and ProviderHealth records. Retain source identifiers, units, instrument identity,
   observation/publication/receipt times, feed class, and licensing/retention metadata.
5. Cache and batch by data type. Deduplicate articles across providers and preserve
   corrections. Repeated copies of one syndicated story are not independent evidence.
6. Enforce freshness, timeouts, bounded retries/backoff, rate budgets, and a circuit
   breaker. Token expiry creates a visible reconnect-needed state; no infinite login loop.
7. Compare sample quotes, bars, adjusted prices, and timestamps with Alpaca. Never mix
   index levels, ETF prices, currency quote conventions, and futures contract units.
8. Prove operation after worker restart and session expiry on the VPS. Confirm permitted
   personal automated use, retention, and cross-broker use before making it a required
   production source. Do not assume that a retail MCP connection is an unrestricted feed.

Acceptance: known tool schemas and data freshness, authentication survives the supported
lifecycle, forbidden calls are rejected locally, and an outage blocks only decisions
that require the unavailable evidence. If unattended access is unsuitable, retain it for
manual research and use an independently supported data source for mandatory monitors.

## What Alpaca already offers

Alpaca Basic documents IEX equities and indicative options data. It should not be treated
as full consolidated quote coverage or executable OPRA options pricing.
[Market-data plans](https://docs.alpaca.markets/us/docs/about-market-data-api).

Alpaca also documents Benzinga historical news and streaming news. Test access with the
actual paper credentials before assuming Robinhood is required for news.
[News documentation](https://docs.alpaca.markets/us/docs/historical-news-data).

Provider selection should follow the requirement, not the number of tools available:
Alpaca owns orders, fills, cash and tradability. Robinhood is supplementary research.
Reference instruments or macro releases may require an additional specific source.
A missing mandatory input causes a scoped skip/freeze, not a guess that the thesis holds.

## Corrections to inherited account and execution assumptions

- Account policy and broker classification are different. Alpaca's Trading Account page
  describes margin accounts; this does not verify the user's particular account. Preserve
  our no-borrowing, no-shorting, settled-cash-only policy regardless. Confirm actual legal
  classification/settings rather than inferring them from the account multiplier.
  [Trading Account](https://docs.alpaca.markets/us/docs/account-plans).
- Do not carry forward the old PDT-field logic. Alpaca's June 3, 2026 changelog announced
  removal of `pattern_day_trader` and `pdt_check` on July 6, 2026. Probe current responses
  and current account restrictions; do not infer that trading is unrestricted.
  [API change](https://docs.alpaca.markets/us/changelog/2026-06-03-pdt-f3c04da).
- Fractional protection is more capable than the old spec assumes. Current docs list
  fractional market, limit, stop and stop-limit orders with DAY time in force. Bracket/OCO,
  renewal, asset eligibility and actual acceptance still require verification. The page
  also contains older inconsistent wording, reinforcing the need for paper order tests.
  [Fractional trading](https://docs.alpaca.markets/us/docs/fractional-trading).

The current probe reads a deprecated PDT field and describes multiplier 1 as cash-account
behavior. Update it before using its output as verification. News access, reference
coverage, data age, fractional order behavior, and options feed quality need explicit
probes; the existing probe cannot certify all of them from account/asset flags alone.

## Priority and scope

Research integration belongs in Phase 0 capability discovery; runtime integration follows
Phases 1-4 validation and joins Phase 5 deployment. The first autonomous paper release can
operate without Robinhood for theses whose inputs Alpaca covers adequately. It should
never depend on this chat or an open desktop application to refresh its observations.
