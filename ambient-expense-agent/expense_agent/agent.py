# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import base64
import json
import logging
import os
from typing import Any

import google.auth
from google.adk.agents.context import Context
from google.adk.apps import App
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.workflow import Workflow, node
from google.genai import Client, types
from pydantic import BaseModel, Field

from expense_agent.config import EXPENSE_THRESHOLD, MODEL_NAME
from expense_agent.security import detect_injection, scrub_pii


def _setup_credentials() -> None:
    """Configure Google Cloud credentials from the environment (called once at startup)."""
    try:
        _, project_id = google.auth.default()
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project_id)
    except Exception:
        pass
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")


_setup_credentials()


# 1. Schemas
class Expense(BaseModel):
    amount: float = Field(description="The expense amount in USD.")
    submitter: str = Field(description="The name or email of the person submitting the expense.")
    category: str = Field(description="The category of the expense (e.g. travel, meals).")
    description: str = Field(description="A description of the expense.")
    date: str = Field(description="The date of the expense.")


class WorkflowState(BaseModel):
    expense: Expense | None = None
    risk_analysis: str | None = None
    status: str | None = None
    reason: str | None = None
    security_redactions: list[str] = Field(default_factory=list)
    injection_flagged: bool = False


class WorkflowOutput(BaseModel):
    status: str = Field(description="The final status of the expense: approved or rejected.")
    reason: str = Field(description="The reason for the approval or rejection.")


# 2. Nodes
@node
def parse_expense_event(node_input: Any) -> Event:
    """Parses incoming JSON or Pub/Sub event, extracts expense details, and saves to state."""
    raw_str = ""
    if isinstance(node_input, types.Content):
        if node_input.parts:
            raw_str = node_input.parts[0].text or ""
    elif isinstance(node_input, str):
        raw_str = node_input
    elif isinstance(node_input, dict):
        data_dict = node_input
    else:
        raw_str = str(node_input)

    if not isinstance(node_input, dict):
        try:
            data_dict = json.loads(raw_str)
        except json.JSONDecodeError:
            raise ValueError(f"Input is not a valid JSON string: {raw_str}")

    # Standard Pub/Sub wrapper check
    message_data = None
    if "message" in data_dict and isinstance(data_dict["message"], dict) and "data" in data_dict["message"]:
        message_data = data_dict["message"]["data"]
    elif "data" in data_dict:
        message_data = data_dict["data"]

    if message_data is not None:
        try:
            if isinstance(message_data, str):
                decoded_bytes = base64.b64decode(message_data)
                decoded_str = decoded_bytes.decode("utf-8")
                expense_data = json.loads(decoded_str)
            else:
                expense_data = message_data
        except Exception:
            expense_data = message_data
    else:
        expense_data = data_dict

    if isinstance(expense_data, str):
        try:
            expense_data = json.loads(expense_data)
        except json.JSONDecodeError:
            raise ValueError(f"Decoded expense data is not valid JSON: {expense_data}")

    # Extract details ignoring case
    normalized = {}
    for key in ["amount", "submitter", "category", "description", "date"]:
        val = None
        for k, v in expense_data.items():
            if k.lower() == key:
                val = v
                break
        normalized[key] = val

    try:
        amount = float(normalized.get("amount") or 0.0)
    except (ValueError, TypeError):
        amount = 0.0

    expense = Expense(
        amount=amount,
        submitter=str(normalized.get("submitter") or ""),
        category=str(normalized.get("category") or ""),
        description=str(normalized.get("description") or ""),
        date=str(normalized.get("date") or ""),
    )

    return Event(
        output=expense.model_dump(),
        state={"expense": expense.model_dump()}
    )


@node
def check_threshold(ctx: Context, node_input: dict) -> Event:
    """Checks the expense amount against the configured threshold."""
    expense = node_input
    amount = expense.get("amount", 0.0)

    if amount < EXPENSE_THRESHOLD:
        reason = f"Auto-approved instantly (Amount ${amount:.2f} is under ${EXPENSE_THRESHOLD:.2f})"
        logging.info(reason)
        return Event(
            output={"status": "approved", "reason": reason},
            route="auto_approve",
            state={"status": "approved", "reason": reason}
        )
    else:
        logging.info(f"Expense of ${amount:.2f} requires LLM and human review.")
        return Event(
            output=expense,
            route="requires_review",
            message=f"Expense of ${amount:.2f} requires LLM and human review.",
            state={"status": "pending_review"}
        )


@node
def auto_approve(node_input: dict) -> Event:
    """Terminal node for the auto-approve flow."""
    reason = node_input.get("reason", "Auto-approved")
    return Event(
        output=node_input,
        message=reason
    )


