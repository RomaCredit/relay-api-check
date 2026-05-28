"""进程内扫描任务存储 + SSE 订阅。"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from auto_scan.models import ScanState, ScanStepEvent

SCAN_TTL_SEC = 3600
MAX_SCANS_PER_IP_HOUR = 20


@dataclass
class ScanRecord:
    scan_id: str
    client_ip: str
    state: ScanState = "pending"
    progress: float = 0.0
    report: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    # 敏感：仅内存保存
    api_key: str = ""
    base_url: str = ""
    profile: str = "standard"

    def publish(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        for q in list(self.subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        for past in self.events:
            await q.put(past)
        self.subscribers.append(q)
        try:
            while True:
                item = await q.get()
                yield item
                if item.get("type") in ("done", "error"):
                    break
        finally:
            if q in self.subscribers:
                self.subscribers.remove(q)


class ScanStore:
    def __init__(self) -> None:
        self._scans: dict[str, ScanRecord] = {}
        self._ip_active: dict[str, str] = {}
        self._ip_hour: dict[str, list[float]] = {}

    def _gc(self) -> None:
        now = time.time()
        dead = [sid for sid, r in self._scans.items() if now - r.created_at > SCAN_TTL_SEC]
        for sid in dead:
            rec = self._scans.pop(sid, None)
            if rec and self._ip_active.get(rec.client_ip) == sid:
                del self._ip_active[rec.client_ip]

    def check_rate_limit(self, ip: str) -> None:
        self._gc()
        if ip in self._ip_active:
            active = self._ip_active[ip]
            rec = self._scans.get(active)
            if rec and rec.state in ("pending", "running"):
                raise ValueError("该 IP 已有进行中的扫描，请稍后再试")
        now = time.time()
        bucket = [t for t in self._ip_hour.get(ip, []) if now - t < 3600]
        if len(bucket) >= MAX_SCANS_PER_IP_HOUR:
            raise ValueError("扫描次数过多，请一小时后再试")
        bucket.append(now)
        self._ip_hour[ip] = bucket

    def create(
        self,
        *,
        client_ip: str,
        base_url: str,
        api_key: str,
        profile: str,
    ) -> ScanRecord:
        self.check_rate_limit(client_ip)
        scan_id = uuid.uuid4().hex[:16]
        rec = ScanRecord(
            scan_id=scan_id,
            client_ip=client_ip,
            base_url=base_url,
            api_key=api_key,
            profile=profile,
            report={
                "schemaVersion": 1,
                "meta": {
                    "baseUrl": base_url,
                    "profile": profile,
                    "scanId": scan_id,
                },
                "models": [],
                "protocols": {},
                "parameters": {},
                "agents": {},
                "summary": {},
            },
        )
        self._scans[scan_id] = rec
        self._ip_active[client_ip] = scan_id
        return rec

    def get(self, scan_id: str) -> ScanRecord | None:
        self._gc()
        return self._scans.get(scan_id)

    def release_ip(self, rec: ScanRecord) -> None:
        if self._ip_active.get(rec.client_ip) == rec.scan_id:
            del self._ip_active[rec.client_ip]


STORE = ScanStore()
