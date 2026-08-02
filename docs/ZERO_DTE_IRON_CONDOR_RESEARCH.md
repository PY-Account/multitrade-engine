# 0DTE Iron Condor Research

This candidate is research-only and limited to SPY and QQQ. It evaluates once
near 10:00 ET, after six closed five-minute bars. Entry requires an opening gap
of at most 4%, an opening range of at most 2%, bounded relative volume and
trend strength, and no high-volatility regime.

The option vehicle is a same-expiration, four-leg, defined-risk iron condor.
The short delta target is 0.15 with a 0.20 absolute ceiling; protective wings
target 0.05 delta and may not exceed five strike points. The research policy
targets 45% premium capture, limits loss to 1.5 times opening premium, and
forces an exit after 210 minutes. Profit and loss exits are evaluated before
the time exit.

Alpaca begins expiration-day risk handling at 15:30 ET and stops accepting new
opening orders then. MultiTrade's exit is intentionally much earlier. No Paper
submission is authorized until exact-contract, synchronized multi-leg evidence
passes cost, return-on-risk, tail-loss, and prospective gates.

Historical underlying bars can screen quiet-day timing but cannot prove option
fills, intraday Greeks, premium capture, or executable profitability. Those
claims require decision-time chains and exact-contract option paths.
