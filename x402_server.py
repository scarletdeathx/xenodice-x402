"""xenodice x402-challenge: pay-per-roll dice API for the Algorand Global x402 Challenge.

Puts an x402 (v2) payment gate in front of the Eshkol-backed /roll endpoint:
  unpaid -> 402 + PAYMENT-REQUIRED (Algorand Mainnet USDC via the GoPlausible facilitator)
  paid   -> verify via facilitator, serve the roll, settle via facilitator

The roll logic itself is untouched (imported from dice_api); this file is only
the toll booth. Eshkol/drawd remain the randomness source.

All config is environment variables; no secrets live in the repo.
The payTo address must be opted into USDC ASA 31566704 before the first payment,
and the challenge tag must be live in the 402 BEFORE the first real settlement
(attribution is not retroactive).

  X402_PAYTO            Algorand address receiving per-roll payments (required)
  X402_FACILITATOR_URL  default https://facilitator.goplausible.xyz
  X402_PRICE_USDC       price per roll in USDC, default "0.01"
  X402_HOST / X402_PORT default 127.0.0.1 / 18753

Run: /opt/xenodice/.venv/bin/python x402_server.py
"""

import json
import os
import urllib.parse

from flask import Flask, request, Response

from dice_api import draw_bytes, roll_dice

from x402.extensions.bazaar import declare_discovery_extension
from x402.http.facilitator_client import FacilitatorConfig, HTTPFacilitatorClientSync
from x402.http.middleware.flask import payment_middleware
from x402.http.types import PaymentOption, RouteConfig
from x402.mechanisms.avm import ALGORAND_MAINNET_CAIP2
from x402.mechanisms.avm.exact.register import register_exact_avm_server
from x402.mechanisms.evm.exact.register import register_exact_evm_server
from x402.schemas.base import AssetAmount
from x402.server import x402ResourceServerSync

# --- challenge constants (public, stable) -----------------------------------
FACILITATOR_URL = os.environ.get(
    "X402_FACILITATOR_URL", "https://facilitator.goplausible.xyz"
)
USDC_ASA_ID = "31566704"  # USDC on Algorand mainnet
USDC_DECIMALS = 6
CHALLENGE_TAG = "x402-global-challenge"

# Base (EVM) payment rail — added alongside Algorand, which is untouched.
BASE_CHAIN_ID = "eip155:8453"
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA4aN0"  # native USDC on Base
EVM_PAYTO = "0x29DcA5EBBbeb4027c65C3797a7B768B09FDae5b2"  # voidwitch.eth

PAYTO = os.environ.get("X402_PAYTO", "").strip()
if not PAYTO:
    raise SystemExit("X402_PAYTO is not set: an Algorand payTo address is required.")

price_atomic = str(int(round(float(os.environ.get("X402_PRICE_USDC", "0.01")) * 10**USDC_DECIMALS)))

# Public base URL advertised in the 402 resource block (Bazaar + clients use this).
PUBLIC_BASE_URL = os.environ.get("X402_PUBLIC_URL", "https://xenodice.rngoddess.com").rstrip("/")

# --- x402 plumbing -----------------------------------------------------------
facilitator = HTTPFacilitatorClientSync(FacilitatorConfig(url=FACILITATOR_URL))
server = x402ResourceServerSync(facilitator)
register_exact_avm_server(server, networks=[ALGORAND_MAINNET_CAIP2])
register_exact_evm_server(server, networks=[BASE_CHAIN_ID])

routes = {
    "GET /roll": RouteConfig(
        accepts=[
            PaymentOption(
                scheme="exact",
                network=ALGORAND_MAINNET_CAIP2,
                pay_to=PAYTO,
                # NOTE: x402s server_base builds the 402 extra from the *prices*
                # extra, ignoring PaymentOption.extra entirely. The challenge tag
                # must ride on the AssetAmount or attribution breaks (it is not
                # retroactive).
                price=AssetAmount(
                    amount=price_atomic,
                    asset=USDC_ASA_ID,
                    extra={"tag": CHALLENGE_TAG},
                ),
                max_timeout_seconds=300,
            ),
            PaymentOption(
                scheme="exact",
                network=BASE_CHAIN_ID,
                pay_to=EVM_PAYTO,
                price=AssetAmount(
                    amount=price_atomic,
                    asset=USDC_BASE,
                ),
                max_timeout_seconds=300,
            ),
        ],
        resource=f"{PUBLIC_BASE_URL}/roll",
        description=(
            "Eshkol-backed dice rolls: GET /roll?sides=20&count=2&backend=moonlab "
            "returns uniform dice rolls drawn from the Eshkol randomness network, "
            "with provenance."
        ),
        mime_type="application/json",
        extensions=declare_discovery_extension(
            input={"sides": 20, "count": 2, "backend": "moonlab"},
            input_schema={
                "type": "object",
                "properties": {
                    "sides": {"type": "integer", "minimum": 2, "maximum": 256},
                    "count": {"type": "integer", "minimum": 1, "maximum": 100},
                    "backend": {"type": "string"},
                },
            },
        ),
    ),
}

app = Flask(__name__)
payment_middleware(app, routes, server)


@app.route("/roll")
def roll():
    q = urllib.parse.parse_qs(urllib.parse.urlparse(request.url).query)
    try:
        sides = int(q.get("sides", ["20"])[0])
        count = int(q.get("count", ["1"])[0])
        backend = q.get("backend", ["moonlab"])[0]
        n_bytes = count * 4
        raw = draw_bytes(backend, n_bytes)
        rolls = roll_dice(raw, sides, count)
        body = json.dumps({
            "rolls": rolls,
            "sides": sides,
            "count": count,
            "provenance": {
                "backend": backend,
                "n_bytes": n_bytes,
                "note": "bytes from Eshkol via drawd; see drawd provenance for attestation",
            },
        })
        return Response(body, status=200, mimetype="application/json")
    except (ValueError, RuntimeError) as e:
        return Response(json.dumps({"error": str(e)}), status=400, mimetype="application/json")
    except Exception as e:  # noqa: BLE001 - drawd failures surface as 502
        return Response(json.dumps({"error": f"drawd error: {e}"}), status=502,
                        mimetype="application/json")


if __name__ == "__main__":
    app.run(
        host=os.environ.get("X402_HOST", "127.0.0.1"),
        port=int(os.environ.get("X402_PORT", "18753")),
    )
