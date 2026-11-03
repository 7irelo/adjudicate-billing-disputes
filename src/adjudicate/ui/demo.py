"""A thin Gradio front end over the API. Demonstration only: fictional operators, not advice."""

import json
import os

import gradio as gr
import httpx

from adjudicate.tenancy.auth import demo_api_key
from adjudicate.world.seed import MANIFEST

API = os.environ.get("ADJUDICATE_API_URL", "http://127.0.0.1:8000")
TENANTS = ["baobab", "karoo", "fynbos"]
DISCLAIMER = (
    "**Demonstration only.** The operators, customers and records are fictional, and nothing here "
    "is legal or billing advice."
)
EXAMPLES = [
    ["baobab", "duplicate_recurring", "My monthly fee is on my May invoice twice."],
    [
        "baobab",
        "cancellation_mid_month",
        "I cancelled on 10 April. Why did I pay the full fee for May?",
    ],
    [
        "karoo",
        "cancellation_mid_month",
        "I cancelled on 10 April. Why did I pay the full fee for May?",
    ],
    [
        "baobab",
        "fraud_pbx_burst",
        "I was billed R1,200 for international calls I never made in May.",
    ],
]


def scenarios() -> list[str]:
    if not MANIFEST.exists():
        return []
    names = {k.split(":")[1] for k in json.loads(MANIFEST.read_text("utf-8"))["scenarios"]}
    return sorted(names)


def _headers(tenant: str) -> dict[str, str]:
    return {"X-API-Key": demo_api_key(tenant)}


def submit(tenant: str, scenario: str, text: str) -> str:
    manifest = json.loads(MANIFEST.read_text("utf-8"))
    account = manifest["scenarios"][f"{tenant}:{scenario}"]["account_ref"]
    response = httpx.post(
        f"{API}/disputes",
        headers=_headers(tenant),
        json={"account_ref": account, "text": text},
        timeout=300,
    )
    return json.dumps(response.json(), indent=2)


def reviews(tenant: str) -> str:
    return json.dumps(httpx.get(f"{API}/reviews", headers=_headers(tenant)).json(), indent=2)


def resolve(tenant: str, review_id: str, action: str, note: str) -> str:
    response = httpx.post(
        f"{API}/reviews/{review_id}",
        headers=_headers(tenant),
        json={"action": action, "note": note},
        timeout=120,
    )
    return json.dumps(response.json(), indent=2)


def build_demo() -> gr.Blocks:
    with gr.Blocks(title="Adjudicate") as demo:
        gr.Markdown(f"# Adjudicate\n{DISCLAIMER}")
        with gr.Tab("Dispute"):
            tenant = gr.Dropdown(TENANTS, value="baobab", label="Operator")
            scenario = gr.Dropdown(scenarios(), label="Account (by scenario)")
            text = gr.Textbox(label="Dispute", lines=3)
            run = gr.Button("Adjudicate")
            output = gr.Code(language="json", label="Result")
            run.click(submit, [tenant, scenario, text], output)
            gr.Examples(EXAMPLES, [tenant, scenario, text])
        with gr.Tab("Review queue"):
            queue_tenant = gr.Dropdown(TENANTS, value="baobab", label="Operator")
            refresh = gr.Button("Refresh")
            queue = gr.Code(language="json", label="Pending")
            refresh.click(reviews, queue_tenant, queue)
            review_id = gr.Textbox(label="Review ID")
            action = gr.Dropdown(["approve", "reject", "escalate"], value="approve", label="Action")
            note = gr.Textbox(label="Note")
            send = gr.Button("Submit")
            outcome = gr.Code(language="json", label="Outcome")
            send.click(resolve, [queue_tenant, review_id, action, note], outcome)
    return demo
