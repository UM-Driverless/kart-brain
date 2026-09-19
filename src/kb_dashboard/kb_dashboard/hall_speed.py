"""Pure-Python conversion of motor Hall counters into diagnostic speed data."""

from __future__ import annotations

import math
from numbers import Real
from typing import Any


_UINT32 = 1 << 32
_HALF_UINT32 = 1 << 31
_STALE_S = 2.5


class HallSpeed:
    """Track Hall counter deltas without inventing a calibration or sensor state."""

    def __init__(self, edges_per_metre: float = 0.0) -> None:
        if isinstance(edges_per_metre, bool) or not isinstance(edges_per_metre, Real):
            raise ValueError("edges_per_metre must be a finite number")
        if not math.isfinite(float(edges_per_metre)) or float(edges_per_metre) < 0:
            raise ValueError("edges_per_metre must be finite and non-negative")
        self.edges_per_metre = float(edges_per_metre)
        self._last: dict[str, Any] | None = None
        self._last_time: float | None = None
        self._baseline_edges: tuple[int, int, int] | None = None
        self._last_result: dict[str, Any] | None = None

    def ingest(self, sample: dict[str, Any], now: float) -> dict[str, Any]:
        """Accept one telemetry sample and return the current derived snapshot."""
        now = self._number(now, "now")
        parsed = self._parse(sample)
        if self._last_time is not None and now < self._last_time:
            self._invalidate()
            result = self._result(parsed, "invalid")
            self._last = parsed
            self._last_result = result
            return result
        stale_gap = self._last_time is not None and now - self._last_time > _STALE_S
        if stale_gap:
            self._invalidate()

        status = self._base_status(parsed)
        rate: float | None = None
        speed: float | None = None
        if status is None:
            edges = parsed["hall_edges"]
            if stale_gap:
                self._baseline_edges = edges
                status = "stale"
            elif self._baseline_edges is None:
                self._baseline_edges = edges
                status = "warming_up"
            elif self._multi_changed(parsed):
                self._baseline_edges = edges
                status = "ambiguous"
            elif self._last is None or self._last_time is None:
                self._baseline_edges = edges
                status = "warming_up"
            else:
                dt = now - self._last_time
                deltas = tuple(self._delta(a, b) for a, b in zip(edges, self._baseline_edges))
                if any(delta is None for delta in deltas):
                    self._invalidate()
                    self._baseline_edges = edges
                    status = "counter_reset"
                else:
                    counts = tuple(int(delta) for delta in deltas)
                    self._baseline_edges = edges
                    total = sum(counts)
                    if total == 0:
                        status = "no_edges"
                    elif 0 in counts:
                        status = "ambiguous"
                    elif dt <= 0 or not math.isfinite(dt):
                        self._invalidate()
                        status = "invalid"
                    else:
                        rate = total / dt
                        if self.edges_per_metre == 0:
                            status = "uncalibrated"
                        else:
                            speed = rate / self.edges_per_metre
                            status = "moving" if speed > 0 else "no_edges"
        if status in {"invalid", "unsupported", "capture_error"}:
            self._invalidate()
        result = self._result(parsed, status, rate, speed)
        self._last = parsed
        self._last_time = now
        self._last_result = result
        return result

    def snapshot(self, now: float) -> dict[str, Any]:
        """Return the last result, marking telemetry stale after a 2.5 s gap."""
        now = self._number(now, "now")
        if self._last is None:
            return self._result({}, "no_data")
        if self._last_time is None or now < self._last_time:
            return self._result(self._last, "invalid")
        if now - self._last_time > _STALE_S:
            self._invalidate()
            return self._result(self._last, "stale")
        return dict(self._last_result or self._result(self._last, "no_data"))

    @staticmethod
    def _number(value: Any, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
            raise ValueError(f"{name} must be a finite number")
        return float(value)

    @classmethod
    def _parse(cls, sample: dict[str, Any]) -> dict[str, Any]:
        keys = ("hall_init_err", "hall_bits", "hall_edges", "hall_age_ms", "hall_interval_us", "hall_multi_changes")
        if sample is None:
            return {key: None for key in keys}
        if not isinstance(sample, dict):
            return {key: None for key in keys} | {"_invalid": True}
        parsed = {key: sample.get(key) for key in keys}
        if "hall_init_err" not in sample:
            parsed["_invalid"] = True
        edges = sample.get("hall_edges")
        if edges is not None and (not isinstance(edges, (list, tuple)) or len(edges) != 3):
            parsed["hall_edges"] = None
            parsed["_invalid"] = True
        elif edges is not None:
            try:
                parsed["hall_edges"] = tuple(cls._edge(value) for value in edges)
            except ValueError:
                parsed["hall_edges"] = None
                parsed["_invalid"] = True
        init_err = sample.get("hall_init_err")
        if init_err is not None and (isinstance(init_err, bool) or not isinstance(init_err, int) or init_err < 0):
            parsed["hall_init_err"] = None
            parsed["_invalid"] = True
        bits = sample.get("hall_bits")
        if bits is not None and (isinstance(bits, bool) or not isinstance(bits, int) or bits < -1 or bits > 7):
            parsed["hall_bits"] = None
            parsed["_invalid"] = True
        if bits == -1 and not init_err:
            parsed["hall_bits"] = None
            parsed["_invalid"] = True
        for key in ("hall_init_err", "hall_bits"):
            value = sample.get(key)
            if value is None and key not in sample:
                parsed["_invalid"] = True
        for key in ("hall_age_ms", "hall_interval_us"):
            if key in sample and sample[key] is not None:
                try:
                    value = sample[key]
                    if isinstance(value, bool) or not isinstance(value, int) or not -1 <= value <= 2**31 - 1:
                        raise ValueError
                except ValueError:
                    parsed[key] = None
                    parsed["_invalid"] = True
        changes = sample.get("hall_multi_changes")
        if changes is not None and (isinstance(changes, bool) or not isinstance(changes, int) or not 0 <= changes < _UINT32):
            parsed["hall_multi_changes"] = None
            parsed["_invalid"] = True
        return parsed

    @staticmethod
    def _edge(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < _UINT32:
            raise ValueError("Hall counters must be unsigned 32-bit integers")
        return value

    @staticmethod
    def _delta(current: int, previous: int) -> int | None:
        delta = (current - previous) % _UINT32
        return None if delta >= _HALF_UINT32 else delta

    def _multi_changed(self, sample: dict[str, Any]) -> bool:
        if sample.get("hall_multi_changes") is None or self._last is None:
            return False
        previous = self._last.get("hall_multi_changes")
        return previous is not None and sample["hall_multi_changes"] != previous

    def _base_status(self, sample: dict[str, Any]) -> str | None:
        if sample.get("_invalid"):
            return "invalid"
        if sample.get("hall_init_err"):
            return "capture_error"
        if sample.get("hall_init_err") is None or sample.get("hall_edges") is None or sample.get("hall_bits") is None:
            return "unsupported"
        return None

    def _invalidate(self) -> None:
        self._baseline_edges = None
        self._last_result = None

    def _result(self, sample: dict[str, Any], status: str, rate: float | None = None, speed: float | None = None) -> dict[str, Any]:
        result = {key: sample.get(key) for key in ("hall_init_err", "hall_bits", "hall_edges", "hall_age_ms", "hall_interval_us", "hall_multi_changes")}
        result.update({"hall_status": status, "hall_edge_rate_hz": rate, "hall_speed_mps": speed, "hall_edges_per_metre": self.edges_per_metre})
        return result
