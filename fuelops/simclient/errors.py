"""Typed simulator error taxonomy (pipeline A18). Raw wire JSON never leaves the ACL."""

from __future__ import annotations

from typing import Any


class SimulatorError(Exception):
    retryable = False
    kind = "unknown"

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class SimTimeout(SimulatorError):
    retryable = True
    kind = "timeout"


class SimUnavailable(SimulatorError):
    """Connection refused, or an injected 503 FAULT_INJECTED."""

    retryable = True
    kind = "unavailable"


class SimTransient(SimulatorError):
    retryable = True
    kind = "transient"


class SimCircuitOpen(SimulatorError):
    kind = "circuit_open"


class SimContractViolation(SimulatorError):
    kind = "contract_violation"


class SimRejected(SimulatorError):
    """A domain answer: 404 / 409 / 422 with the simulator's code (e.g. ROUTE_DISRUPTED)."""

    kind = "rejected"


class SimAmbiguous(SimulatorError):
    """A write whose outcome is unknown (timeout / 503 mid-POST): reconcile before retrying."""

    kind = "ambiguous"


def envelope_code(body: Any) -> str | None:
    """Code from any simulator envelope; ``VALIDATION`` for FastAPI 422 lists."""
    if not isinstance(body, dict):
        return None
    if isinstance(body.get("detail"), list):
        return "VALIDATION"
    for key in ("detail", "error"):
        inner = body.get(key)
        if isinstance(inner, dict) and isinstance(inner.get("code"), str):
            return str(inner["code"])
    return None


def envelope_message(body: Any) -> str:
    if isinstance(body, dict):
        for key in ("detail", "error"):
            inner = body.get(key)
            if isinstance(inner, dict) and inner.get("message"):
                return str(inner["message"])
    return ""


def map_status(status: int, body: Any) -> SimulatorError:
    code = envelope_code(body)
    message = envelope_message(body) or f"HTTP {status}"
    if status == 503:
        return SimUnavailable(message, status=status, code=code or "UNAVAILABLE")
    if status in (408, 429) or status >= 500:
        return SimTransient(message, status=status, code=code)
    if status in (404, 409, 422):
        return SimRejected(message, status=status, code=code or str(status))
    return SimContractViolation(f"unexpected HTTP {status}", status=status, code=code)
