# Proposal: a capture you started is only evidence after you have calibrated it against an independent count

- **Source**: lis dream 2026-10-06 (VP-18464 on-prem capture loop, 2026-10-06)
- **Status**: inbox — single employer domain and partly tool-specific, so not a PR. Candidate extension to the existing "a quiet observation window is not evidence of failure" lesson (mirror clause).

## Lesson (de-identified)
A heartbeat proves the observer is alive; it does not prove the observer sees everything. Before a self-built capture (a log tail, a packet sniffer, a polling script) is allowed to produce a "zero events in N days" claim, compare its first window against an independent direct count of the same stream taken by a different method. If the two counts differ, the capture is lossy and its zero is worthless — restart the clock only after the fix is verified the same way. A streaming follow is not a retention mechanism: it drops on every reconnect and replays only a default-sized tail; poll with overlap and de-duplicate instead.

## Why it is a discipline, not a behaviour description
It prescribes a calibration step before an absence claim is accepted, and it applies to any home-made observer regardless of tooling.

## Evidence (identifying details stay here)
1. 2026-10-06: a `kubectl logs -f -l <selector>` capture ran 13 hours with an hourly heartbeat that kept counting. Its hourly totals were ~15% below the ingress's real volume because (a) a label-selector `logs` call defaults to `--tail=10`, so each of the 272 reconnects (`unexpected EOF`, ~every 3 min) replayed only ten lines of a two-minute gap, and (b) nobody had compared the first hour's count with a direct count. Rewritten as a 60-second poll with `--since=180s --tail=-1` and request-id de-duplication; first-window calibration 186 vs direct 178 vs default-tail 10. The provable zero window restarted from the rewrite, not from the first start.

## Target
`framework/ENGINEERING-LESSONS.md` — append to the "quiet observation window" lesson as a third clause, if a second domain produces the same shape.
