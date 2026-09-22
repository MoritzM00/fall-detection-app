from typing import Literal

type ActivityLabel = Literal[
    "walk",
    "fall",
    "fallen",
    "sit_down",
    "sitting",
    "lie_down",
    "lying",
    "stand_up",
    "standing",
    "other",
    "kneel_down",
    "kneeling",
    "squat_down",
    "squatting",
    "crawl",
    "jump",
]

ACTIVITY_LABELS: tuple[ActivityLabel, ...] = (
    "walk",
    "fall",
    "fallen",
    "sit_down",
    "sitting",
    "lie_down",
    "lying",
    "stand_up",
    "standing",
    "other",
    "kneel_down",
    "kneeling",
    "squat_down",
    "squatting",
    "crawl",
    "jump",
)
