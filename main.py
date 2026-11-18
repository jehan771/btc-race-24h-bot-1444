"""Stabilised dip-buy bot for agent-fun/bars-replay/v1 (cbBTC/USDC, 1-min).

Long only, one position, paper $1,000 start. No clock, no randomness, no
environment reads: every decision is a function of the bars seen so far, so
two runs on the same window produce identical action logs.

Method (the final-winner pattern of the previous race, adapted to this
window): buy a deep dip only once the price has stopped falling. A dip
alone is not enough in a downtrend -- buying a falling knife bleeds -- so
the entry needs the dip AND a stabilising up-tick (this close at or above
the previous close) AND an oversold RSI. That is the "stabilised" part:
the bot waits for the washout to turn, then rides the bounce.

Two entry tiers, both relative (no absolute price levels, so they carry
over to the unseen race window):
- RANGE (the workhorse): the close is at least 2.25% below the highest
  high of the last 240 bars, RSI(28) is below 45, and the close is not
  below the previous close. Target +1.0%, stop -1.2%, at most 360 bars
  held. The deep threshold keeps it out of shallow dips in a falling
  market; the long hold lets a slow grind to the range top pay.
- CAPITULATION (the deep catcher): the close is at least 7.5% below the
  whole-session high, RSI(28) is below 40, and the close is not below
  the previous close. Target +3.0%, stop -1.5%, at most 120 bars held.
Both buy 100% of equity. A 20% drawdown from the equity peak closes any
open position and stops trading for the rest of the window.

Orders decided on a batch fill at the next bar's open, so the entry price
is always read back from the fill, and exits are judged on closes.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

ASSET = "CBBTC"

# --- indicator ---
RSI_N = 28               # Wilder RSI length: slow enough to wait out the knife

# --- range tier ---
LOOKBACK = 240           # reference high: highest high of the last 240 bars
DROP_BPS = 225           # enter when close is >= 2.25% below the reference high
RSI_MAX = 45.0           # ... and RSI(28) is below 45
WIN_BPS = 100            # take profit +1.0% from the entry fill price
LOSE_BPS = 120           # stop loss -1.2% from the entry fill price
MAX_BARS = 360           # ... or 360 bars held, whichever comes first

# --- capitulation tier ---
CAP_DROP_BPS = 750       # enter when close is >= 7.5% below the session high
CAP_RSI_MAX = 40.0       # ... and RSI(28) is below 40
CAP_WIN_BPS = 300        # take profit +3.0%
CAP_LOSE_BPS = 150       # stop loss -1.5%
CAP_MAX_BARS = 120       # ... or 120 bars held

# --- sizing / safety ---
FULL_BPS = 10000         # buy 100% of equity per trade (rules allow to 100%)
STOP_ALL_BPS = 2000      # halt trading for the window at a 20% equity drawdown
BUY_CAP_BPS = 300        # buy fills at most 3% above the close we decided on
SELL_CAP_BPS = 1000      # sell fills at most 10% below it (never trap an exit)
MIN_NOTIONAL = 1_000_000  # 1 USDC, the harness minimum notional


class DipBot:
    """Rolling state for one window: bars, RSI, the open trade, the halt."""

    def __init__(self):
        self.highs = []        # bar highs, in order
        self.closes = []       # bar closes, in order
        self.seed = []         # first RSI_N closes' changes, then dropped
        self.avg_up = None     # Wilder-smoothed average gain, once seeded
        self.avg_dn = None     # Wilder-smoothed average loss, once seeded
        self.rsi = None        # latest RSI, None until seeded
        self.entry_price = None  # fill price of the open position (int)
        self.entry_seq = None  # seq of the batch whose open filled the entry
        self.win = None        # take-profit bps of the open trade
        self.lose = None       # stop-loss bps of the open trade
        self.max_bars = None   # max-hold bars of the open trade
        self.order_out = False  # a buy order is out, awaiting its fill
        self.stopped = False   # drawdown halt latched for the rest of the window
        self.peak = 0          # highest equity seen so far

    def note_bar(self, close, high):
        """Fold one bar's close/high into the rolling highs and RSI."""
        self.highs.append(high)
        if self.closes:
            move = close - self.closes[-1]
            if self.avg_up is None:
                self.seed.append(move)
                if len(self.seed) == RSI_N:
                    up = sum(m for m in self.seed if m > 0)
                    dn = sum(-m for m in self.seed if m < 0)
                    self.avg_up = up / RSI_N
                    self.avg_dn = dn / RSI_N
                    self.seed = []
            else:
                up = move if move > 0 else 0
                dn = -move if move < 0 else 0
                self.avg_up = (self.avg_up * (RSI_N - 1) + up) / RSI_N
                self.avg_dn = (self.avg_dn * (RSI_N - 1) + dn) / RSI_N
            if self.avg_up is not None:
                if self.avg_dn == 0:
                    self.rsi = 100.0
                else:
                    self.rsi = 100.0 - 100.0 / (1.0 + self.avg_up / self.avg_dn)
        self.closes.append(close)

    def ref_high(self):
        """Highest high of the last LOOKBACK bars (whole session if fewer)."""
        window = self.highs[-LOOKBACK:]
        return max(window) if window else 0

    def top_high(self):
        """Highest high of the whole window so far."""
        return max(self.highs) if self.highs else 0

    def settled(self):
        """True when the price has stopped falling: this close >= last close."""
        if len(self.closes) < 2:
            return False
        return self.closes[-1] >= self.closes[-2]

    def absorb(self, fills, seq):
        """Learn entry prices from fills; clear the outstanding-buy flag."""
        for fill in fills:
            if fill.get("client_id", "").startswith("_"):
                continue
            if fill.get("side") == "buy":
                self.order_out = False
                if fill.get("status") == "filled":
                    self.entry_price = int(fill["price"])
                    self.entry_seq = seq
            elif fill.get("side") == "sell":
                if fill.get("status") == "filled":
                    self.entry_price = None
                    self.entry_seq = None
                    self.win = None
                    self.lose = None
                    self.max_bars = None


