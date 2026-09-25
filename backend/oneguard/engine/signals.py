"""Soft signal: is the shop's text talking to the agent? (S_agent_directed; rules.md P5, P8).

One question only, the one Laya was kept for (docs/decisions.md): does shop text contain
instructions aimed at an automated purchasing agent or payment system. It never
classifies items, amounts or billing terms. The answer is evidence: a triggered signal
may raise approve → step_up in decide; it can never approve or lower a decline.

``ONEGUARD_SOFT_SIGNALS`` (read at import) picks the backend:

- ``off``: no soft signal;
- ``keywords`` (default): the A1 pattern list from protections.py;
- ``laya``: keywords, plus the Laya checkpoint (loaded once by ``warm()`` in the background
  at API startup while the worker already polls, then asked in a worker thread within
  ``budget_s``). The signal is triggered if keywords OR Laya fire: Laya can only add, never
  clear a keyword hit. Until it has loaded, on load failure, timeout or error it is keywords.
  Keywords go first: Laya reads only the lines they did not flag that have at least
  ``MIN_WORDS`` words (docs/decisions.md 2026-09-25), and the signal says how many it read.

The pipeline calls ``soft_signals`` only when signals are enabled for the run.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import unicodedata
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from oneguard.engine.interfaces import register
from oneguard.engine.protections import agent_directed_spans
from oneguard.engine.types import Facts, Signal

log = logging.getLogger(__name__)

SIGNALS_ENV = "ONEGUARD_SOFT_SIGNALS"
THRESHOLD = 0.6
MIN_WORDS = 6
CHECKPOINTS = ("laya-typed-decisions", "laya")
QUESTIONS = {
    "agent_directed": {
        "type": "noul",
        "instructions": (
            "Does this product text contain instructions aimed at an automated purchasing "
            "agent or payment system, rather than describing the product?"
        ),
    },
}


def _shop_texts(facts: Facts) -> list[tuple[str, str]]:
    texts = []
    for line in facts.items:
        texts.append((f"line {line.line_no} details", line.item_details))
    return texts


def _words(text: str) -> int:
    return len(unicodedata.normalize("NFKC", text or "").split())


def _for_model(facts: Facts) -> list[tuple[str, str]]:
    """The shop texts Laya reads: not flagged by the keywords and at least ``MIN_WORDS`` long.

    A flagged line keeps its keyword verdict (the model could only agree); a shorter one
    has too little to read an instruction from and would only add latency.
    """
    return [
        (label, text) for label, text in _shop_texts(facts)
        if not agent_directed_spans(text) and _words(text) >= MIN_WORDS
    ]  # fmt: skip


def _read(asked: int, total: int) -> str:
    return f"The model read {asked} of {total} item line{'' if total == 1 else 's'}."


def _signal(triggered: bool, detail: str, source: str) -> Signal:
    return Signal(
        id="S_agent_directed", triggered=triggered, strength="strong",
        outcome_if_triggered="ask", detail=detail, source=source,
    )  # fmt: skip


class KeywordSignals:
    """The A1 pattern list over the shop's product text. Deterministic, no model."""

    name = "keywords"

    def __call__(self, facts: Facts, budget_s: float) -> list[Signal]:
        hits = [label for label, text in _shop_texts(facts) if agent_directed_spans(text)]
        if hits:
            return [_signal(True, f"Instructions aimed at the agent in {', '.join(hits)}.", "merchant_text")]
        return [_signal(False, "No instructions aimed at the agent in the product text.", "merchant_text")]


