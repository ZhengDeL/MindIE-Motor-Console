"""Reference native renderer, operating only on the public capability context."""
from __future__ import annotations
import copy,json,os,shutil,subprocess,sys
from pathlib import Path
from typing import Any
from fastapi import HTTPException
import yaml
from ..operation_budget import remaining_time


def _json_write(path: Path, value: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary=path.with_suffix(path.suffix+".tmp")
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def render_kubernetes(context, payload: dict, namespace: str, revision: str) -> tuple[list[dict], Path]:
    """Use the pinned CLI and native configmap assets in isolated subprocesses."""
    source = context.upstream / "examples/deployer"
    if not (source / "deploy.py").is_file():
        raise HTTPException(503, "Pinned Motor deploy.py checkout is missing")
    from ..motor_adapter import MotorConfigError, verify_upstream_source
    try:
        context.verify_source(context.upstream)
    except MotorConfigError as exc:
        raise HTTPException(409, "Pinned upstream verification failed: " + str(exc)) from exc
    directory = context.directory_for(payload["service_id"]) / "revisions" / revision
    directory.mkdir(parents=True, exist_ok=False)
    directory.chmod(0o700)
    _json_write(directory / "original.json", {k: payload.get(k) for k in ("user_config", "env")})
    config = copy.deepcopy(payload.get("user_config"))
    if not isinstance(config, dict) or not isinstance(config.get("motor_deploy_config"), dict):
        raise HTTPException(422, "user_config.motor_deploy_config is required")
    if not isinstance(payload.get("env"), dict):
        raise HTTPException(422, "env must be a JSON object")
    config["motor_deploy_config"]["job_id"] = namespace
    config_dir = directory / "config"
    _json_write(config_dir / "user_config.json", config)
    _json_write(config_dir / "env.json", payload["env"])
    deployer = directory / "deployer"
    shutil.copytree(source, deployer, ignore=shutil.ignore_patterns("__pycache__", "output_yamls", "motor_workspace"))
    # The image checkout is read-only; only this isolated copy is made writable.
    for path in [deployer, *deployer.rglob("*")]:
        path.chmod(path.stat().st_mode | 0o200)
    process_env = os.environ.copy()
    process_env["PYTHONDONTWRITEBYTECODE"] = "1"
    commands = [sys.executable, str(deployer / "deploy.py"), "--dry-run", "--nostep", "--skip-check",
                "--config_dir", str(config_dir)]
    try:
        rendered = context.runner(commands, cwd=str(deployer), env=process_env, text=True, capture_output=True,
                               timeout=remaining_time(150), check=False, shell=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(503, "Native Motor rendering failed to start or timed out") from exc
    (directory / "render.log").write_text(rendered.stdout + rendered.stderr, encoding="utf-8")
    if rendered.returncode:
        raise HTTPException(422, "Native Motor dry-run failed; inspect the agent revision render.log")
    # Native dry-run intentionally skips shell-env rendering and ConfigMap creation.
    # Do the file-only preparation on the private copy using the original helpers.
    helper = (
        "import json,pathlib;from lib.utils import read_json,set_env_to_shell;"
        "from lib.generator.k8s_utils import get_deploy_mode_from_config;"
        "from lib.config_validator import validate_node_selectors;"
        "from lib.prepare_utils import configmap_assets;"
        "u=read_json(" + repr(str(config_dir / "user_config.json")) + ");"
        "validate_node_selectors(u);"
        "set_env_to_shell(u," + repr(str(config_dir / "env.json")) + ","
        "get_deploy_mode_from_config(u['motor_deploy_config']));"
        "d={n:p.read_text(encoding='utf-8') for p,n in configmap_assets(pathlib.Path.cwd())};"
        "d['user_config.json']=json.dumps(u,ensure_ascii=False);"
        "d['nodeport_conflict_coordinator.txt']='';d['nodeport_conflict_controller.txt']='';"
        "pathlib.Path('assets.json').write_text(json.dumps(d),encoding='utf-8')"
    )
    try:
        prepared = context.runner([sys.executable, "-c", helper], cwd=str(deployer), env=process_env, text=True,
                               capture_output=True, timeout=remaining_time(45), check=False, shell=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(503, "Native ConfigMap preparation failed to start or timed out") from exc
    if prepared.returncode:
        raise HTTPException(422, "Native Motor ConfigMap preparation failed")
    documents: list[dict] = []
    for path in sorted((deployer / "output_yamls").glob("*.y*ml")):
        documents.extend(doc for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")) if doc)
    assets = json.loads((deployer / "assets.json").read_text(encoding="utf-8"))
    documents.insert(0, {"apiVersion": "v1", "kind": "ConfigMap",
                         "metadata": {"name": "motor-config", "namespace": namespace}, "data": assets})
    if len(documents) == 1:
        raise HTTPException(422, "Native Motor renderer produced no workload YAML")
    return documents, directory


def render_kubernetes_scale(context, baseline: dict, target: dict, document: dict,
                            service_id: str, revision: str) -> tuple[dict, Path]:
    """Run only the native, file-only replica updater; never run the update CLI.

    Pinned deploy.py --dry-run does not cover --update_instance_num. Its CLI
    reads and mutates the cluster, so scaling uses its original pure helpers
    against a private copy of the already owned InferServiceSet instead.
    """
    from ..motor_adapter import MotorConfigError, verify_upstream_source
    try:
        context.verify_source(context.upstream)
    except MotorConfigError as exc:
        raise HTTPException(409, "Pinned upstream verification failed: " + str(exc)) from exc
    source = context.upstream / "examples/deployer"
    directory = context.directory_for(service_id) / "revisions" / revision
    directory.mkdir(parents=True, exist_ok=False)
    directory.chmod(0o700)
    _json_write(directory / "baseline.json", baseline)
    _json_write(directory / "target.json", target)
    manifest = directory / "infer-service.yaml"
    manifest.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")
    deployer = directory / "deployer"
    shutil.copytree(source, deployer, ignore=shutil.ignore_patterns("__pycache__", "output_yamls", "motor_workspace"))
    for path in [deployer, *deployer.rglob("*")]:
        path.chmod(path.stat().st_mode | 0o200)
    helper = (
        "import json,pathlib;"
        "from lib.config_validator import validate_only_instance_changed;"
        "from lib.generator.engine import validate_instance_nums;"
        "from lib.generator.infer_service import update_infer_service_replicas_only;"
        "b=json.loads(pathlib.Path(" + repr(str(directory / "baseline.json")) + ").read_text(encoding='utf-8'));"
        "u=json.loads(pathlib.Path(" + repr(str(directory / "target.json")) + ").read_text(encoding='utf-8'));"
        "validate_only_instance_changed(u,b);validate_instance_nums(u);"
        "update_infer_service_replicas_only(" + repr(str(manifest)) + ",u['motor_deploy_config'],u)"
    )
    process_env = os.environ.copy()
    process_env["PYTHONDONTWRITEBYTECODE"] = "1"
    process_env["KUBECONFIG"] = str(directory / "no-cluster-access")
    try:
        result = context.runner([sys.executable, "-c", helper], cwd=str(deployer), env=process_env,
                             text=True, capture_output=True, timeout=remaining_time(45), check=False, shell=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(503, "Native Motor replica rendering failed to start or timed out") from exc
    (directory / "render.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise HTTPException(422, "Native Motor replica rendering failed; inspect the agent revision render.log")
    documents = [d for d in yaml.safe_load_all(manifest.read_text(encoding="utf-8")) if d]
    if len(documents) != 1 or not isinstance(documents[0], dict):
        raise HTTPException(422, "Native replica updater must return the single owned InferServiceSet")
    return documents[0], directory
