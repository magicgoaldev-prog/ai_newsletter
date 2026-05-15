"""Pydantic contract between writing, infographic generation, and rendering."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, HttpUrl

AspectRatio = Literal["16:9", "1:1", "4:3", "3:2", "2:3"]


class InfographicSpec(BaseModel):
    placement: Literal["hero", "section"]
    section_index: int | None = Field(
        default=None,
        description="Index into Newsletter.sections (0-based) when placement='section'. None for hero.",
    )
    prompt: str = Field(
        description="Editorial-illustration prompt passed verbatim to the image model.",
        min_length=20,
    )
    aspect_ratio: AspectRatio = "16:9"


class Section(BaseModel):
    heading: str = Field(min_length=2, max_length=120)
    body_markdown: str = Field(min_length=80)
    key_takeaway: str | None = Field(default=None, max_length=240)


class Newsletter(BaseModel):
    topic: str
    title: str = Field(min_length=4, max_length=160)
    subtitle: str = Field(min_length=4, max_length=300)
    hero_blurb: str = Field(min_length=40, max_length=800)
    sections: list[Section] = Field(min_length=3, max_length=6)
    infographic_specs: list[InfographicSpec] = Field(min_length=1, max_length=5)
    cta: str = Field(min_length=4, max_length=300)
    sources: list[HttpUrl] = Field(min_length=1)
