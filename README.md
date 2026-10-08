# dice-api

HTTP dice rolls backed by Eshkol randomness. 


## What it is

One endpoint:

    GET /roll?sides=20&count=2&backend=moonlab

Returns:

```json
{
  "rolls": [14, 7],
  "sides": 20,
  "count": 2,
  "provenance": {
    "backend": "moonlab",
    "n_bytes": 8,
    "note": "bytes from Eshkol via drawd; see drawd provenance for attestation"
  }
}
```

## How it works

1. Takes sides (2-256), count (1-100), backend (any Eshkol backend name).
2. Requests bytes from drawd (Unix socket /tmp/eshkol-drawd.sock preferred,
   TCP 127.0.0.1:18751 fallback). Over-provisions 4 bytes per die to feed
   rejection sampling.
3. Maps bytes to uniform dice via rejection sampling -- never modulo, which
   would bias results when 256 is not a multiple of sides.
4. Returns JSON. No game logic, no state, no randomness of its own.

## Running

    python -m dice_api.dice_api [--host 127.0.0.1] [--port 18752]

Requires drawd running. Stdlib only -- no dependencies.

## Design notes

- It validates the
  "one randomness interface, many consumers" architecture.
- For latency-sensitive use (real-time dice), run drawd with the pregen
  buffer (DRAWD_PREGEN_BACKENDS) so slow backends like moonlab do not
  block rolls.
- Provenance is passed through, not invented here. The notary story lives
  in Eshkol/drawd.
