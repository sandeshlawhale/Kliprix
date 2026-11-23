"""The clip critic (D-050): one LLM call for the clips the rules could not judge.

Only clips with a soft defect (payoff not verified, ending on a question or mid-thought) are
sent, at most CRITIC_MAX_CLIPS. The critic returns structured issues and edge moves by id;
every move goes through the same repair and validation as the rules, so the critic can never
bypass the boundary checks or set free timestamps. A failed call changes nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from app.config import LLM_SELECT_TEMPERATURE
from app.core.errors import AppError
from app.llm.client import LlmClient
from app.llm.prompts import CriticClip, critic_system, critic_user
from app.llm.schemas import CritiqueAnswer, CritiqueClip
from app.pipeline.context.timeline import ClipContext, describe_context

logger = logging.getLogger(__name__)

EDGE_ACTIONS = ("expand_start_to", "trim_start_to", "extend_end_to", "trim_end_to")


@dataclass(frozen=True)
class Verdict:
    worth_sharing: bool
    valid: bool
    issues: list[str]
    # (new start or None, new end or None) from the id-based repairs.
    start: float | None
    end: float | None


def edges_from_repairs(
    clip: CritiqueClip, ctx: ClipContext, start: float, end: float
) -> tuple[float | None, float | None]:
    """The critic's edge moves turned into times through the context ids; unknown ids and
    moves in the wrong direction are ignored (logged)."""
    new_start: float | None = None
    new_end: float | None = None
    for item in clip.repairs:
        action = item.action.strip().lower()
        utterance = ctx.utterances.get(item.ref)
        event = ctx.event(item.ref)
        if action not in EDGE_ACTIONS or (utterance is None and event is None):
            logger.info("critic repair ignored: %s %s", item.action[:30], item.ref[:12])
            continue
        if utterance is not None:
            low, high = utterance.start, utterance.end
        elif event is not None:
            low, high = event.t, event.end
        else:
            continue
        if not ctx.zone_start <= low <= ctx.zone_end:
            continue
        if (action == "expand_start_to" and low < start) or (
            action == "trim_start_to" and start < low < end
        ):
            new_start = low
        elif (action == "extend_end_to" and high > end) or (
            action == "trim_end_to" and start < high < end
        ):
            new_end = high
    return new_start, new_end


def critique(
    client: LlmClient,
    title: str,
    guidance: str,
    clips: Sequence[tuple[int, float, float, str, list[str], ClipContext]],
) -> dict[int, Verdict]:
    """Verdicts by clip id for (id, start, end, moment shape, rule findings, context)."""
    if not clips:
        return {}
    items = [
        CriticClip(
            id=i, start=s, end=e, moment=shape, problems=problems, context=describe_context(ctx)
        )
        for i, s, e, shape, problems, ctx in clips
    ]
    try:
        answer = client.complete_json(
            critic_system(guidance),
            critic_user(title, items),
            CritiqueAnswer,
            LLM_SELECT_TEMPERATURE,
        )
    except AppError as exc:
        logger.warning("critic call failed: %s", exc.code)
        return {}
    if not isinstance(answer, CritiqueAnswer):
        return {}
    by_id = {i: (s, e, ctx) for i, s, e, _, _, ctx in clips}
    verdicts: dict[int, Verdict] = {}
    for clip in answer.clips:
        if clip.id not in by_id or clip.id in verdicts:
            continue
        start, end, ctx = by_id[clip.id]
        new_start, new_end = edges_from_repairs(clip, ctx, start, end)
        verdicts[clip.id] = Verdict(
            worth_sharing=clip.worth_sharing,
            valid=clip.valid,
            issues=[issue.strip()[:80] for issue in clip.issues if issue.strip()][:5],
            start=new_start,
            end=new_end,
        )
    logger.info("critic clips=%s answered=%s", len(clips), len(verdicts))
    return verdicts
