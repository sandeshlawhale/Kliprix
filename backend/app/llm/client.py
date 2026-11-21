"""The only module that talks to LiteLLM (docs/10).

One call = one chat completion that must return JSON matching a Pydantic schema. Invalid
JSON gets one repair retry; 429/5xx/timeouts are retried with backoff (2 s, 6 s, 15 s).
Errors map to LLM_AUTH_FAILED, LLM_RATE_LIMITED, or LLM_BAD_RESPONSE. The key is passed
per call and never logged.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from pydantic import TypeAdapter, ValidationError

from app.config import LLM_BACKOFF_S, LLM_TIMEOUT_S
from app.core.errors import AppError, ErrorCode
from app.core.logging import redact
from app.llm.session import LlmSession

logger = logging.getLogger(__name__)

T = TypeVar("T")
Completion = Callable[..., Any]
Sleep = Callable[[float], None]

# How much of a bad answer is sent back in the repair request.
REPAIR_EXCERPT_CHARS = 4000
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def litellm_model(session: LlmSession) -> str:
    """LiteLLM's provider-prefixed model string (checked against litellm.get_llm_provider)."""
    prefix = "openai" if session.provider == "openai_compatible" else session.provider
    return f"{prefix}/{session.model}"


def _load_litellm() -> Any:
    # LiteLLM downloads a model price list from GitHub on import unless told to use its
    # bundled copy; that request is not an allowed network call (docs/11).
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    import litellm  # heavy: imported on demand

    litellm.suppress_debug_info = True
    return litellm


_preload_started = threading.Lock()


def preload_litellm() -> threading.Thread | None:
    """Import LiteLLM on a background thread so the first LLM call does not pay for it.

    A cold `import litellm` measured 21.9 s on the owner's PC, which made the first
    "Test connection" take ~20 s although the provider answered in about a second. Python's
    import lock makes a request that arrives mid-import wait for this thread, not import
    twice. Returns None when the preload already started.
    """
    if not _preload_started.acquire(blocking=False):
        return None

    def run() -> None:
        started = time.monotonic()
        try:
            _load_litellm()
        except Exception:  # the real call reports the problem with a proper error code
            logger.exception("litellm preload failed")
            return
        logger.info("litellm preloaded in %.1f s", time.monotonic() - started)

    thread = threading.Thread(target=run, name="litellm-preload", daemon=True)
    thread.start()
    return thread


def default_completion(**kwargs: Any) -> Any:
    return _load_litellm().completion(**kwargs)


def extract_json(text: str) -> Any:
    """Parse JSON from a reply, tolerating Markdown code fences and text around one value.

    Raises:
        ValueError: no JSON value could be parsed.
    """
    cleaned = _FENCE.sub("", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = cleaned.find(opener), cleaned.rfind(closer)
        if 0 <= start < end:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("the reply is not valid JSON")


class LlmClient:
    """Calls the session's model. `completion` and `sleep` are replaceable in tests only."""

    def __init__(
        self,
        session: LlmSession,
        completion: Completion | None = None,
        sleep: Sleep = time.sleep,
        backoff_s: Sequence[float] = LLM_BACKOFF_S,
        timeout_s: float = LLM_TIMEOUT_S,
    ) -> None:
        self._session = session
        self._timeout_s = timeout_s
        self._completion = completion or default_completion
        self._sleep = sleep
        self._backoff_s = tuple(backoff_s)

    def _call(
        self, messages: list[dict[str, str]], temperature: float, max_tokens: int | None = None
    ) -> str:
        """One completion with retries on rate limits and server errors; returns the text."""
        kwargs: dict[str, Any] = {
            "model": litellm_model(self._session),
            "messages": messages,
            "temperature": temperature,
            "timeout": self._timeout_s,
            "api_key": self._session.api_key,
            "num_retries": 0,
            # Some models reject settings such as `temperature` (e.g. gemini-3.5-flash-lite);
            # LiteLLM then drops them instead of failing the call.
            "drop_params": True,
        }
        if self._session.base_url:
            kwargs["api_base"] = self._session.base_url
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        attempt = 0
        while True:
            started = time.monotonic()
            try:
                response = self._completion(**kwargs)
            except Exception as exc:  # classified below and re-raised as AppError
                if _is_transient(exc) and attempt < len(self._backoff_s):
                    wait = self._backoff_s[attempt]
                    logger.warning("llm call failed (%s), retry in %.0f s", _name(exc), wait)
                    self._sleep(wait)
                    attempt += 1
                    continue
                raise _app_error(classify_error(exc), exc, self._session.api_key) from exc
            logger.info(
                "llm call provider=%s model=%s latency_ms=%d",
                self._session.provider,
                self._session.model,
                (time.monotonic() - started) * 1000,
            )
            return _reply_text(response)

    def complete_json(
        self,
        system: str,
        user: str,
        schema: type[T] | TypeAdapter[T],
        temperature: float,
    ) -> T:
        """Ask for JSON and validate it; one repair retry, then LLM_BAD_RESPONSE."""
        adapter: TypeAdapter[T] = schema if isinstance(schema, TypeAdapter) else TypeAdapter(schema)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        reply = self._call(messages, temperature)
        try:
            return adapter.validate_python(extract_json(reply))
        except (ValueError, ValidationError) as exc:
            problem = short_problem(exc)
            logger.warning("llm reply invalid, asking for a repair: %s", problem)
        repair = [
            *messages,
            {"role": "assistant", "content": reply[:REPAIR_EXCERPT_CHARS]},
            {
                "role": "user",
                "content": (
                    "Your previous answer was not valid for the required JSON schema. "
                    f"Problem: {problem}\nReturn only the corrected JSON, nothing else."
                ),
            },
        ]
        reply = self._call(repair, temperature)
        try:
            return adapter.validate_python(extract_json(reply))
        except (ValueError, ValidationError) as exc:
            logger.error("llm reply invalid after repair: %s", short_problem(exc))
            raise AppError(
                ErrorCode.LLM_BAD_RESPONSE,
                "The AI model returned an answer in the wrong format twice.",
            ) from exc

    def ping(self) -> None:
        """A tiny request that proves the key and model work."""
        # No max_tokens: "thinking" models spend output tokens before answering, and a tiny
        # limit leaves them with an empty reply.
        self._call([{"role": "user", "content": "Reply with OK."}], 0.0)


def _reply_text(response: Any) -> str:
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, KeyError, TypeError) as exc:
        raise AppError(ErrorCode.LLM_BAD_RESPONSE, "The AI model sent an empty answer.") from exc
    if not isinstance(content, str) or not content.strip():
        raise AppError(ErrorCode.LLM_BAD_RESPONSE, "The AI model sent an empty answer.")
    return content


