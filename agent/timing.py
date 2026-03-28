"""
LLMTiming — measures actual LLM response times and derives all agent intervals.

Run once on startup via benchmark(). All event bus and screen reader intervals
are set from measured values, not hardcoded.

Derived intervals:
  idle_timeout        = max(reactive_ms * 1.5, 3000) ms  — normal gameplay
  combat_idle_timeout = max(reactive_ms * 1.1, 1500) ms  — urgent combat mode
  vision_interval     = max(vision_ms * 2.0, 5000)  ms  — between vision reads
  scan_interval       = 60.0 s normal / 5.0 s in combat

Rolling average: recalibrates every RECAL_INTERVAL calls as model load changes.
"""
import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Recalibrate rolling average every N completed LLM calls
RECAL_INTERVAL = 50

# Hard lower bounds (seconds) regardless of measured speed
MIN_IDLE_S         = 3.0
MIN_COMBAT_IDLE_S  = 1.5
MIN_VISION_S       = 5.0

# Hard upper bounds — if model is this slow, use fallback strategy
MAX_VISION_S       = 20.0   # above this: skip vision, use engine-only


class LLMTiming:
    """
    Benchmarks LLM response times and publishes derived intervals.
    Safe to query before benchmark() completes — returns safe defaults.
    """

    def __init__(self) -> None:
        self._reactive_ms: float = 3000.0   # default until measured
        self._vision_ms:   float = 8000.0
        self._reactive_samples: list[float] = []
        self._vision_samples:   list[float] = []
        self._call_count: int = 0
        self._benchmarked: bool = False

    # ── Derived intervals (always safe to call) ───────────────────────────────

    @property
    def idle_timeout(self) -> float:
        """Seconds between LLM calls during normal gameplay."""
        return max(self._reactive_ms / 1000 * 1.5, MIN_IDLE_S)

    @property
    def combat_idle_timeout(self) -> float:
        """Seconds between LLM calls during combat (urgent mode)."""
        return max(self._reactive_ms / 1000 * 1.1, MIN_COMBAT_IDLE_S)

    @property
    def vision_interval(self) -> float:
        """Minimum seconds between vision LLM reads."""
        return max(self._vision_ms / 1000 * 2.0, MIN_VISION_S)

    @property
    def vision_enabled(self) -> bool:
        """False if vision LLM is too slow to be useful."""
        return self._vision_ms < MAX_VISION_S * 1000

    # ── Benchmark ─────────────────────────────────────────────────────────────

    async def benchmark(self, llm: Any) -> None:
        """
        Fire a minimal reactive call and a minimal vision call to measure latency.
        Sets initial intervals. Safe to call once on startup.
        """
        logger.info("[Timing] Benchmarking LLM response times...")

        reactive_ms = await self._measure_reactive(llm)
        if reactive_ms:
            self._reactive_ms = reactive_ms
            logger.info(f"[Timing] Reactive: {reactive_ms:.0f}ms → "
                        f"idle={self.idle_timeout:.1f}s  combat={self.combat_idle_timeout:.1f}s")

        vision_ms = await self._measure_vision(llm)
        if vision_ms:
            self._vision_ms = vision_ms
            logger.info(f"[Timing] Vision: {vision_ms:.0f}ms → "
                        f"interval={self.vision_interval:.1f}s  enabled={self.vision_enabled}")
        else:
            logger.warning(f"[Timing] Vision benchmark failed — using default {self._vision_ms:.0f}ms")

        self._benchmarked = True
        logger.info(f"[Timing] Benchmark complete. "
                    f"idle={self.idle_timeout:.1f}s  combat={self.combat_idle_timeout:.1f}s  "
                    f"vision={self.vision_interval:.1f}s  vision_ok={self.vision_enabled}")

    async def _measure_reactive(self, llm: Any) -> float | None:
        try:
            t0 = time.time()
            await llm.complete(
                system="You are a timer.",
                user="Reply with exactly: OK",
                task="reactive",
                max_tokens=10,
            )
            ms = (time.time() - t0) * 1000
            return ms
        except Exception as e:
            logger.warning(f"[Timing] Reactive benchmark error: {e}")
            return None

    async def _measure_vision(self, llm: Any) -> float | None:
        try:
            from screen_reader import ScreenReader
            sr = ScreenReader(llm)
            frame = await asyncio.to_thread(sr.capture_frame)
            if not frame:
                logger.warning("[Timing] Vision benchmark: no frame captured")
                return None
            t0 = time.time()
            await llm.complete_vision(
                system="You are a timer.",
                user="Describe this image in 5 words.",
                img_bytes=frame,
                task="vision",
            )
            ms = (time.time() - t0) * 1000
            return ms
        except Exception as e:
            logger.warning(f"[Timing] Vision benchmark error: {e}")
            return None

    # ── Rolling recalibration ─────────────────────────────────────────────────

    def record_reactive(self, elapsed_s: float) -> None:
        """Call after each reactive LLM call completes."""
        self._reactive_samples.append(elapsed_s * 1000)
        self._call_count += 1
        if self._call_count % RECAL_INTERVAL == 0:
            self._recalibrate()

    def record_vision(self, elapsed_s: float) -> None:
        """Call after each vision LLM call completes."""
        self._vision_samples.append(elapsed_s * 1000)

    def _recalibrate(self) -> None:
        """Update intervals from recent sample averages."""
        if len(self._reactive_samples) >= 5:
            recent = self._reactive_samples[-20:]  # last 20 samples
            self._reactive_ms = sum(recent) / len(recent)
        if len(self._vision_samples) >= 3:
            recent = self._vision_samples[-10:]
            self._vision_ms = sum(recent) / len(recent)
        logger.info(f"[Timing] Recalibrated — reactive={self._reactive_ms:.0f}ms "
                    f"vision={self._vision_ms:.0f}ms "
                    f"idle={self.idle_timeout:.1f}s combat={self.combat_idle_timeout:.1f}s")
