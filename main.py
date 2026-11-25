"""Tiny trend-rotation bot for make.fun bars-replay/v1.

Design (cross-sectional momentum + volatility-parity rotation, all parameters
identical across every asset, nothing asset-specific):
- Per symbol, over its own hourly closes: fast EMA(8) vs slow EMA(24), a
  12-bar log-return momentum, and a 24-bar realised volatility (std of
  1-bar log returns, floored at 0.005).
- An asset is eligible only while its fast EMA sits above its slow EMA and
  its 12-bar momentum is above +1.5%.
- Eligible assets are ranked by risk-adjusted momentum (momentum / vol) and
  only the top 3 can be bought. Sizing is inverse-volatility across those
  three out of a 72% equity budget, hard-capped at 24% of equity per asset,
  so fewer eligible assets automatically leaves more cash in USDC.
- Exits: the whole position goes when the fast EMA falls back under the
  slow EMA (after a 4-bar minimum hold), or when the close drops below the
  position's peak close minus 2.5x ATR(14) trailing stop, or when the
  position is worth 1.5x its cost (take profit) or 0.85x its cost (hard
  stop); the stops act immediately, without the minimum hold.
- Anti-churn: a holding is only topped up or trimmed when its target weight
  differs from its current weight by more than 5% of equity.
- Equity drawdown guard on peak equity: at 12% drawdown the per-asset cap
  halves to 12%; at 18% everything is liquidated to USDC and no new entry
  is taken for the rest of the window.
- The bot speaks the hello / batch / orders line protocol through the kit
  shim in vendor/: one JSON reply per batch, echoing its seq. It keeps its
  own close history (the shim only keeps the latest bar per symbol).
- Deterministic: pure float math on the bars, iteration in hello order,
  no clock, no environment, no randomness, no network.
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

# --- Strategy parameters (identical for every symbol) ---
FAST_SPAN = 8
SLOW_SPAN = 24
MOM_BARS = 12
MOM_MIN = 0.015
VOL_BARS = 24
VOL_FLOOR = 0.005
TOP_N = 3
BUDGET = 0.72
CAP_W = 0.24
CAP_W_HALVED = 0.12
MIN_ENTRY_W = 0.08
ADJUST_MIN = 0.05
MIN_HOLD_BARS = 4
ATR_SPAN = 14
TRAIL_MULT = 2.5
ROI_TAKE = 1.5
HARD_STOP = 0.85
MAX_SLIPPAGE_BPS = 150
DD_HALVE = 0.12
DD_FREEZE = 0.18


class Ema:
    """One exponential average: seeded by the mean of its first span closes."""

    def __init__(self, span):
        self.span = span
        self.warm = []
        self.value = None

    def step(self, close):
        if self.value is not None:
            self.value += (2.0 / (self.span + 1)) * (close - self.value)
            return
        self.warm.append(close)
        if len(self.warm) == self.span:
            self.value = sum(self.warm) / self.span


class Track:
    """Per-symbol history and position memory."""

    def __init__(self):
        self.closes = []
        self.highs = []
        self.lows = []
        self.fast = Ema(FAST_SPAN)
        self.slow = Ema(SLOW_SPAN)
        self.peak_close = None
        self.held_since = None

    def record(self, bar):
        close = float(int(bar["c"]))
        self.closes.append(close)
        self.highs.append(float(int(bar["h"])))
        self.lows.append(float(int(bar["l"])))
        self.fast.step(close)
        self.slow.step(close)

    def momentum(self):
        """Log return over MOM_BARS bars, or None without enough closes."""
        if len(self.closes) <= MOM_BARS:
            return None
        return math.log(self.closes[-1] / self.closes[-1 - MOM_BARS])

    def volatility(self):
        """Std of 1-bar log returns over VOL_BARS bars, floored."""
        closes = self.closes
        if len(closes) < 3:
            return VOL_FLOOR
        rets = []
        start = max(1, len(closes) - VOL_BARS)
        for i in range(start, len(closes)):
            rets.append(math.log(closes[i] / closes[i - 1]))
        if not rets:
            return VOL_FLOOR
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / len(rets)
        return max(VOL_FLOOR, math.sqrt(var))

    def atr(self):
        """Mean true range over ATR_SPAN bars, in price units, or None."""
        if len(self.closes) < 2:
            return None
        ranges = []
        start = max(1, len(self.closes) - ATR_SPAN)
        for i in range(start, len(self.closes)):
            prev = self.closes[i - 1]
            ranges.append(max(
                self.highs[i] - self.lows[i],
                abs(self.highs[i] - prev),
                abs(self.lows[i] - prev),
            ))
        if not ranges:
            return None
        return sum(ranges) / len(ranges)

    def trending_up(self):
        return (
            self.fast.value is not None
            and self.slow.value is not None
            and self.fast.value > self.slow.value
        )

    def trending_down(self):
        return (
            self.fast.value is not None
            and self.slow.value is not None
            and self.fast.value < self.slow.value
        )


tracks = {}
peak_equity = 0
frozen = False


def on_hello(state):
    for info in state.hello["symbols"]:
        tracks[info["symbol"]] = Track()


def on_batch(state, batch):
    global peak_equity, frozen
    # Record every bar of this batch first: warm-up batches count too, so
    # the indicators are already seeded when the scored window starts.
    fresh = []
    for bar in batch["bars"]:
        track = tracks.get(bar["symbol"])
        if track is None:
            continue
        track.record(bar)
        fresh.append(bar["symbol"])
    if state.warming_up:
        return []
    if not fresh:
        return []

    equity = state.equity
    if equity > peak_equity:
        peak_equity = equity
    drawdown = (peak_equity - equity) / peak_equity if peak_equity else 0.0
    if drawdown >= DD_FREEZE:
        frozen = True

    # Track which positions are held and since when, for the minimum hold.
    for symbol, held in list(state.positions.items()):
        track = tracks.get(symbol)
        if track is None:
            continue
        if track.held_since is None:
            track.held_since = state.seq
        close = state.close(symbol)
        if close is not None:
            if track.peak_close is None or close > track.peak_close:
                track.peak_close = close
    for symbol, track in tracks.items():
        if symbol not in state.positions:
            track.held_since = None
            track.peak_close = None

    cap = CAP_W_HALVED if drawdown >= DD_HALVE else CAP_W
    orders = []
    max_orders = state.hello["caps"]["max_orders_per_bar"]
    min_notional = int(state.hello["fees"]["min_notional_quote_units"])

    # A frozen bot only liquidates: no new entries for the rest of the run.
    if frozen:
        for symbol in state.hello["symbols"]:
            held = state.positions.get(symbol["symbol"])
            if held is not None and len(orders) < max_orders:
                orders.append({
                    "side": "sell", "symbol": symbol["symbol"],
                    "qty": held.qty, "max_slippage_bps": MAX_SLIPPAGE_BPS,
                })
        return orders

    # Score every fresh symbol: eligible needs an uptrend plus momentum.
    scored = []
    for symbol in fresh:
        track = tracks[symbol]
        mom = track.momentum()
        if mom is None or not track.trending_up() or mom <= MOM_MIN:
            continue
        vol = track.volatility()
        scored.append((mom / vol, symbol))
    scored.sort(key=lambda item: (-item[0], item[1]))
    leaders = [symbol for _, symbol in scored[:TOP_N]]

    # Inverse-volatility target weights out of the equity budget.
    targets = {}
    if leaders:
        inv = {}
        total_inv = 0.0
        for symbol in leaders:
            weight = 1.0 / tracks[symbol].volatility()
            inv[symbol] = weight
            total_inv += weight
        for symbol in leaders:
            targets[symbol] = min(cap, (inv[symbol] / total_inv) * BUDGET)

    # Sells first: trailing stops, trend exits, then trims toward target.
    for info in state.hello["symbols"]:
        symbol = info["symbol"]
        if symbol not in fresh or len(orders) >= max_orders:
            continue
        held = state.positions.get(symbol)
        if held is None:
            continue
        track = tracks[symbol]
        close = state.close(symbol)
        if close is None:
            continue
        held_bars = state.seq - track.held_since if track.held_since is not None else 0
        atr = track.atr()
        stop_hit = (
            atr is not None
            and track.peak_close is not None
            and close < track.peak_close - TRAIL_MULT * atr
        )
        position_value = state.value(symbol, held.qty)
        take_profit = ROI_TAKE > 0 and position_value >= ROI_TAKE * held.cost
        hard_stop = HARD_STOP > 0 and position_value <= HARD_STOP * held.cost
        if stop_hit or take_profit or hard_stop or (track.trending_down() and held_bars >= MIN_HOLD_BARS):
            orders.append({
                "side": "sell", "symbol": symbol,
                "qty": held.qty, "max_slippage_bps": MAX_SLIPPAGE_BPS,
            })
        elif symbol in targets:
            current_w = state.value(symbol, held.qty) / equity if equity else 0.0
            diff = targets[symbol] - current_w
            if diff < -ADJUST_MIN:
                # Trim the excess value back toward the target weight.
                value = state.value(symbol, held.qty)
                excess = -diff * equity
                qty = int(held.qty * excess // value) if value > 0 else 0
                if qty > 0 and qty < held.qty:
                    orders.append({
                        "side": "sell", "symbol": symbol,
                        "qty": qty, "max_slippage_bps": MAX_SLIPPAGE_BPS,
                    })
                elif qty >= held.qty:
                    orders.append({
                        "side": "sell", "symbol": symbol,
                        "qty": held.qty, "max_slippage_bps": MAX_SLIPPAGE_BPS,
                    })

    # Buys: only the ranked leaders, only with a real target weight.
    cash_left = state.cash
    held_count = len(state.positions)
    for symbol in leaders:
        if len(orders) >= max_orders:
            break
        if symbol in state.positions:
            continue
        if held_count >= state.hello["caps"]["max_open_positions"]:
            break
        target_w = targets[symbol]
        if target_w < MIN_ENTRY_W:
            continue
        notional = int(target_w * equity)
        room = state.weight_room(symbol)
        if room < notional:
            notional = room
        if cash_left < notional:
            notional = cash_left
        if notional < min_notional:
            continue
        orders.append({
            "side": "buy", "symbol": symbol,
            "notional_quote_units": notional,
            "max_slippage_bps": MAX_SLIPPAGE_BPS,
        })
        cash_left -= notional
        held_count += 1

    # Top-ups for leaders already held, where the target moved enough.
    for symbol in leaders:
        if len(orders) >= max_orders:
            break
        held = state.positions.get(symbol)
        if held is None or symbol not in fresh:
            continue
        current_w = state.value(symbol, held.qty) / equity if equity else 0.0
        diff = targets[symbol] - current_w
        if diff <= ADJUST_MIN:
            continue
        notional = int(diff * equity)
        room = state.weight_room(symbol)
        if room < notional:
            notional = room
        if cash_left < notional:
            notional = cash_left
        if notional < min_notional:
            continue
        orders.append({
            "side": "buy", "symbol": symbol,
            "notional_quote_units": notional,
            "max_slippage_bps": MAX_SLIPPAGE_BPS,
        })
        cash_left -= notional

    return orders


if __name__ == "__main__":
    makefun_bars.run(on_batch, on_hello=on_hello)
