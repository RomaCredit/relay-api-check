from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

CheckStatus = Literal["pass", "warn", "fail", "skipped", "unknown"]
ScanProfile = Literal["quick", "standard", "deep"]
ScanState = Literal["pending", "running", "done", "error", "cancelled"]


class StepKind(str, Enum):
    DISCOVERY = "discovery"
    PROTOCOL = "protocol"
    PARAMETERS = "parameters"
    AGENT = "agent"
    INGEST = "ingest"


@dataclass
class ComplianceCheck:
    id: str
    label: str
    status: CheckStatus
    detail: str = ""


@dataclass
class ProtocolCellResult:
    endpoint: str
    stream: bool
    model: str
    url: str
    http_status: int | None = None
    success: bool = False
    elapsed_ms: int | None = None
    content_type: str | None = None
    error_message: str | None = None
    diagnosis_code: str | None = None
    checks: list[ComplianceCheck] = field(default_factory=list)
    status: CheckStatus = "unknown"
    response_snippet: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "stream": self.stream,
            "model": self.model,
            "url": self.url,
            "httpStatus": self.http_status,
            "success": self.success,
            "elapsedMs": self.elapsed_ms,
            "contentType": self.content_type,
            "errorMessage": self.error_message,
            "diagnosisCode": self.diagnosis_code,
            "status": self.status,
            "checks": [
                {"id": c.id, "label": c.label, "status": c.status, "detail": c.detail}
                for c in self.checks
            ],
            "responseSnippet": self.response_snippet,
        }


@dataclass
class ModelDiscoveryResult:
    model_id: str
    source: Literal["api", "fallback", "user"]
    listed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.model_id, "source": self.source, "listed": self.listed}


@dataclass
class AgentProbeResult:
    agent_id: str
    label: str
    status: CheckStatus
    evidence: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.agent_id,
            "label": self.label,
            "status": self.status,
            "evidence": self.evidence,
            "detail": self.detail,
        }


@dataclass
class ScanStepEvent:
    kind: StepKind
    name: str
    status: Literal["started", "finished", "error"]
    detail: str = ""
    progress: float = 0.0

    def to_sse(self) -> dict[str, Any]:
        return {
            "type": "step",
            "kind": self.kind.value,
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "progress": self.progress,
        }


def mask_api_key(key: str) -> str:
    k = (key or "").strip()
    if len(k) <= 10:
        return "***"
    return f"{k[:6]}...{k[-4:]}"
