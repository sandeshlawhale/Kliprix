"""The saved LLM setup (D-053): one row, the API key encrypted with `SecretBox`."""

from __future__ import annotations

import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.secret_box import SecretBox, SecretBoxError
from app.db.models import LlmCredentialRow, utc_now
from app.llm.providers import PROVIDERS_BY_ID
from app.llm.session import LlmSession

logger = logging.getLogger(__name__)

_ROW_ID = 1


def save(session: Session, box: SecretBox, llm: LlmSession) -> None:
    """Replace the saved setup with `llm`. The caller commits."""
    session.merge(
        LlmCredentialRow(
            id=_ROW_ID,
            provider=llm.provider,
            model=llm.model,
            base_url=llm.base_url,
            api_key_enc=box.encrypt(llm.api_key),
            saved_at=utc_now(),
        )
    )


def exists(session: Session) -> bool:
    return session.get(LlmCredentialRow, _ROW_ID) is not None


def load(session: Session, box: SecretBox) -> LlmSession | None:
    """The saved setup, decrypted; None if nothing is saved.

    A row that cannot be decrypted (key file lost, other Windows user) or names an unknown
    provider is removed so the app asks for the key again. The caller commits.
    """
    row = session.scalars(select(LlmCredentialRow).where(LlmCredentialRow.id == _ROW_ID)).first()
    if row is None:
        return None
    if row.provider not in PROVIDERS_BY_ID:
        logger.warning("saved LLM setup removed: unknown provider %s", row.provider)
        clear(session)
        return None
    try:
        api_key = box.decrypt(row.api_key_enc)
    except SecretBoxError:
        logger.warning("saved LLM key removed: it cannot be decrypted on this install")
        clear(session)
        return None
    return LlmSession(
        provider=PROVIDERS_BY_ID[row.provider].id,
        model=row.model,
        api_key=api_key,
        base_url=row.base_url,
    )


def clear(session: Session) -> None:
    """Remove the saved setup. The caller commits."""
    session.execute(delete(LlmCredentialRow))
