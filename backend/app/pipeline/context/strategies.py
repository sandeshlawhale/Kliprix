"""Game-aware contextual strategies (D-050).

The shared engine (events, boundary resolver, quality gate, repair) is the same for every
video; a strategy only supplies knowledge: which on-screen text, speech and sounds are which
events, which events are structural boundaries (a Valorant round start, a GTA "WASTED"),
what kinds of moments the game has and how much context each needs (its `MomentTemplate`),
and a short paragraph of guidance for the LLM. Unknown games and non-gaming videos use the
generic strategy.

No strategy needs its own model: everything comes from the transcript, the sound events,
the screen type and the on-screen text that the pipeline already reads.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.config import KILL_SPEECH, KILL_TEXT, SCENE_END_SPEECH, SCENE_END_TEXT

Source = Literal["text", "speech"]


@dataclass(frozen=True)
class MomentTemplate:
    """How much context one kind of moment needs around its core.

    The clip starts between `min_setup_s` and `max_setup_s` before the core (earlier when a
    `setup_kinds` event happens in that range), keeps `payoff_kinds` events that follow the
    core within `payoff_reach_s`, and ends `reaction_s` after the payoff. `speech_led`
    moments (talk, banter) keep whole utterances even when that adds a few seconds.
    """

    name: str
    shape: str
    min_setup_s: float
    max_setup_s: float
    payoff_reach_s: float
    reaction_s: float
    setup_kinds: frozenset[str] = frozenset()
    payoff_kinds: frozenset[str] = frozenset()
    # Events that mark the core when the LLM did not name it.
    core_kinds: frozenset[str] = frozenset()
    # The moment is incomplete without a payoff event (checked when the strategy can see one).
    needs_payoff: bool = False
    speech_led: bool = False
    # Action moments (D-051): quiet gaps inside the clip may be skipped (a montage), and a
    # setup said long before the core may be joined to it as its own part.
    montage: bool = False


@dataclass(frozen=True)
class EventRule:
    kind: str
    source: Source
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class GameStrategy:
    name: str
    label: str
    templates: dict[str, MomentTemplate]
    default_template: str
    rules: tuple[EventRule, ...] = ()
    # Sound group (D-041) -> event kind.
    sound_kinds: dict[str, str] = field(default_factory=dict)
    # Video title/channel words and on-screen text that show this game.
    title_pattern: re.Pattern[str] | None = None
    text_pattern: re.Pattern[str] | None = None
    # LLM categories and moment types -> template names.
    aliases: dict[str, str] = field(default_factory=dict)
    # A boundary at the event (a new round begins).
    round_start_kinds: frozenset[str] = frozenset()
    # A boundary `post_round_s` after the event, or at the next round start if sooner.
    round_end_kinds: frozenset[str] = frozenset()
    post_round_s: float = 8.0
    # A boundary `terminal_tail_s` after the event (death screen, "WASTED").
    terminal_kinds: frozenset[str] = frozenset()
    terminal_tail_s: float = 5.0
    # Menu/death-screen runs and generic round words end a scene (the generic rule, D-046).
    generic_scene_ends: bool = False
    # Clips may cover two rounds when the LLM says the story needs it.
    allow_multi_round: bool = False
    # Speech cues of an ongoing activity -> activity name (event kind "activity:<name>").
    activity_rules: tuple[tuple[str, re.Pattern[str]], ...] = ()
    guidance: str = ""

    def template(self, name: str | None) -> MomentTemplate:
        key = (name or "").strip().lower().replace(" ", "_").replace("-", "_")
        key = self.aliases.get(key, key)
        return self.templates.get(key) or self.templates[self.default_template]

    @property
    def detects_rounds(self) -> bool:
        return bool(self.round_start_kinds or self.round_end_kinds)


def _re(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


def _t(
    name: str,
    shape: str,
    setup: tuple[float, float],
    reach: float,
    reaction: float,
    *,
    setup_kinds: Iterable[str] = (),
    payoff: Iterable[str] = (),
    core: Iterable[str] = (),
    needs_payoff: bool = False,
    speech_led: bool = False,
    montage: bool = False,
) -> MomentTemplate:
    return MomentTemplate(
        name=name,
        shape=shape,
        min_setup_s=setup[0],
        max_setup_s=setup[1],
        payoff_reach_s=reach,
        reaction_s=reaction,
        setup_kinds=frozenset(setup_kinds),
        payoff_kinds=frozenset(payoff),
        core_kinds=frozenset(core),
        needs_payoff=needs_payoff,
        speech_led=speech_led,
        montage=montage,
    )


_COMMON_SOUNDS = {
    "gunfire": "gunfire",
    "explosion": "explosion",
    "shouting": "shout",
    "laughter": "laughter",
    "cheering": "cheer",
    "crash": "crash",
    "vehicle": "vehicle",
}
_REACTIONS = ("laughter", "shout", "cheer")

# ---------- generic ----------

GENERIC_TEMPLATES = {
    t.name: t
    for t in (
        _t("moment", "setup -> moment -> reaction", (3, 20), 4, 2, payoff=_REACTIONS),
        _t(
            "funny",
            "setup -> punchline -> reaction",
            (4, 25),
            6,
            3,
            payoff=("laughter",),
            speech_led=True,
        ),
        _t("reaction", "cause -> reaction", (4, 15), 6, 4, payoff=_REACTIONS),
        _t(
            "conversation",
            "question or setup -> exchange -> conclusion",
            (3, 40),
            4,
            1.5,
            speech_led=True,
        ),
        _t("story", "setup -> story -> point", (5, 45), 4, 1.5, speech_led=True),
        _t("explanation", "question -> explanation -> conclusion", (3, 40), 2, 1, speech_led=True),
        _t("emotional", "context -> moment -> response", (5, 30), 4, 2, speech_led=True),
        _t("demonstration", "intro -> demonstration -> result", (4, 30), 6, 2, speech_led=True),
        _t(
            "unexpected",
            "situation -> unexpected event -> reaction",
            (5, 15),
            8,
            3,
            payoff=(*_REACTIONS, "explosion", "crash"),
            core=("explosion", "crash"),
        ),
        _t(
            "play",
            "situation -> play -> outcome -> reaction",
            (4, 15),
            8,
            3,
            payoff=("kill", *_REACTIONS),
            core=("kill",),
            montage=True,
        ),
        _t("discovery", "approach -> discovery -> reaction", (6, 20), 6, 4, payoff=_REACTIONS),
    )
}
GENERIC_ALIASES = {
    "informative": "explanation",
    "opinion": "conversation",
    "conflict": "conversation",
    "surprising": "unexpected",
    "chaos": "unexpected",
    "talk": "conversation",
    "tip": "explanation",
    "insight": "explanation",
    "hot_take": "conversation",
}

GENERIC = GameStrategy(
    name="generic",
    label="Generic",
    templates=GENERIC_TEMPLATES,
    default_template="moment",
    rules=(
        EventRule("scene_end", "text", _re(SCENE_END_TEXT)),
        EventRule("scene_end", "speech", _re(SCENE_END_SPEECH)),
        EventRule("kill", "text", _re(KILL_TEXT)),
        EventRule("kill", "speech", _re(KILL_SPEECH)),
    ),
    sound_kinds=_COMMON_SOUNDS,
    aliases=GENERIC_ALIASES,
    generic_scene_ends=True,
    guidance=(
        "Keep whole sentences and whole exchanges: the setup (the question, the situation), "
        "the moment, and its payoff or reaction."
    ),
)

# ---------- Valorant ----------

_VAL_ROUND_START = (
    r"buy ?phase|press ?to ?buy|\bround ?\d+\b|match ?point|over ?time|last ?round|"
    r"switching ?sides"
)
_VAL_ROUND_END = (
    r"round ?(won|lost|over)|\bvictory\b|\bdefeat\b|flawless|thrifty|team ?ace|"
    r"spike ?(detonated|defused)|won ?the ?round|lost ?the ?round"
)
VALORANT = GameStrategy(
    name="valorant",
    label="Valorant",
    templates={
        t.name: t
        for t in (
            _t(
                "kill",
                "situation -> fight -> kill -> reaction",
                (4, 12),
                6,
                3,
                payoff=("kill", "multi_kill", "round_end"),
                core=("kill", "multi_kill"),
                montage=True,
            ),
            _t(
                "multi_kill",
                "situation -> kills -> last kill -> reaction",
                (4, 15),
                8,
                3,
                setup_kinds=("clutch_call", "spike_plant"),
                payoff=("kill", "multi_kill", "round_end"),
                core=("multi_kill", "kill"),
                needs_payoff=True,
                montage=True,
            ),
            _t(
                "clutch",
                "situation (player count, spike) -> clutch -> outcome -> round result -> reaction",
                (8, 35),
                15,
                3,
                setup_kinds=("clutch_call", "spike_plant"),
                payoff=("kill", "multi_kill", "round_end", "defuse"),
                core=("clutch_call", "multi_kill", "kill"),
                needs_payoff=True,
                montage=True,
            ),
            _t(
                "spike",
                "spike situation -> plant/defuse -> outcome",
                (6, 20),
                12,
                3,
                setup_kinds=("spike_plant",),
                payoff=("defuse", "round_end", "explosion"),
                core=("spike_plant", "defuse"),
                needs_payoff=True,
                montage=True,
            ),
            _t(
                "round_outcome",
                "decisive moment -> round result -> reaction",
                (6, 20),
                10,
                4,
                payoff=("round_end",),
                core=("round_end",),
                needs_payoff=True,
                montage=True,
            ),
            _t(
                "funny",
                "setup -> punchline -> reaction",
                (4, 25),
                6,
                3,
                payoff=("laughter",),
                speech_led=True,
            ),
            _t("reaction", "cause -> reaction", (4, 15), 6, 4, payoff=_REACTIONS),
            _t("conversation", "setup -> exchange -> conclusion", (3, 40), 4, 1.5, speech_led=True),
        )
    },
    default_template="kill",
    rules=(
        EventRule("round_start", "text", _re(_VAL_ROUND_START)),
        EventRule("round_start", "speech", _re(r"\b(next round|new round)\b")),
        EventRule("round_end", "text", _re(_VAL_ROUND_END)),
        EventRule("spike_plant", "text", _re(r"spike ?planted|planting")),
        EventRule("spike_plant", "speech", _re(r"\b(planted|planting|plant (it|the spike))\b")),
        EventRule("defuse", "text", _re(r"defus(e|ing|ed)")),
        EventRule("defuse", "speech", _re(r"\bdefus(e|ing|ed)\b")),
        EventRule("multi_kill", "text", _re(r"double|triple|quadra|\bace\b|\b[2-5] ?kills\b")),
        EventRule(
            "multi_kill",
            "speech",
            _re(r"\b([2-5]k|ace|all (three|four|five)|double|triple|(two|three|four) of them)\b"),
        ),
        # "KILLED BY ..." is the player's own death, not a kill.
        EventRule("kill", "text", _re(r"\bkill(?!ed ?by)|" + KILL_TEXT.removeprefix("kill|"))),
        EventRule("kill", "speech", _re(KILL_SPEECH)),
        EventRule(
            "clutch_call",
            "speech",
            _re(
                r"\b(1 ?v ?[1-5]|one v (one|two|three|four|five)|clutch|it'?s on me|"
                r"last (one )?alive|i'?m alone)\b"
            ),
        ),
        EventRule("clutch_call", "text", _re(r"\b1 ?v ?[1-5]\b|clutch")),
        EventRule("death", "text", _re(r"killed ?by|you died")),
    ),
    sound_kinds={
        "gunfire": "gunfire",
        "explosion": "explosion",
        "shouting": "shout",
        "laughter": "laughter",
        "cheering": "cheer",
    },
    title_pattern=_re(
        r"\bvalorant\b|\bvalo\b|\bradiant\b|\bimmortal\b|\bjett\b|\breyna\b|\bsova\b|"
        r"\bphoenix\b|\bomen\b|\bchamber\b|\bclove\b|\bvct\b"
    ),
    text_pattern=_re(r"buy ?phase|press ?to ?buy|spike|match ?point|round ?\d+"),
    aliases={
        "play": "kill",
        "ace": "multi_kill",
        "multikill": "multi_kill",
        "multi": "multi_kill",
        "surprising": "kill",
        "plant": "spike",
        "defuse": "spike",
        "win": "round_outcome",
        "loss": "round_outcome",
        "informative": "conversation",
        "opinion": "conversation",
        "emotional": "reaction",
        "conflict": "reaction",
    },
    round_start_kinds=frozenset({"round_start"}),
    round_end_kinds=frozenset({"round_end"}),
    post_round_s=8.0,
    terminal_kinds=frozenset({"death"}),
    terminal_tail_s=6.0,
    allow_multi_round=True,
    guidance=(
        "Valorant is played in rounds. A clip stays inside one round: it never runs into the "
        "next BUY PHASE or round banner unless the story truly needs two rounds (then set "
        '"multi_round" true and say why). For a clutch, keep the situation (how many are '
        "left, the spike), the clutch, the last kill and the round result; not the whole "
        "round and not the buy phase. For kills, keep a few seconds of the fight before them "
        "and the reaction after."
    ),
)

# ---------- GTA ----------

GTA = GameStrategy(
    name="gta",
    label="GTA",
    templates={
        t.name: t
        for t in (
            _t(
                "funny",
                "setup -> interaction -> punchline -> reaction",
                (5, 25),
                8,
                3,
                payoff=("laughter",),
                speech_led=True,
            ),
            _t(
                "chaos",
                "situation -> escalation -> unexpected event -> outcome -> reaction",
                (6, 20),
                10,
                4,
                setup_kinds=("chase", "gunfire", "crash"),
                payoff=("explosion", "crash", "wasted", "busted", "laughter", "shout"),
                core=("explosion", "crash", "gunfire"),
                needs_payoff=True,
                montage=True,
            ),
            _t(
                "mission",
                "mission context -> important event -> result",
                (8, 30),
                12,
                3,
                payoff=("mission_end", "wasted", "busted"),
                core=("mission_end",),
                needs_payoff=True,
                montage=True,
            ),
            _t(
                "failure",
                "attempt -> failure -> reaction",
                (4, 15),
                8,
                4,
                setup_kinds=("chase",),
                payoff=("wasted", "busted", "fail_cue", "crash"),
                core=("wasted", "busted", "crash"),
                needs_payoff=True,
                montage=True,
            ),
            _t(
                "discovery",
                "approach -> discovery -> reaction",
                (6, 20),
                6,
                4,
                payoff=_REACTIONS,
                core=("discovery_cue",),
            ),
            _t(
                "conversation",
                "conversational setup -> exchange -> punchline or reaction",
                (5, 40),
                6,
                2,
                payoff=("laughter",),
                speech_led=True,
            ),
            _t(
                "story",
                "relevant story setup -> story event -> reaction",
                (8, 40),
                6,
                2,
                speech_led=True,
            ),
            _t(
                "chase",
                "start of the chase -> escalation -> outcome",
                (6, 25),
                12,
                3,
                setup_kinds=("chase",),
                payoff=("crash", "wasted", "busted", "explosion"),
                core=("chase",),
                needs_payoff=True,
                montage=True,
            ),
        )
    },
    default_template="funny",
    rules=(
        EventRule("wasted", "text", _re(r"\bwasted\b")),
        EventRule("busted", "text", _re(r"\bbusted\b")),
        EventRule("mission_end", "text", _re(r"mission ?(passed|failed|complete)")),
        EventRule(
            "chase", "speech", _re(r"\b(cops|police|chase|they'?re on us|go go go|drive|run)\b")
        ),
        EventRule("chase", "text", _re(r"\bwanted\b|lose the cops")),
        EventRule("fail_cue", "speech", _re(r"\b(oh no|no+|i'?m dead|we'?re dead|what the)\b")),
        EventRule(
            "discovery_cue",
            "speech",
            _re(r"\b(look at (this|that)|what is (this|that)|no way|wait what|found)\b"),
        ),
    ),
    sound_kinds=_COMMON_SOUNDS | {"vehicle": "chase"},
    title_pattern=_re(
        r"\bgta\b|grand theft auto|\bnopixel\b|los santos|vice city|\bgta ?(v|5|6|rp)\b"
    ),
    text_pattern=_re(r"\bwasted\b|\bbusted\b|mission ?passed|mission ?failed"),
    aliases={
        "surprising": "chaos",
        "conflict": "chaos",
        "play": "chaos",
        "fail": "failure",
        "reaction": "funny",
        "informative": "conversation",
        "opinion": "conversation",
        "emotional": "story",
        "interaction": "funny",
        "unexpected": "chaos",
    },
    terminal_kinds=frozenset({"wasted", "busted", "mission_end"}),
    terminal_tail_s=5.0,
    guidance=(
        "GTA moments are stories: keep what leads to the moment (the setup of an "
        "interaction, how a chase or the chaos started, the attempt before a failure) and "
        "the payoff and reaction. A clip ends a few seconds after WASTED, BUSTED or MISSION "
        "PASSED/FAILED. Do not cut a conversation in the middle."
    ),
)

# ---------- Minecraft ----------

MINECRAFT = GameStrategy(
    name="minecraft",
    label="Minecraft",
    templates={
        t.name: t
        for t in (
            _t(
                "discovery",
                "exploration -> approach -> discovery -> reaction",
                (8, 30),
                6,
                4,
                setup_kinds=("activity:mining", "activity:exploring"),
                payoff=_REACTIONS,
                core=("discovery",),
            ),
            _t(
                "death",
                "situation -> mistake or encounter -> death -> reaction",
                (5, 20),
                8,
                4,
                setup_kinds=("mob", "explosion", "activity:*"),
                payoff=("death", "death_cue"),
                core=("death", "explosion"),
                needs_payoff=True,
                montage=True,
            ),
            _t(
                "boss",
                "encounter -> fight -> important moment -> outcome",
                (8, 30),
                15,
                4,
                setup_kinds=("boss",),
                payoff=("achievement", "death", "cheer", "shout"),
                core=("boss",),
                needs_payoff=True,
                montage=True,
            ),
            _t(
                "building",
                "progress -> completion -> reaction",
                (8, 30),
                8,
                5,
                setup_kinds=("activity:building",),
                payoff=("build_done", "laughter", "cheer"),
                core=("build_done",),
            ),
            _t(
                "unexpected",
                "activity -> unexpected event -> outcome -> reaction",
                (5, 20),
                8,
                4,
                setup_kinds=("activity:*",),
                payoff=("explosion", "death", "shout"),
                core=("explosion", "mob"),
            ),
            _t(
                "achievement",
                "challenge -> achievement -> reaction",
                (6, 20),
                8,
                4,
                payoff=("achievement",),
                core=("achievement",),
            ),
            _t(
                "funny",
                "setup -> punchline -> reaction",
                (4, 25),
                6,
                3,
                payoff=("laughter",),
                speech_led=True,
            ),
            _t("conversation", "setup -> exchange -> conclusion", (3, 40), 4, 1.5, speech_led=True),
        )
    },
    default_template="unexpected",
    rules=(
        EventRule(
            "death",
            "text",
            _re(
                r"you died|was slain|blew up|was blown up|fell from|drowned|burned to death|"
                r"tried to swim in lava|hit the ground|respawn"
            ),
        ),
        EventRule("death_cue", "speech", _re(r"\b(i died|i'?m dead|no+ ?way i died)\b")),
        EventRule(
            "achievement", "text", _re(r"advancement ?made|challenge ?complete|goal ?reached")
        ),
        EventRule("boss", "text", _re(r"ender ?dragon|wither|warden|elder ?guardian")),
        EventRule("boss", "speech", _re(r"\b(ender dragon|wither|warden|elder guardian|boss)\b")),
        EventRule(
            "discovery",
            "speech",
            _re(
                r"\b(diamonds?|ancient debris|netherite|found (a|an|the|it)|stronghold|"
                r"end portal|village|spawner|treasure|ancient city)\b"
            ),
        ),
        EventRule("discovery", "text", _re(r"diamond")),
        EventRule(
            "build_done",
            "speech",
            _re(
                r"\b((it'?s|its|we'?re) (done|finished|complete)|finally (done|finished)|"
                r"look at (this|the|my|our) (build|house|base|castle|farm)|finished building)\b"
            ),
        ),
        EventRule(
            "mob",
            "speech",
            _re(r"\b(zombie|skeleton|spider|enderman|creeper|piglin|phantom|warden)s?\b"),
        ),
    ),
    sound_kinds={
        "explosion": "explosion",
        "shouting": "shout",
        "laughter": "laughter",
        "cheering": "cheer",
    },
    title_pattern=_re(r"\bminecraft\b|\bmc\b|\bsmp\b|\bhardcore\b|\bcreeper\b|\bender ?dragon\b"),
    text_pattern=_re(r"advancement ?made|respawn|title ?screen|you died"),
    aliases={
        "surprising": "unexpected",
        "play": "unexpected",
        "fail": "death",
        "conflict": "unexpected",
        "reaction": "unexpected",
        "build": "building",
        "informative": "conversation",
        "opinion": "conversation",
        "emotional": "conversation",
        "chaos": "unexpected",
    },
    terminal_kinds=frozenset({"death"}),
    terminal_tail_s=5.0,
    activity_rules=(
        ("mining", _re(r"\b(mine|mining|strip ?mine|branch ?mine|dig(ging)?)\b")),
        ("exploring", _re(r"\b(explor(e|ing)|cave|looking for|search(ing)?|let'?s go find)\b")),
        ("building", _re(r"\b(build(ing)?|placing|blocks)\b")),
        ("farming", _re(r"\b(farm(ing)?|crops|breed(ing)?)\b")),
        ("crafting", _re(r"\b(craft(ing)?|smelt(ing)?|enchant(ing)?)\b")),
        ("fighting", _re(r"\b(fight(ing)?|attack(ing)?)\b")),
    ),
    guidance=(
        "Minecraft moments sit inside an activity (exploring, mining, building, a fight). "
        "Keep enough of the activity to show what the player was doing (the exploring before "
        "a discovery, the mistake or mob before a death, the progress before a finished "
        "build) and the reaction, but not the whole activity. A clip ends a few seconds "
        "after the death screen."
    ),
)

STRATEGIES: dict[str, GameStrategy] = {s.name: s for s in (GENERIC, VALORANT, GTA, MINECRAFT)}


def detect_strategy(
    title: str, channel: str | None = None, texts: Sequence[str] = ()
) -> GameStrategy:
    """The game strategy for a video, from its title and channel (2 points per match) and the
    on-screen text found so far (1 point per text, at most 3); generic below 2 points."""
    best, best_points = GENERIC, 0
    head = f"{title} {channel or ''}"
    for strategy in (VALORANT, GTA, MINECRAFT):
        points = 0
        if strategy.title_pattern is not None and strategy.title_pattern.search(head):
            points += 2
        if strategy.text_pattern is not None:
            points += min(3, sum(1 for text in texts if strategy.text_pattern.search(text)))
        if points > best_points:
            best, best_points = strategy, points
    return best if best_points >= 2 else GENERIC
