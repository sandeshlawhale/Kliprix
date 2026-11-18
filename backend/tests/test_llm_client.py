"""LLM client: JSON parsing, repair retry, backoff, and error mapping (LLM mocked in tests)."""

import os
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.errors import AppError, ErrorCode
from app.llm.client import LlmClient, classify_error, extract_json, litellm_model
from app.llm.prompts import (
    NO_SPEECH,
    PromptCandidate,
    select_system,
    select_user,
    transliterate_user,
)
from app.llm.schemas import SelectionAnswer, TransliterationAnswer
from app.llm.session import LlmSession
from app.llm.transliterate import transliterate_words

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
import litellm.exceptions  # must come after the env var above

SECRET = "sk-test-SECRET-123456"  # noqa: S105 - fake key for tests
SESSION = LlmSession(provider="gemini", model="gemini-x", api_key=SECRET, base_url=None)


def reply(text: str) -> Any:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


class Script:
    """A fake completion function that plays back replies or raises exceptions in order."""

    def __init__(self, *steps: Any) -> None:
        self.steps: Iterator[Any] = iter(steps)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        step = next(self.steps)
        if isinstance(step, BaseException):
            raise step
        return reply(step)


def rate_limit() -> Exception:
    return litellm.exceptions.RateLimitError("slow down", llm_provider="gemini", model="gemini-x")


def auth_error() -> Exception:
    return litellm.exceptions.AuthenticationError(
        "bad key", llm_provider="gemini", model="gemini-x"
    )


def client(script: Script, sleeps: list[float] | None = None) -> LlmClient:
    waits = sleeps if sleeps is not None else []
    return LlmClient(SESSION, completion=script, sleep=waits.append)


GOOD = '{"clips": [{"candidate_id": 0, "start": 10, "end": 50, "title": "Big win"}]}'


def test_extract_json_handles_fences_and_noise() -> None:
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Here you go: {"a": [1, 2]} hope it helps') == {"a": [1, 2]}
    assert extract_json('["x", "y"]') == ["x", "y"]
    with pytest.raises(ValueError, match="not valid JSON"):
        extract_json("no json here")


def test_complete_json_passes_session_values() -> None:
    script = Script(GOOD)
    answer = client(script).complete_json("sys", "user", SelectionAnswer, 0.3)
    assert answer.clips[0].title == "Big win"
    call = script.calls[0]
    assert call["model"] == "gemini/gemini-x"
    assert call["api_key"] == SECRET
    assert call["temperature"] == 0.3
    assert call["timeout"] == 90.0
    assert call["drop_params"] is True
    assert "api_base" not in call


def test_bad_json_gets_one_repair_retry() -> None:
    script = Script("not json at all", GOOD)
    answer = client(script).complete_json("sys", "user", SelectionAnswer, 0.3)
    assert len(answer.clips) == 1
    repair_messages = script.calls[1]["messages"]
    assert repair_messages[-2] == {"role": "assistant", "content": "not json at all"}
    assert "not valid" in repair_messages[-1]["content"]


def test_bad_json_twice_is_llm_bad_response() -> None:
    script = Script('{"clips": "nope"}', "still bad")
    with pytest.raises(AppError) as info:
        client(script).complete_json("sys", "user", SelectionAnswer, 0.3)
    assert info.value.code == ErrorCode.LLM_BAD_RESPONSE
    assert len(script.calls) == 2


def test_rate_limit_backs_off_then_succeeds() -> None:
    sleeps: list[float] = []
    script = Script(rate_limit(), rate_limit(), GOOD)
    answer = client(script, sleeps).complete_json("sys", "user", SelectionAnswer, 0.3)
    assert answer.clips
    assert sleeps == [2.0, 6.0]


def test_rate_limit_after_all_retries_is_llm_rate_limited() -> None:
    sleeps: list[float] = []
    script = Script(rate_limit(), rate_limit(), rate_limit(), rate_limit())
    with pytest.raises(AppError) as info:
        client(script, sleeps).complete_json("sys", "user", SelectionAnswer, 0.3)
    assert info.value.code == ErrorCode.LLM_RATE_LIMITED
    assert sleeps == [2.0, 6.0, 15.0]
    assert info.value.status_code == 429


def test_server_error_after_retries_is_bad_response() -> None:
    error = litellm.exceptions.InternalServerError("boom", llm_provider="gemini", model="gemini-x")
    script = Script(error, error, error, error)
    with pytest.raises(AppError) as info:
        client(script).complete_json("sys", "user", SelectionAnswer, 0.3)
    assert info.value.code == ErrorCode.LLM_BAD_RESPONSE


