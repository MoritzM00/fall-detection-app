THESIS_BASELINE_PROMPT = """You are given a short video clip showing one primary human activity.
Classify the action in the first part of the clip using exactly one label from:
walk, fall, fallen, sit_down, sitting, lie_down, lying, stand_up, standing,
other, kneel_down, kneeling, squat_down, squatting, crawl, jump.

Account for the fact that adjacent clips may overlap. Respond only in this format:
The best answer is: <class_label>"""

PRESET_ID = "thesis-baseline-v1"