@node
async def llm_risk_review(ctx: Context, node_input: dict) -> Event:
    """Evaluates the expense report for risk factors using Gemini."""
    expense = node_input

    prompt = f"""
You are an expert risk analysis agent reviewing corporate expense reports.
Analyze the following expense details and identify any risk factors (e.g., unusual category, vague description, potential policy violations):

Submitter: {expense.get('submitter')}
Amount: ${expense.get('amount', 0.0):.2f}
Category: {expense.get('category')}
Description: {expense.get('description')}
Date: {expense.get('date')}

Provide a concise summary of your risk analysis, listing any alerts or flags, and a final recommendation.
"""
    # Explicitly target the Vertex AI global endpoint.
    # Agent Runtime sets GOOGLE_CLOUD_LOCATION=us-east1, which doesn't serve
    # Gemini models. Passing http_options forces the correct endpoint.
    client = Client(
        vertexai=True,
        project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
        location="us-central1",
    )
    response = await client.aio.models.generate_content(
        model=MODEL_NAME,
        contents=prompt,
    )
    risk_summary = response.text or "No risk factors identified."

    logging.info(f"LLM Risk Analysis complete: {risk_summary[:100]}...")
    return Event(
        output=expense,
        message=f"Risk analysis complete: {risk_summary}",
        state={"risk_analysis": risk_summary}
    )


@node
def security_checkpoint(ctx: Context, node_input: dict) -> Event:
    """Checks the expense description for PII and prompt injection."""
    expense = node_input
    desc = expense.get("description", "")

    # 1. Check for prompt injection on raw description
    is_injection = detect_injection(desc)

    # 2. Scrub PII from description
    scrubbed_desc, redactions = scrub_pii(desc)

    # Update the expense description to the scrubbed version
    updated_expense = dict(expense)
    updated_expense["description"] = scrubbed_desc

    if is_injection:
        reason = "Prompt injection attempt detected"
        logging.warning(f"Security Warning: {reason} in expense from {expense.get('submitter')}.")
        return Event(
            output=updated_expense,
            route="injection_detected",
            state={
                "expense": updated_expense,
                "injection_flagged": True,
                "security_redactions": redactions,
                "risk_analysis": "[SECURITY WARNING] Prompt injection attempt detected in the expense description. LLM review was bypassed."
            }
        )
    else:
        return Event(
            output=updated_expense,
            route="proceed",
            state={
                "expense": updated_expense,
                "security_redactions": redactions
            }
        )


@node(rerun_on_resume=True)
async def human_approval(ctx: Context, node_input: dict) -> Event:
    """Pauses the workflow for human approval and records the decision upon resume."""
    if not ctx.resume_inputs or "approve_decision" not in ctx.resume_inputs:
        expense = ctx.state.get("expense", {})
        risk_summary = ctx.state.get("risk_analysis", "No risk analysis found.")
        redactions = ctx.state.get("security_redactions", [])
        redactions_str = f" (Redacted PII: {', '.join(redactions)})" if redactions else ""

        message = (
            f"=== EXPENSE APPROVAL REQUIRED ===\n"
            f"Submitter: {expense.get('submitter')}\n"
            f"Amount: ${expense.get('amount', 0.0):.2f}\n"
            f"Category: {expense.get('category')}\n"
            f"Description: {expense.get('description')}{redactions_str}\n"
            f"Date: {expense.get('date')}\n\n"
            f"--- Risk Analysis / Alert ---\n"
            f"{risk_summary}\n\n"
            f"Please respond with 'approve' or 'reject'."
        )

        yield RequestInput(
            interrupt_id="approve_decision",
            message=message
        )
        return

    # Process resume input
    decision = ctx.resume_inputs["approve_decision"]
    decision_str = str(decision).strip().lower()

    if "approve" in decision_str:
        status = "approved"
    elif "reject" in decision_str:
        status = "rejected"
    else:
        status = "rejected"  # Default safety fallback

    reason = f"Human decision: {decision}"
    logging.info(f"Human decision recorded: {status} ({reason})")

    yield Event(
        output={"status": status, "reason": reason},
        message=reason,
        state={"status": status, "reason": reason}
    )


# 3. Graph Assembly
edges = [
    ('START', parse_expense_event),
    (parse_expense_event, check_threshold),
    (check_threshold, {
        "auto_approve": auto_approve,
        "requires_review": security_checkpoint,
    }),
    (security_checkpoint, {
        "proceed": llm_risk_review,
        "injection_detected": human_approval,
    }),
    (llm_risk_review, human_approval),
]

root_agent = Workflow(
    name="expense_approval_workflow",
    edges=edges,
    state_schema=WorkflowState,
    output_schema=WorkflowOutput,
)

app = App(
    root_agent=root_agent,
    # Name must match the package directory so `adk web .` can locate it.
    name="expense_agent",
)
