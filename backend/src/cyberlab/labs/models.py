"""Ready-made labs: the catalog format (``registry/labs/<id>.yaml``)."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cyberlab.manifest.models import SLUG, UniverseSpec

# A path inside /workspace: relative, no "..", no hidden traversal tricks.
_REL_PATH = re.compile(r"^(?!/)(?!.*(^|/)\.\.(/|$))[A-Za-z0-9._/-]{1,200}$")
PLACEHOLDER = re.compile(r"\{\{(flag):([a-z0-9-]+)\}\}")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SetupFile(_Model):
    """A file placed into a machine's /workspace when the lab starts.

    ``content`` may contain ``{{flag:<task>}}`` (that task's random flag). ``generator``
    names a built-in content generator instead (see ``labs.generators``)."""

    environment: str = Field(pattern=SLUG)
    path: str
    content: str | None = Field(default=None, max_length=200_000)
    generator: str | None = Field(default=None, pattern=SLUG)
    # For generators that hide a flag: which task's flag.
    flag_task: str | None = Field(default=None, alias="flagTask", pattern=SLUG)

    @field_validator("path")
    @classmethod
    def _safe_path(cls, path: str) -> str:
        if not _REL_PATH.match(path):
            raise ValueError(f"setup paths must be relative paths inside /workspace: {path!r}")
        return path

    @model_validator(mode="after")
    def _one_source(self) -> SetupFile:
        if (self.content is None) == (self.generator is None):
            raise ValueError("a setup file needs exactly one of 'content' or 'generator'")
        return self


class Task(_Model):
    id: str = Field(pattern=SLUG)
    title: str = Field(max_length=120)
    text: str = Field(max_length=4000)
    hints: list[str] = Field(default_factory=list, max_length=5)
    # "flag": a random CL{...} flag made for each run. Otherwise the name of a value a
    # generator produced (for example the attacker's IP address in a generated log).
    answer: str = Field(default="flag", pattern=SLUG)
    answer_format: str = Field(default="CL{...}", alias="answerFormat", max_length=60)


class Lab(_Model):
    id: str = Field(pattern=SLUG)
    title: str = Field(max_length=80)
    level: Literal["beginner", "intermediate", "advanced"]
    minutes: int = Field(ge=5, le=600)
    summary: str = Field(max_length=400)
    topic: Literal["linux", "networking", "web", "forensics", "blue-team", "crypto"]
    skills: list[str] = Field(default_factory=list, max_length=12)
    introduction: str = Field(default="", max_length=4000)
    spec: UniverseSpec
    setup: list[SetupFile] = Field(default_factory=list, max_length=50)
    tasks: list[Task] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _consistent(self) -> Lab:
        envs = {e.name for e in self.spec.environments}
        tasks = [t.id for t in self.tasks]
        if len(set(tasks)) != len(tasks):
            raise ValueError("task ids must be unique")
        paths = [(f.environment, f.path) for f in self.setup]
        if len(set(paths)) != len(paths):
            raise ValueError("setup files must not repeat")
        for f in self.setup:
            if f.environment not in envs:
                raise ValueError(f"setup file {f.path!r}: unknown machine {f.environment!r}")
            for _kind, task in PLACEHOLDER.findall(f.content or ""):
                if task not in tasks:
                    raise ValueError(f"setup file {f.path!r}: unknown task {task!r}")
            if f.flag_task is not None and f.flag_task not in tasks:
                raise ValueError(f"setup file {f.path!r}: unknown flagTask {f.flag_task!r}")
        return self

    def public(self) -> dict:
        """What learners may see: everything except where the answers are hidden."""
        return {
            "id": self.id,
            "title": self.title,
            "level": self.level,
            "minutes": self.minutes,
            "summary": self.summary,
            "topic": self.topic,
            "skills": self.skills,
            "introduction": self.introduction,
            "machines": [
                {
                    "name": e.name,
                    "baseImage": e.base_image,
                    "toolPacks": e.tool_packs,
                    "desktop": e.access.desktop,
                }
                for e in self.spec.environments
            ],
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "text": t.text,
                    "hints": t.hints,
                    "answerFormat": t.answer_format,
                }
                for t in self.tasks
            ],
        }