BOT = DipBot()


def sell_everything(qty):
    """One order selling the whole position, with a wide slippage cap."""
    return [{"side": "sell", "symbol": ASSET, "qty": qty,
             "max_slippage_bps": SELL_CAP_BPS}]


def buy_full(cash, win, lose, max_bars):
    """One order buying 100% of cash, remembering this trade's exit rules."""
    notional = cash * FULL_BPS // 10_000
    if notional < MIN_NOTIONAL:
        return []
    BOT.order_out = True
    BOT.win = win
    BOT.lose = lose
    BOT.max_bars = max_bars
    return [{"side": "buy", "symbol": ASSET,
             "notional_quote_units": notional, "max_slippage_bps": BUY_CAP_BPS}]


def decide(state, batch):
    """Decide this batch's orders (a list, possibly empty)."""
    bar = state.latest.get(ASSET)
    if bar is not None:
        BOT.note_bar(int(bar["c"]), int(bar["h"]))
    BOT.absorb(batch.get("fills", []), state.seq)

    if state.equity > BOT.peak:
        BOT.peak = state.equity

    holding = state.positions.get(ASSET)
    holding = holding if holding is not None and holding.qty > 0 else None
    close = state.close(ASSET)

    # Halt: 20% drawdown from the equity peak. Close out and stop.
    if not BOT.stopped and BOT.peak > 0:
        if state.equity * 10_000 <= BOT.peak * (10_000 - STOP_ALL_BPS):
            BOT.stopped = True
    if BOT.stopped:
        return sell_everything(holding.qty) if holding is not None else []

    # Exit the open position: take profit, stop loss, or max hold, on closes.
    if holding is not None and BOT.entry_price is not None and close is not None:
        if close * 10_000 >= BOT.entry_price * (10_000 + BOT.win):
            return sell_everything(holding.qty)
        if close * 10_000 <= BOT.entry_price * (10_000 - BOT.lose):
            return sell_everything(holding.qty)
        if BOT.entry_seq is not None and state.seq - BOT.entry_seq >= BOT.max_bars:
            return sell_everything(holding.qty)
        return []

    # Enter: flat, no buy outstanding, dip washed out AND stabilised.
    if holding is None and not BOT.order_out and close is not None and BOT.rsi is not None:
        # Capitulation tier first: the deepest dips get the widest target.
        top = BOT.top_high()
        if (BOT.rsi < CAP_RSI_MAX and top > 0 and BOT.settled()
                and close * 10_000 <= top * (10_000 - CAP_DROP_BPS)):
            return buy_full(state.cash, CAP_WIN_BPS, CAP_LOSE_BPS, CAP_MAX_BARS)
        # Range tier: a deep dip below the recent high, now stabilised.
        ref = BOT.ref_high()
        if (BOT.rsi < RSI_MAX and ref > 0 and BOT.settled()
                and close * 10_000 <= ref * (10_000 - DROP_BPS)):
            return buy_full(state.cash, WIN_BPS, LOSE_BPS, MAX_BARS)
    return []


def main():
    def on_hello(state):
        BOT.peak = state.equity

    def on_batch(state, batch):
        return decide(state, batch)

    makefun_bars.run(on_batch, on_hello=on_hello)


if __name__ == "__main__":
    main()
