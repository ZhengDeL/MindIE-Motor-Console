"""Version-pinned feature and real upstream configuration catalog."""
from __future__ import annotations

import copy
from html.parser import HTMLParser
import json
from pathlib import Path
from .capability_guides import capability_guide
from .compatibility import public_profile
from .settings import documentation_url

MOTOR_COMMIT = "37d615f232a36b8dba8630ffc3a01a20d289c65e"


class _FeatureTable(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr": self.row = []
        if tag in ("td", "th"): self.cell = ""
        if tag == "a" and self.cell is not None:
            self.link = dict(attrs).get("href", "")

    def handle_data(self, data):
        if self.cell is not None: self.cell += data

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            if self.row is not None: self.row.append(self.cell.strip())
            self.cell = None
        if tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def capabilities(upstream: Path) -> list[dict]:
    path = upstream / "docs/zh/user_guide/features/supported_features.md"
    if not path.is_file(): return []
    parser = _FeatureTable()
    parser.feed(path.read_text(encoding="utf-8"))
    result, category = [], "推理与调度"
    docs_url = documentation_url()
    motor_commit = public_profile()["commit"]
    for row in parser.rows:
        if len(row) < 7 or not any("✅" in cell for cell in row): continue
        if len(row) == 8: category = row[0]
        name, maturity, vllm, sglang, k8s, docker, _ = row[-7:]
        guide = capability_guide(name, motor_commit)
        supported = docker == "✅"
        result.append({
            "id": "feature_" + str(len(result) + 1), "name": name, "category": category,
            "supported": supported, "docker_supported": supported, "kubernetes_supported": k8s == "✅",
            "runtimes": (["docker"] if supported else []) + (["kubernetes"] if k8s == "✅" else []),
            "engines": (["vllm"] if vllm == "✅" else []) + (["sglang"] if sglang == "✅" else []),
            "description": (("POC · " if maturity == "🔵" else "") + guide["purpose"]) if guide else "按固定版本原生配置与实际部署条件使用",
            "config_hint": guide["config_path"] if guide else "原生 Motor JSON 配置",
            "guide": guide,
            "maturity": "poc" if maturity == "🔵" else "supported",
            "doc_path": docs_url,
            "validation": "native_declared",
        })
        if name == "EPD分离":
            result[-1]["execution_supported"] = {"docker": False, "kubernetes": True}
            result[-1]["adapter_note"] = "当前固定版本 Docker 启动入口未串通 Encode，请使用 Kubernetes；官方支持声明与入口实现存在差异"
    return result


def summarize(user: dict) -> dict:
    deploy = user.get("motor_deploy_config") or {}
    hybrid = "motor_engine_union_config" in user
    section = user.get("motor_engine_union_config" if hybrid else "motor_engine_prefill_config") or {}
    engine = section.get("engine_config") or {}
    legacy = section.get("model_config") or {}
    model = engine.get("served_model_name") or engine.get("served-model-name") or engine.get("model") or engine.get("model-path") or legacy.get("model_name") or "未配置模型"
    if isinstance(model, list): model = ", ".join(str(v) for v in model)
    return {"model": str(model), "scenario": "hybrid" if hybrid else "separate", "engine": section.get("engine_type", "vllm"), "image": deploy.get("image_name", ""),
            "replicas": {"prefill": int(deploy.get("p_instances_num", 0) or 0), "decode": int(deploy.get("d_instances_num", 0) or 0), "union": int(deploy.get("hybrid_instances_num", 0) or 0)}}


def templates(upstream: Path) -> list[dict]:
    root = upstream / "model_configs"
    if not root.is_dir(): return []
    items = []
    for path in sorted(root.rglob("user_config.json")):
        env_path = path.with_name("env.json")
        if not env_path.is_file(): continue
        try:
            user = json.loads(path.read_text(encoding="utf-8"))
            env = json.loads(env_path.read_text(encoding="utf-8"))
            if not isinstance(user, dict) or not isinstance(env, dict): continue
            summary = summarize(user)
            deploy = user.get("motor_deploy_config") or {}
            relative = path.parent.relative_to(root).as_posix()
            items.append({"id": relative, "name": relative.replace("/", " · "), "engine": summary["engine"], "scenario": summary["scenario"],
                          "hardware": deploy.get("hardware_type", ""), "user_config": copy.deepcopy(user), "env": env})
        except (ValueError, TypeError, OSError): continue
    return items
