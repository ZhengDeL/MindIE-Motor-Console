"""Reference HTTP request validation; JSON remains opaque to the workflow port."""
from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from ..json_documents import validate_json_document
class ServiceInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    runtime: Literal["docker", "kubernetes"] = "docker"
    user_config: dict[str, Any]
    env: dict[str, Any]
    placements: list[dict[str, Any]] = Field(default_factory=list, max_length=256)
    extra_manifests: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    motor_profile: str = Field(default="", max_length=96)
    expected_revision: int | None = Field(default=None, ge=1, strict=True)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str):
        if any(ord(c) < 32 for c in value): raise ValueError("名称不能含控制字符")
        if not value.strip(): raise ValueError("名称不能为空")
        return value.strip()

    @model_validator(mode="after")
    def finite_configuration(self):
        validate_json_document({"user_config": self.user_config, "env": self.env,
                                "placements": self.placements, "extra_manifests": self.extra_manifests})
        return self

class InferenceInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=100000)
    max_tokens: int = Field(default=128, ge=1, le=32768)

class ImageInspectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    node_id: str = Field(min_length=1, max_length=100)
    image: str = Field(min_length=1, max_length=256)
    probe_runtime: bool = False

    @field_validator("image")
    @classmethod
    def literal_image(cls, value: str) -> str:
        from ..image_contract import validate_image_reference
        return validate_image_reference(value)

class DeploymentApproval(BaseModel):
    extra_manifests_approved_sha256: str = Field(default="", pattern=r"^([0-9a-f]{64})?$")
    reviewed_configuration_digest: str = Field(default="", pattern=r"^([0-9a-f]{64})?$")

class ScaleInput(BaseModel):
    counts: dict[str, Any] = Field(min_length=1, max_length=3)

    @field_validator("counts")
    @classmethod
    def strict_counts(cls, value: dict):
        allowed = {"p_instances_num", "d_instances_num", "hybrid_instances_num"}
        if set(value) - allowed or any(type(number) is not int or not 1 <= number <= 16 for number in value.values()):
            raise ValueError("仅允许 p/d/hybrid_instances_num，且实例数必须为 1..16 的整数")
        return value

class UndeployInput(BaseModel):
    confirmation_name: str = Field(min_length=1, max_length=100)
