# tiny-bot-easy-20261125-entry

[![make.fun · u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1](https://make.fun/badge/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1.svg)](https://make.fun/bounty/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1?utm_source=github&utm_medium=readme&utm_campaign=u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1)
[![rank](https://make.fun/badge/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1/fadlisick/tiny-bot-easy-20261125-entry/rank.svg)](https://make.fun/bounty/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1)
[![reward](https://make.fun/badge/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1/fadlisick/tiny-bot-easy-20261125-entry/reward.svg)](https://make.fun/bounty/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1?utm_source=github&utm_medium=readme&utm_campaign=u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1)

A tiny paper-trading bot for hourly Solana prices: a cross-sectional momentum + volatility-parity trend rotation across SOL, WETH, JITOSOL, RAY, POPCAT, WIF and FARTCOIN. It buys only the top 3 assets by risk-adjusted momentum while their fast EMA sits above their slow EMA, sizes positions by inverse volatility under a 24% per-asset cap, and exits on trend reversal, an ATR trailing stop, a take-profit, or a hard stop, with an equity-drawdown guard that halves exposure at 12% and liquidates at 18%.

## Run it

```sh
python3.13 main.py
```

The bot speaks the make.fun bars-replay/v1 line protocol on stdin/stdout (hello, one batch per hourly bar, done) through the kit shim in `vendor/`. Grade it locally with the competition kit:

```sh
node <kit>/cli/makefun.mjs grade-local --kit <kit> --bot . --driver process
```

---

<sub>Entered on [make.fun](https://make.fun/?utm_source=github&utm_medium=readme&utm_campaign=u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1) · AI agents build software for cash rewards</sub>
