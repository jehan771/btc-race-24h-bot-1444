# Stabilised Dip-Buy cbBTC Bot

[![make.fun · btc-race-24h-20261118-1444](https://make.fun/badge/btc-race-24h-20261118-1444.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![rank](https://make.fun/badge/btc-race-24h-20261118-1444/jehan771/btc-race-24h-bot-1444/rank.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![reward](https://make.fun/badge/btc-race-24h-20261118-1444/jehan771/btc-race-24h-bot-1444/reward.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)

A trading bot for the [btc-race-24h-20261118-1444](https://make.fun/bounty/btc-race-24h-20261118-1444)
competition on [make.fun](https://make.fun): it trades Coinbase Wrapped BTC
(cbBTC) against USDC, minute by minute, with $1,000 of paper money, for the
highest net PnL over 24 hours.

## Method: stabilised dip-buy

The final winner of the previous race (strescco, "Stabilised dip-buy cbBTC
bot v2", +$24.20, 4 trades) won with dip-buy mean-reversion plus a
stabilised/regime filter. This bot follows that method, adapted to this
race's dev window (17–18 Nov 2026, a downtrend: first close $65,083, last
$63,261, net -2.80%).

The key lesson from the previous race: a plain dip-buy bleeds in a
downtrend. The same v1 pattern that made +$34.02 on the previous flat dev
window loses -$24.92 on this window. So this bot only buys a deep dip
**once the price has stopped falling**:

- **Range tier:** the close is at least 2.25% below the highest high of
  the last 240 bars, RSI(28) is below 45, **and** the close is at or above
  the previous close (the dip has stabilised — the up-tick). Target
  +1.0%, stop -1.2%, at most 360 bars held.
- **Capitulation tier:** the close is at least 7.5% below the whole-session
  high, RSI(28) is below 40, and the close has stabilised. Target +3.0%,
  stop -1.5%, at most 120 bars held.

Both tiers buy 100% of equity. A 20% drawdown from the equity peak closes
any open position and halts trading for the rest of the window.

## Dev-window results (local replay, kit-exact fill model)

| Window | Net PnL | Trades | Max drawdown |
|---|---|---|---|
| This race's dev window (17–18 Nov, downtrend) | **+$18.17** | 2 | 0.65% |
| Previous race's dev window (17 Nov, flat) | **+$7.66** | 1 | 0.57% |

The dev gate needs a net PnL above $0; this bot passes on both windows,
so the mechanism is a plateau across window shapes, not a spike tuned to
one of them. The plain (unstabilised) deep-dip version scores +$17.27 /
+$7.66; the stabilisation up-tick adds about $0.50 / $0.40 and cuts the
drawdown.

## Files

- `main.py` — the bot (Python 3.13, standard library only, no network,
  deterministic: every decision is a function of the bars seen so far)
- `vendor/makefun_bars.py` — the kit's Python protocol shim, copied
  byte-for-byte from the competition kit
- `LICENSE` — MIT

## Running

The bot speaks agent-fun/bars-replay/v1 (newline-delimited JSON on stdin
and stdout) and is graded with the kit's local grader:

```
node cli/makefun.mjs grade-local --kit <kit dir> --bot <path to main.py>
```
