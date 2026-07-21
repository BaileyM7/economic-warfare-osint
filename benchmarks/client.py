"""HTTP client for the benchmark — talks to a live or local Emissary the way a
real client does, and times every call as wall-clock (what a user experiences).

State-agnostic: it never sets feature flags. It just runs against whatever
``base_url`` points at and records ``GET /api/health`` so the result set carries
proof of which BEFORE/AFTER state produced it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class Timed:
    """A response plus its wall-clock latency in seconds."""

    value: Any
    seconds: float
    extra: dict[str, Any] = field(default_factory=dict)


class EmissaryClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        poll_interval: float = 0.9,  # mirror the frontend's 900ms poll cadence
        analyze_timeout: float = 900.0,  # a cold run is ~6-7 min; allow slack
        request_timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.poll_interval = poll_interval
        self.analyze_timeout = analyze_timeout
        self._http = httpx.Client(timeout=request_timeout, follow_redirects=True)
        self._token: str | None = None

    # --- auth ----------------------------------------------------------------

    def login(self) -> None:
        r = self._http.post(
            f"{self.base_url}/api/auth/login",
            json={"username": self.username, "password": self.password},
        )
        r.raise_for_status()
        self._token = r.json()["access_token"]

    @property
    def _headers(self) -> dict[str, str]:
        if not self._token:
            raise RuntimeError("call login() first")
        return {"Authorization": f"Bearer {self._token}"}

    # --- the health oracle ---------------------------------------------------

    def health(self) -> dict[str, Any]:
        """The ground-truth state of every feature toggle (BEFORE vs AFTER)."""
        r = self._http.get(f"{self.base_url}/api/health")
        r.raise_for_status()
        return r.json()

    # --- scenarios -----------------------------------------------------------

    def suggest(self, query: str) -> Timed:
        t0 = time.perf_counter()
        r = self._http.post(
            f"{self.base_url}/api/analyze/suggest", json={"query": query}, headers=self._headers
        )
        dt = time.perf_counter() - t0
        r.raise_for_status()
        return Timed(value=r.json(), seconds=dt)

    def upsert_entity(
        self, entity_id: str, name: str, entity_type: str, country: str, notes: str
    ) -> None:
        r = self._http.post(
            f"{self.base_url}/api/knowledge/entities",
            json={
                "entity_id": entity_id,
                "name": name,
                "entity_type": entity_type,
                "country": country,
                "notes": notes,
            },
            headers=self._headers,
        )
        r.raise_for_status()

    def delete_entity(self, entity_id: str) -> None:
        """Best-effort cleanup so S1's seed entities don't linger in the target DB."""
        try:
            self._http.delete(
                f"{self.base_url}/api/knowledge/entities/{entity_id}", headers=self._headers
            )
        except Exception:  # noqa: BLE001
            pass

    def entity_similar(
        self,
        *,
        name: str | None = None,
        entity_id: str | None = None,
        backend: str | None = None,
        top_k: int = 5,
    ) -> Timed:
        body: dict[str, Any] = {"top_k": top_k}
        if entity_id:
            body["entity_id"] = entity_id
        if name:
            body["name"] = name
        if backend:
            body["backend"] = backend
        t0 = time.perf_counter()
        r = self._http.post(f"{self.base_url}/api/entity/similar", json=body, headers=self._headers)
        dt = time.perf_counter() - t0
        r.raise_for_status()
        return Timed(value=r.json(), seconds=dt)

    def analyze(
        self, query: str, *, session_id: str | None = None, force_fresh: bool = False
    ) -> Timed:
        """Run a full analysis: POST then poll to completed. Times POST→completed.

        Returns the terminal AnalysisStatus dict (status, result, events, progress,
        session_id, replayed_from, notice) plus latency. `extra` carries the
        number of polls and whether it was a semantic replay.
        """
        body: dict[str, Any] = {"query": query, "force_fresh": force_fresh}
        if session_id:
            body["session_id"] = session_id

        t0 = time.perf_counter()
        r = self._http.post(f"{self.base_url}/api/analyze", json=body, headers=self._headers)
        r.raise_for_status()
        started = r.json()
        analysis_id = started["analysis_id"]
        returned_session = started.get("session_id")

        polls = 0
        while True:
            if time.perf_counter() - t0 > self.analyze_timeout:
                raise TimeoutError(
                    f"analysis {analysis_id} did not complete in {self.analyze_timeout}s"
                )
            time.sleep(self.poll_interval)
            polls += 1
            pr = self._http.get(f"{self.base_url}/api/analyze/{analysis_id}", headers=self._headers)
            pr.raise_for_status()
            status = pr.json()
            if status.get("status") in ("completed", "failed"):
                dt = time.perf_counter() - t0
                return Timed(
                    value=status,
                    seconds=dt,
                    extra={
                        "polls": polls,
                        "session_id": status.get("session_id") or returned_session,
                        "replayed_from": status.get("replayed_from"),
                        "is_replay": bool(status.get("replayed_from")),
                    },
                )

    def memory_list(self) -> Timed:
        t0 = time.perf_counter()
        r = self._http.get(f"{self.base_url}/api/memory", headers=self._headers)
        dt = time.perf_counter() - t0
        # A "before" deploy without the endpoint 404s — treat as feature-absent.
        if r.status_code == 404:
            return Timed(value=None, seconds=dt, extra={"absent": True})
        r.raise_for_status()
        return Timed(value=r.json(), seconds=dt)

    def memory_search(self, query: str) -> Timed:
        t0 = time.perf_counter()
        r = self._http.get(
            f"{self.base_url}/api/memory/search", params={"q": query}, headers=self._headers
        )
        dt = time.perf_counter() - t0
        if r.status_code == 404:
            return Timed(value=None, seconds=dt, extra={"absent": True})
        r.raise_for_status()
        return Timed(value=r.json(), seconds=dt)

    def usage_latency(self) -> dict[str, Any] | None:
        """Server-recorded latency_ms (corroborator). None if not admin/available."""
        try:
            r = self._http.get(f"{self.base_url}/api/admin/usage", headers=self._headers)
            if r.status_code != 200:
                return None
            return r.json()
        except Exception:  # noqa: BLE001
            return None

    def close(self) -> None:
        self._http.close()
