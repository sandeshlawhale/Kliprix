"""Shared FastAPI dependencies (config, database sessions, LLM session store)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppConfig


def get_config(request: Request) -> AppConfig:
    config: AppConfig = request.app.state.config
    return config


def get_db(request: Request) -> Iterator[Session]:
    """One database session per request, closed afterwards."""
    factory: sessionmaker[Session] = request.app.state.session_factory
    with factory() as session:
        yield session


ConfigDep = Annotated[AppConfig, Depends(get_config)]
DbDep = Annotated[Session, Depends(get_db)]