def test_auth_error_is_not_retried_and_hides_provider_text() -> None:
    script = Script(auth_error())
    with pytest.raises(AppError) as info:
        client(script).ping()
    assert info.value.code == ErrorCode.LLM_AUTH_FAILED
    assert len(script.calls) == 1
    assert SECRET not in info.value.message
    assert "bad key" not in info.value.message


def test_classify_error_by_class() -> None:
    assert classify_error(auth_error()) == ErrorCode.LLM_AUTH_FAILED
    assert classify_error(rate_limit()) == ErrorCode.LLM_RATE_LIMITED
    assert classify_error(ValueError("x")) == ErrorCode.LLM_BAD_RESPONSE


def test_empty_reply_is_bad_response() -> None:
    script = Script("", "")
    with pytest.raises(AppError) as info:
        client(script).complete_json("sys", "user", SelectionAnswer, 0.3)
    assert info.value.code == ErrorCode.LLM_BAD_RESPONSE


def test_litellm_model_strings() -> None:
    assert litellm_model(SESSION) == "gemini/gemini-x"
    compat = LlmSession("openai_compatible", "local", "k", "http://127.0.0.1:1234/v1")
    assert litellm_model(compat) == "openai/local"
    router = LlmSession("openrouter", "meta/llama-3", "k", None)
    assert litellm_model(router) == "openrouter/meta/llama-3"
    for session in (SESSION, compat, router):
        _, provider, _, _ = litellm.get_llm_provider(
            litellm_model(session), api_base=session.base_url
        )
        assert provider == (
            "openai" if session.provider == "openai_compatible" else session.provider
        )


def test_base_url_is_passed_as_api_base() -> None:
    script = Script("OK")
    compat = LlmSession("openai_compatible", "local", "k", "http://127.0.0.1:1234/v1")
    LlmClient(compat, completion=script).ping()
    assert script.calls[0]["api_base"] == "http://127.0.0.1:1234/v1"
    assert "max_tokens" not in script.calls[0]


# ---------- prompts: every prompt validates sample outputs (docs/10) ----------


def test_select_prompt_contents_and_sample_output() -> None:
    system = select_system(5, extended=False)
    assert "up to 5 moments" in system
    assert "<transcript>" in system
    assert "needs_extended" in system
    assert "30 to 60 seconds." in system
    assert "up to 90" in select_system(5, extended=True)
    candidates = [
        PromptCandidate(0, 100.0, 160.0, 0.8, {"audio": 0.7}, [(101.0, "hello\x07 there")]),
        PromptCandidate(1, 300.0, 360.0, 0.5, {"motion": 0.4}, []),
    ]
    user = select_user("A video", None, 900.0, 5, candidates)
    assert "[01:41] hello  there" in user or "[01:41] hello there" in user
    assert "\x07" not in user
    assert NO_SPEECH in user
    sample = (
        '{"clips":[{"candidate_id":3,"start":288.4,"end":340.2,"needs_extended":false,'
        '"title":"He never saw it coming","youtube_title":"He Never Saw It Coming #shorts",'
        '"youtube_description":"Two sentences.","hashtags":["#gaming","#funny","#shorts"],'
        '"linkedin_post":"Short.","reason":"Why.","score":0.9,"extra_field":"ignored"}]}'
    )
    answer = SelectionAnswer.model_validate_json(sample)
    assert answer.clips[0].candidate_id == 3


def test_select_prompt_caps_long_lines() -> None:
    lines = [(float(i), "x" * 1000) for i in range(500)]
    user = select_user("t", "c", 900.0, 5, [PromptCandidate(0, 0.0, 60.0, 1.0, {}, lines)])
    assert len(user) < 70000
    assert "x" * 301 not in user


def test_transliteration_prompt_and_sample_output() -> None:
    words = ["क्या", "OK"]
    assert transliterate_user(words) == '["क्या", "OK"]'
    assert TransliterationAnswer.validate_json('["kya", "OK"]') == ["kya", "OK"]


def test_transliterate_words_retries_wrong_length() -> None:
    script = Script('["kya baat"]', '["kya", "baat"]')
    assert transliterate_words(client(script), ["a", "b"]) == ["kya", "baat"]
    assert "exactly 2" in script.calls[1]["messages"][1]["content"]


def test_transliterate_words_gives_up_after_reminder() -> None:
    script = Script('["one"]', '["one"]')
    assert transliterate_words(client(script), ["a", "b"]) is None


def test_transliterate_words_none_on_llm_error() -> None:
    script = Script(auth_error())
    assert transliterate_words(client(script), ["a"]) is None
