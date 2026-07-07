from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Callable

logger = logging.getLogger(__name__)


def build_companionship_alert_message(
    camera_name: str,
    alert_state: str = "CHILD_ALONE",
) -> str:
    clean_name = " ".join(str(camera_name).replace("_", " ").split())
    if not clean_name:
        clean_name = "camera nao identificada"
    if alert_state == "CHILD_SEPARATED":
        return f"Alerta, {clean_name}. Crianca se afastando do acompanhante."
    return f"Alerta, {clean_name}. Crianca desacompanhada."


class MvpVoiceAnnouncer:
    """Serializa e repete alertas ativos do MVP sem sobrepor audios."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        repeat_interval_seconds: float = 10.0,
        rate: int = 175,
        volume: float = 1.0,
        engine_factory: Callable[[], Any] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.enabled = enabled
        self.repeat_interval_seconds = repeat_interval_seconds
        self.rate = rate
        self.volume = volume
        self.engine_factory = engine_factory or self._default_engine_factory
        self.monotonic = monotonic
        self._active: OrderedDict[str, str] = OrderedDict()
        self._condition = threading.Condition()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._next_play_at = 0.0

    @staticmethod
    def _default_engine_factory() -> Any:
        import pyttsx3

        return pyttsx3.init()

    def activate_companionship_alert(
        self,
        camera_name: str,
        alert_state: str = "CHILD_ALONE",
    ) -> bool:
        """Ativa ou mantem um alerta por camera; duplicatas nao criam nova voz."""

        if not self.enabled:
            return False
        message = build_companionship_alert_message(camera_name, alert_state)
        with self._condition:
            is_new = camera_name not in self._active
            if is_new:
                self._active[camera_name] = message
                if len(self._active) == 1:
                    self._next_play_at = self.monotonic()
                else:
                    self._active.move_to_end(camera_name, last=False)
            self._ensure_thread_locked()
            self._condition.notify_all()
        return is_new

    def deactivate_alert(self, camera_name: str) -> bool:
        with self._condition:
            removed = self._active.pop(camera_name, None) is not None
            self._condition.notify_all()
            return removed

    def active_camera_names(self) -> tuple[str, ...]:
        with self._condition:
            return tuple(self._active)

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._active.clear()
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _ensure_thread_locked(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._worker,
            daemon=True,
            name="pediatria-mvp-voice",
        )
        self._thread.start()

    def _worker(self) -> None:
        while True:
            with self._condition:
                while not self._closed and not self._active:
                    self._condition.wait()
                if self._closed:
                    return
                wait_seconds = self._next_play_at - self.monotonic()
                if wait_seconds > 0:
                    self._condition.wait(timeout=wait_seconds)
                    continue
                camera_name, message = self._active.popitem(last=False)
                self._active[camera_name] = message
                active_count = len(self._active)

            engine: Any | None = None
            try:
                # A fresh engine per utterance avoids a Windows SAPI/pyttsx3
                # issue where a reused engine may speak only the first alert.
                engine = self.engine_factory()
                engine.setProperty("rate", self.rate)
                engine.setProperty("volume", self.volume)
                logger.info(
                    "event=pediatria_mvp_voice_play camera_id=%s active_count=%s",
                    camera_name,
                    active_count,
                )
                engine.say(message)
                engine.runAndWait()
            except Exception as exc:
                logger.warning(
                    "event=pediatria_mvp_voice_failed camera_id=%s reason=%s",
                    camera_name,
                    exc.__class__.__name__,
                )
            finally:
                if engine is not None:
                    try:
                        engine.stop()
                    except Exception:
                        logger.debug(
                            "event=pediatria_mvp_voice_stop_failed camera_id=%s",
                            camera_name,
                        )
                with self._condition:
                    self._next_play_at = (
                        self.monotonic() + self.repeat_interval_seconds
                    )
                    self._condition.notify_all()
