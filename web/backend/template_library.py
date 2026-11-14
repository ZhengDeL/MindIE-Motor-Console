"""Persist user-owned Motor templates without changing the native source.

Only operator/admin configuration views expose the original Motor JSON; those
documents may contain sensitive business configuration. Console credentials,
placements and approval digests are not accepted as top-level template fields.
Runtime=None permits either runtime to be selected, not every native feature.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Callable, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator, model_validator

from .compatibility import CompatibilityError, check_binding, public_profile
from .store import now, uid
from .json_documents import validate_json_document


TEMPLATE_FORMAT = "motor-console-template-v1"


class TemplateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: StrictStr = Field(min_length=1, max_length=100)
    description: StrictStr = Field(default="", max_length=1000)
    tags: list[StrictStr] = Field(default_factory=list, max_length=16)
    runtime: Literal["docker", "kubernetes"] | None = None
    motor_profile: StrictStr = Field(default="", max_length=96)
    user_config: dict[str, Any]
    env: dict[str, Any]
    extra_manifests: list[dict[str, Any]] = Field(default_factory=list, max_length=100)

    @field_validator("name")
    @classmethod
    def normalized_name(cls, value):
        value = value.strip()
        if not value or any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("模板名称不能为空或包含控制字符")
        return value

    @field_validator("tags")
    @classmethod
    def normalized_tags(cls, values):
        result = []
        for value in values:
            value = value.strip()
            if not 1 <= len(value) <= 40 or any(ord(character) < 32 or ord(character) == 127 for character in value):
                raise ValueError("模板标签必须为1..40字符且不能包含控制字符")
            if value not in result:
                result.append(value)
        return result

    @model_validator(mode="after")
    def valid_native_document(self):
        if not isinstance(self.user_config.get("motor_deploy_config"), dict):
            raise ValueError("user_config 必须包含 motor_deploy_config 对象")
        validate_json_document(self.model_dump())
        return self


class TemplateUpdate(TemplateInput):
    revision: StrictInt = Field(ge=1)


def _summary(config: dict) -> dict:
    """Derive display metadata without validating or normalizing native fields."""
    hybrid = "motor_engine_union_config" in config
    section = config.get("motor_engine_union_config" if hybrid else "motor_engine_prefill_config")
    section = section if isinstance(section, dict) else {}
    deploy = config["motor_deploy_config"]
    engine, hardware = section.get("engine_type", "vllm"), deploy.get("hardware_type", "")
    return {"engine": engine if isinstance(engine, str) else "", "scenario": "hybrid" if hybrid else "separate",
            "hardware": hardware if isinstance(hardware, str) else ""}


def _profile(identifier: str) -> str:
    try:
        desired = identifier or public_profile()["profile_id"]
        return check_binding(desired)["profile_id"]
    except CompatibilityError as error:
        raise HTTPException(409, "模板绑定的 Motor 适配配置与当前执行配置不兼容；请保留原模板，并按当前配置另存新模板。" + str(error)) from error


class TemplateLibrary:
    def __init__(self, store, official: list[dict], mutation_lock):
        self.store, self.mutation_lock = store, mutation_lock
        profile = public_profile()["profile_id"]
        self.official = {item["id"]: {**copy.deepcopy(item), "source": "official", "description": "", "tags": [],
            "runtime": None, "motor_profile": profile, "revision": 1, "created_at": None, "updated_at": None,
            "extra_manifests": []} for item in official}

    @staticmethod
    def view(value: dict, role: str | None):
        result = copy.deepcopy(value)
        if role == "viewer":
            result.update(user_config={}, env={}, extra_manifests=[], config_redacted=True)
        else:
            result["config_redacted"] = False
        return result

    def items(self, role):
        # GET deliberately does not bind saved profiles, write rows or call Agents.
        return [self.view(item, role) for item in [*self.official.values(), *self.store.list("templates")]]

    def get(self, identifier):
        item = self.official.get(identifier) or self.store.get("templates", identifier)
        if item is None:
            raise HTTPException(404, "模板不存在")
        return copy.deepcopy(item)

    def _custom(self, identifier):
        if identifier in self.official:
            raise HTTPException(409, "官方模板只读，请复制为不同名称的自定义模板")
        item = self.store.get("templates", identifier)
        if item is None:
            raise HTTPException(404, "模板不存在")
        return item

    def _name_available(self, name, excluding=None):
        if any(item["id"] != excluding and item["name"].strip().casefold() == name.casefold()
               for item in self.store.list("templates")):
            raise HTTPException(409, "自定义模板名称已存在，请使用不同名称")

    def create(self, data: TemplateInput):
        with self.mutation_lock:
            self._name_available(data.name)
            profile = _profile(data.motor_profile)
            timestamp = now()
            value = {**data.model_dump(), **_summary(data.user_config), "id": uid("tpl_"), "source": "custom",
                     "motor_profile": profile, "revision": 1, "created_at": timestamp, "updated_at": timestamp}
            self.store.mutate_audited("templates", value["id"], value, action="创建模板", detail="已保存自定义模板",
                                     metadata={"name": value["name"], "action": "create"})
            return copy.deepcopy(value)

    def update(self, identifier: str, data: TemplateUpdate):
        with self.mutation_lock:
            previous = self._custom(identifier)
            if previous["revision"] != data.revision:
                raise HTTPException(409, "模板已被其他操作更新，请重新加载后再保存")
            self._name_available(data.name, excluding=identifier)
            desired_profile = data.motor_profile or previous["motor_profile"]
            if desired_profile != previous["motor_profile"]:
                raise HTTPException(409, "现有模板的 Motor 适配配置绑定不能替换，请另存新模板")
            profile = _profile(desired_profile)
            value = {**data.model_dump(exclude={"revision"}), **_summary(data.user_config), "id": identifier,
                     "source": "custom", "motor_profile": profile, "revision": previous["revision"] + 1,
                     "created_at": previous["created_at"], "updated_at": now()}
            self.store.mutate_audited("templates", identifier, value, action="更新模板", detail="已保存自定义模板新修订",
                                     metadata={"name": value["name"], "action": "update"})
            return copy.deepcopy(value)

    def delete(self, identifier: str, revision: int):
        with self.mutation_lock:
            previous = self._custom(identifier)
            if previous["revision"] != revision:
                raise HTTPException(409, "模板已被其他操作更新，请重新加载后再删除")
            self.store.mutate_audited("templates", identifier, None, action="删除模板", detail="已删除自定义模板，未修改服务或部署资源",
                                     metadata={"name": previous["name"], "action": "delete"})
            return {"ok": True, "id": identifier}


def create_template_router(store, official, mutation_lock, role_for_request: Callable[[Request], str | None]):
    library = TemplateLibrary(store, official, mutation_lock)
    router = APIRouter(prefix="/api/templates", tags=["templates"])

    def writer(request):
        if role_for_request(request) not in {"admin", "operator", "demo"}:
            raise HTTPException(403, "当前账号无权管理自定义模板")

    @router.get("")
    def list_templates(request: Request):
        return {"items": library.items(role_for_request(request))}

    @router.post("", status_code=201)
    def create_template(data: TemplateInput, request: Request):
        writer(request)
        return library.view(library.create(data), role_for_request(request))

    @router.get("/{identifier:path}")
    def get_template(identifier: str, request: Request):
        return library.view(library.get(identifier), role_for_request(request))

    @router.put("/{identifier:path}")
    def update_template(identifier: str, data: TemplateUpdate, request: Request):
        writer(request)
        return library.view(library.update(identifier, data), role_for_request(request))

    @router.delete("/{identifier:path}")
    def delete_template(identifier: str, request: Request,
                        revision: str = Query(..., min_length=1, max_length=20, pattern=r"^[1-9][0-9]*$")):
        writer(request)
        return library.delete(identifier, int(revision))

    return router