def short_problem(exc: Exception) -> str:
    """A short description of why a reply was rejected (sent back in the repair request)."""
    if isinstance(exc, ValidationError):
        parts = []
        for err in exc.errors()[:10]:
            location = ".".join(str(part) for part in err["loc"])
            parts.append(f"{location}: {err['msg']}")
        return "; ".join(parts)
    return str(exc)


_AUTH_NAMES = {"AuthenticationError", "PermissionDeniedError"}
_RATE_NAMES = {"RateLimitError"}
_TRANSIENT_NAMES = {
    "RateLimitError",
    "InternalServerError",
    "ServiceUnavailableError",
    "BadGatewayError",
    "APIConnectionError",
    "Timeout",
}


def _name(exc: BaseException) -> str:
    return type(exc).__name__


def _names(exc: BaseException) -> set[str]:
    return {cls.__name__ for cls in type(exc).__mro__}


def _is_transient(exc: BaseException) -> bool:
    return bool(_names(exc) & _TRANSIENT_NAMES)


def classify_error(exc: BaseException) -> ErrorCode:
    """Map a LiteLLM/OpenAI-SDK exception to a documented code. Matching is by class name
    (including base classes), so it does not depend on where LiteLLM defines each class."""
    names = _names(exc)
    if names & _AUTH_NAMES:
        return ErrorCode.LLM_AUTH_FAILED
    if names & _RATE_NAMES:
        return ErrorCode.LLM_RATE_LIMITED
    return ErrorCode.LLM_BAD_RESPONSE


_MESSAGES = {
    ErrorCode.LLM_AUTH_FAILED: (
        "The AI provider rejected the API key, or the key has no access to this model."
    ),
    ErrorCode.LLM_RATE_LIMITED: (
        "The AI provider's rate limit was reached. Wait a minute and retry, or pick another model."
    ),
    ErrorCode.LLM_BAD_RESPONSE: (
        "The AI provider did not answer correctly. Check the model name and try again."
    ),
}


def _app_error(code: ErrorCode, exc: BaseException, api_key: str) -> AppError:
    # Provider text can echo request details, even the key. It goes to the log only, with
    # this call's key masked (the key may not be the stored session's, e.g. the "Test"
    # button), and the API gets our own fixed message.
    logger.error("llm call failed: %s: %s", _name(exc), redact(str(exc), (api_key,)))
    return AppError(code, _message_for(code, exc), {"provider_error": _name(exc)})


# Clearer texts for common provider answers; the error code stays the documented one.
_NOT_FOUND_MESSAGE = (
    "This model is not available for your API key (it may be retired or not open to new "
    "users). Pick another model; Load models shows the ones your key can use."
)
_BUSY_MESSAGE = (
    "The AI model is busy or not answering right now (the provider reported high demand or "
    "a server error). Try again in a minute, or pick another model."
)
_BUSY_NAMES = {"ServiceUnavailableError", "InternalServerError", "BadGatewayError", "Timeout"}


def _message_for(code: ErrorCode, exc: BaseException) -> str:
    if code == ErrorCode.LLM_BAD_RESPONSE:
        names = _names(exc)
        if "NotFoundError" in names:
            return _NOT_FOUND_MESSAGE
        if names & _BUSY_NAMES:
            return _BUSY_MESSAGE
    return _MESSAGES[code]
