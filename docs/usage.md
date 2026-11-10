# Usage

## The five-minute tour

1. **Sign up.** The demo fleet seeds itself: five machines with 24 hours of
   history, deliberately spread across all four health bands.
2. **Look at the KPI row.** Machines, healthy, warning, critical, open alerts.
3. **Click a card.** You get the channel breakdown, the 72-hour projection, the
   last eight readings, and a form to add one.
4. **Press "Simulate 24h".** Every machine drifts a little further toward
   critical and the alerts update. This is how you watch the engine react
   without a real PLC.
5. **Resolve an alert.** It asks what you did. That note is the point - an alert
   closed with no explanation teaches the next person nothing.

## Reading a machine card

```
PMP-101                          [WARNING]
Cooling water pump
Line A
72 / 100 health
###############......
RUL 18.4 h        worst vibration
```

- **Health** - the blended index. 100 is as-new, 0 is failed.
- **RUL** - hours until health crosses 30 at the current rate. `unknown` means
  the machine is not degrading, which is not the same as "will last forever".
- **worst** - which channel is dragging the score down. This is the one to look
  at first.

## Reading the detail view

**Channel breakdown** - each channel's current value, its score as a percentage,
and a bar. A channel at 20% is the reason the machine is amber.

**Projected health** - a straight line at the current degradation rate, with the
failure threshold drawn as a dashed red line. The note under it says it is a
projection, not a forecast, because that distinction matters when you are
deciding whether to order a part today or next week.

**Add a reading** - three numbers. The response is the new verdict, not just an
acknowledgement.