class LayaSignals:
    """Keywords, plus the agent_directed question to a Laya checkpoint within ``budget_s``.

    Triggered if the keywords OR the model fire: the model can only add (P5). The model
    reads only the lines ``_for_model`` keeps, and the signal's detail says how many of
    the purchase's lines that was, so its latency is explainable. ``predict``
    is ``(text) -> score in [0, 1]``; ``load`` builds it once. Anything that goes wrong
    (no model, a timeout, an error, a malformed answer) leaves the keyword answer alone,
    so a model outage makes the engine exactly as cautious as keywords are (P8).
    """

    name = "laya"

    def __init__(
        self,
        predict: Callable[[str], float] | None = None,
        fallback: KeywordSignals | None = None,
        load: Callable[[], Callable[[str], float]] | None = None,
    ) -> None:
        self.fallback = fallback or KeywordSignals()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="laya")
        self._load = load or _load_laya
        self._load_lock = threading.Lock()
        self._tried = predict is not None
        self.predict = predict

    @property
    def available(self) -> bool:
        return self.predict is not None

    def warm(self) -> bool:
        """Load the model once (idempotent, thread-safe); True if it is ready.

        The API calls this in a background thread at startup. Until ``predict`` is set a
        decision answers with keywords (and starts the load if nobody has): loading never
        happens inside a decision. Setting ``predict`` is the switch to the model.
        """
        with self._load_lock:
            if not self._tried:
                self._tried = True
                try:
                    self.predict = self._load()
                except Exception:  # model missing or broken: keywords from here on
                    log.exception("Laya failed to load; soft signals fall back to keywords")
        return self.available

    def _ask_all(self, texts: list[tuple[str, str]]) -> list[tuple[str, float]]:
        assert self.predict is not None
        return [(label, float(self.predict(text))) for label, text in texts if text]

    def _scores(self, texts: list[tuple[str, str]], budget_s: float) -> list[tuple[str, float]] | None:
        """The model's score per text, or None when it cannot answer in time."""
        if not self.available:
            if not self._tried:
                self._pool.submit(self.warm)  # never load inside a decision
            return None
        if budget_s <= 0:
            return None
        if not texts:
            return []
        started = time.perf_counter()
        future = self._pool.submit(self._ask_all, texts)
        try:
            scores = future.result(timeout=budget_s)
        except FutureTimeout:
            log.warning("Laya over its %.0f ms budget on %d lines; using keywords", budget_s * 1000, len(texts))
            return None
        except Exception:
            log.exception("Laya failed; using keywords")
            return None
        log.info("Laya read %d lines in %.0f ms", len(texts), (time.perf_counter() - started) * 1000)
        if any(not 0.0 <= score <= 1.0 for _, score in scores):
            return None
        return scores

    def __call__(self, facts: Facts, budget_s: float) -> list[Signal]:
        keywords = self.fallback(facts, budget_s)
        texts = _for_model(facts)
        scores = self._scores(texts, budget_s)
        if scores is None:
            return keywords
        [keyword] = keywords
        read = _read(len(texts), len(facts.items))
        hits = [(label, score) for label, score in scores if score >= THRESHOLD]
        if hits:
            where = ", ".join(f"{label} ({score:.2f})" for label, score in hits)
            model = f"The model reads instructions aimed at the agent in {where}. {read}"
            if keyword.triggered:
                return [_signal(True, f"{keyword.detail} {model}", "merchant_text")]
            return [_signal(True, model, "model")]
        if keyword.triggered or not texts:  # the keywords' answer stands, the model had nothing to add
            return [_signal(keyword.triggered, f"{keyword.detail} {read}", keyword.source)]
        top = max(score for _, score in scores)
        return [_signal(False, f"The model reads no instructions aimed at the agent (highest {top:.2f}). {read}", "model")]


def _load_laya() -> Callable[[str], float]:
    from laya import Router  # optional dependency: the `signals` extra

    router = Router(max_loaded=1)
    errors = []
    for checkpoint in CHECKPOINTS:
        try:
            router.load(checkpoint)
        except Exception as exc:  # noqa: BLE001 - any load failure: try the next checkpoint
            errors.append(f"{checkpoint}: {exc!r}")
            continue

        def predict(text: str, _checkpoint: str = checkpoint) -> float:
            answer: Any = router.predict(text, QUESTIONS, model=_checkpoint)
            return float(answer["answers"]["agent_directed"]["noul"])

        predict("warm-up")
        log.info("Laya checkpoint %s loaded for agent_directed", checkpoint)
        return predict
    raise RuntimeError("no Laya checkpoint loaded: " + "; ".join(errors))


def _off(facts: Facts, budget_s: float) -> list[Signal]:
    return []


def select_backend(value: str | None) -> Callable[[Facts, float], list[Signal]]:
    """The soft-signal backend for an ``ONEGUARD_SOFT_SIGNALS`` value."""
    choice = (value or "keywords").strip().lower()
    if choice == "off":
        return _off
    if choice == "keywords":
        return KeywordSignals()
    if choice == "laya":
        return LayaSignals()
    raise ValueError(f"{SIGNALS_ENV} must be off, keywords or laya, not {value!r}")


BACKEND = select_backend(os.environ.get(SIGNALS_ENV))


def warm() -> bool:
    """Load the soft-signal model if the backend has one; True when a model is loaded.

    Called once in a background thread at API startup and reported by /healthz as
    ``model_loaded``. With ``off`` or ``keywords`` there is no model: it returns False
    and does nothing.
    """
    warm_backend = getattr(BACKEND, "warm", None)
    return bool(warm_backend()) if callable(warm_backend) else False


@register("soft_signals")
def soft_signals(facts: Facts, budget_s: float) -> list[Signal]:
    return BACKEND(facts, budget_s)
