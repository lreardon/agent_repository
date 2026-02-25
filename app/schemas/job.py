"""Pydantic v2 schemas for Job lifecycle endpoints."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class JobProposal(BaseModel):
    """Client proposes a job.

    acceptance_criteria supports two modes:

    1. **Declarative tests** (original):
       ```json
       {
         "version": "1.0",
         "tests": [{"test_id": "...", "type": "json_schema", "params": {...}}],
         "pass_threshold": "all"
       }
       ```

    2. **Script-based** (acceptance criteria as code):
       ```json
       {
         "version": "2.0",
         "script": "<base64-encoded verification script>",
         "runtime": "python:3.11",
         "timeout_seconds": 60,
         "memory_limit_mb": 256
       }
       ```
       The script receives the deliverable at /input/result.json.
       Exit code 0 = pass (escrow released), non-zero = fail (escrow refunded).
       Scripts run in isolated Docker containers with no network access.
    """
    seller_agent_id: uuid.UUID
    listing_id: uuid.UUID | None = None
    acceptance_criteria: dict | None = None
    requirements: dict | None = None
    max_budget: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)
    delivery_deadline: datetime | None = None
    max_rounds: int = Field(5, ge=1, le=20)

    @field_validator("acceptance_criteria")
    @classmethod
    def validate_acceptance_criteria(cls, v: dict | None) -> dict | None:
        if v is None:
            return v
        # Script-based criteria get validated at creation time
        if v.get("script"):
            from app.services.sandbox import validate_script_criteria
            validate_script_criteria(v)
        return v

    @field_validator("max_budget")
    @classmethod
    def validate_budget(cls, v: Decimal) -> Decimal:
        if v > Decimal("1000000"):
            raise ValueError("Maximum budget is 1,000,000 credits")
        return v


class CounterProposal(BaseModel):
    """Either party counters with new terms."""
    proposed_price: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)
    counter_terms: dict | None = None
    accepted_terms: list[str] | None = None
    message: str | None = Field(None, max_length=2048)

    @field_validator("proposed_price")
    @classmethod
    def validate_price(cls, v: Decimal) -> Decimal:
        if v > Decimal("1000000"):
            raise ValueError("Maximum price is 1,000,000 credits")
        return v


class AcceptJob(BaseModel):
    """Accept current terms. Seller must include acceptance_criteria_hash to prove
    they have reviewed the verification script/criteria."""
    acceptance_criteria_hash: str | None = Field(
        None,
        description="SHA-256 hash of the acceptance_criteria dict. "
                    "Required when the job has acceptance criteria. "
                    "Proves the accepting party has reviewed the verification logic.",
    )


class DeliverPayload(BaseModel):
    """Seller delivers result."""
    result: dict | list


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: uuid.UUID
    client_agent_id: uuid.UUID
    seller_agent_id: uuid.UUID
    listing_id: uuid.UUID | None
    a2a_task_id: str | None = None
    a2a_context_id: str | None = None
    status: str
    acceptance_criteria: dict | None
    acceptance_criteria_hash: str | None = None
    requirements: dict | None
    agreed_price: Decimal | None
    delivery_deadline: datetime | None
    negotiation_log: list | None
    max_rounds: int
    current_round: int
    result: dict | list | None
    created_at: datetime
    updated_at: datetime

    @field_validator("status", mode="before")
    @classmethod
    def serialize_status(cls, v: object) -> str:
        if hasattr(v, "value"):
            return v.value
        return str(v)

    @model_validator(mode="after")
    def redact_result_unless_completed(self) -> "JobResponse":
        """Redact deliverable from responses unless the job is completed.

        Prevents clients from extracting work product without paying —
        e.g. proposing a rigged verification script, letting it fail,
        and reading the result from the job response.
        """
        if self.status != "completed":
            self.result = None
        return self


class TestResultItem(BaseModel):
    """Single acceptance test result."""
    test_id: str
    passed: bool
    message: str = ""


class VerifyResponse(BaseModel):
    """Verification result including per-test details."""
    job: JobResponse
    verification: dict | None = None  # {passed, threshold, results[], summary}
