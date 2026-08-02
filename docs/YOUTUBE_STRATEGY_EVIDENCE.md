# YouTube-derived strategy evidence

## T3 + Range Filter trend candidate

Source reviewed: [I Did It! I Found The BEST AI Trading Bot Ever (INSANE
Results)](https://www.youtube.com/watch?v=BPFwaD0CgZ8), published by Trading
with DaviddTech on 2026-07-13.

The video's transcript states the following testable concepts:

- Gold, one-hour timeframe, long only;
- a setup associated with the Asia open;
- Tillson T3 must be green/rising and price must be above it;
- the Donovan Wall Range Filter must be green/rising and price must be above
  it;
- a stop based on ATR bands;
- an approximately 3.8 reward-to-risk target.

The video reports 456 historical trades, a 31 percent win rate, approximately
1.89 profit factor, 28 percent maximum drawdown, and very large compounded
return. Those are the publisher's claims and are **not MultiTrade evidence**.
The video itself says forward testing is still required.

## Missing information

The video does not disclose the complete Pine source, T3 settings, Range Filter
settings, exact Asia-open window, ATR formula/settings, sizing, compounding,
spread, slippage, data vendor, test dates, or treatment of intrabar ambiguity.
Its reported result therefore cannot be reproduced exactly from the video.

The current Alpaca account also does not trade spot Gold or Forex. Testing GLD
or liquid US equities is not equivalent to testing XAUUSD during Asia hours.

## MultiTrade adaptation

`t3_range_trend` is a separately labeled equity research adaptation:

- the published Tim Tillson six-EMA T3 construction;
- an adaptive Range Filter that smooths absolute price change and gates moves
  smaller than the derived threshold;
- a long entry only when both filters transition to rising with price above
  both;
- ATR-defined stop and a configurable R-multiple target;
- no claim that the implementation matches the undisclosed video script.

The default candidate and fast/slow sensitivity variants are frozen research
experiments. They participate in accelerated validation and nested parameter
optimization, but remain disabled in the account configuration and cannot
authorize execution.

Relevant public descriptions:

- [Range Filter [DW] by DonovanWall](https://www.tradingview.com/script/lut7sBgG-Range-Filter-DW/)
- [Tim Tillson T3 open-source description](https://www.tradingview.com/script/UI3EYBCr/)

## Admission rule

A YouTube idea is admitted only when its rules can be expressed without future
information. Publisher screenshots, subscriber counts, claimed profits, and
in-sample TradingView results are never evidence gates. Any adaptation must
pass the same adverse-cost, breadth, chronological, nested holdout, and later
prospective Paper requirements as an internally proposed strategy.
