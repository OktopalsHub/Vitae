from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ResumeSkill(BaseModel):
    model_config = ConfigDict(extra="ignore")
    label: str = "Skills"
    value: str = ""


class ResumeExperience(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str = ""
    dates: str = ""
    bullets: list[str] = Field(default_factory=list)


class ResumeProject(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str = ""
    dates: str = ""
    bullets: list[str] = Field(default_factory=list)


class ResumeEducation(BaseModel):
    model_config = ConfigDict(extra="ignore")
    school: str = ""
    dates: str = ""
    details: str = ""


class TailoredResume(BaseModel):
    """Strict boundary for model-generated resume data."""
    model_config = ConfigDict(extra="ignore")
    summary: str
    skills: list[ResumeSkill] = Field(default_factory=list)
    experiences: list[ResumeExperience] = Field(default_factory=list)
    projects: list[ResumeProject] = Field(default_factory=list)
    education: list[ResumeEducation] = Field(default_factory=list)


class ApplicationAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")
    question: str
    answer: str


class ApplicationAnswers(BaseModel):
    model_config = ConfigDict(extra="ignore")
    answers: list[ApplicationAnswer] = Field(default_factory=list)
