"""Proposed protocol v1 ground truth: explicit annotations bound to exact sources."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fall_detection.prediction import ActivityLabel

TIMEBASE = "source_relative_seconds"


class Interval(BaseModel):
    """A finite, positive half-open source-relative interval."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def positive(self) -> "Interval":
        """Reject reversed and empty intervals."""
        if self.end_seconds <= self.start_seconds:
            raise ValueError("interval must have positive duration")
        return self


class Annotation(Interval):
    """An explicit activity annotation; folders cannot supply this label."""

    label: ActivityLabel


class GroundTruthSource(BaseModel):
    """Bind annotations and declared observation to one exact decoded source."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    source_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    timebase: Literal["source_relative_seconds"]
    observations: list[Interval]
    annotations: list[Annotation]

    @model_validator(mode="after")
    def ordered(self) -> "GroundTruthSource":
        """Disallow overlap and annotations outside declared observation."""
        for intervals in (self.observations, self.annotations):
            if any(
                a.end_seconds > b.start_seconds
                for a, b in zip(intervals, intervals[1:], strict=False)
            ):
                raise ValueError("intervals must be ordered and nonoverlapping")
        for annotation in self.annotations:
            if not any(
                o.start_seconds <= annotation.start_seconds
                and annotation.end_seconds <= o.end_seconds
                for o in self.observations
            ):
                raise ValueError("annotation outside observation interval")
        return self


class GroundTruth(BaseModel):
    """Explicit dataset/split identity, independent of prediction exports."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal[1]
    dataset_id: str = Field(min_length=1)
    split_id: str = Field(min_length=1)
    purpose: Literal["tuning", "final", "synthetic"]
    annotation_version: str = Field(min_length=1)
    sources: list[GroundTruthSource] = Field(min_length=1)

    @model_validator(mode="after")
    def unique(self) -> "GroundTruth":
        """Prevent repeated sources from multiplying evaluation exposure."""
        for key in ("source_id", "source_sha256"):
            if len({getattr(s, key) for s in self.sources}) != len(self.sources):
                raise ValueError(f"duplicate {key}")
        return self
