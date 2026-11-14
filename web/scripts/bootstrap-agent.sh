#!/usr/bin/env bash
# Explicit local node bootstrap. Does not SSH, install NPU packages, or deploy Motor services.
set -euo pipefail
if [[ "${MOTOR_ENV:-}" = production ]]; then
  echo 'Development bootstrap is disabled in production. Use scripts/prepare_agent.py and the dedicated production Agent Compose file.' >&2
  exit 2
fi
image='motor-console:local'
name='motor-console-agent'
upstream='/opt/motor/upstream'
workspace='/opt/motor-console/workspace'
data_dir='/opt/motor-console/agent-data'
bind_ip='127.0.0.1'
port='19082'
token_file=''
kubeconfig=''
while (($#)); do
  case "$1" in
    --image) image="$2"; shift 2;;
    --name) name="$2"; shift 2;;
    --upstream-dir) upstream="$2"; shift 2;;
    --workspace-dir) workspace="$2"; shift 2;;
    --data-dir) data_dir="$2"; shift 2;;
    --bind-ip) bind_ip="$2"; shift 2;;
    --port) port="$2"; shift 2;;
    --token-file) token_file="$2"; shift 2;;
    --kubeconfig) kubeconfig="$2"; shift 2;;
    -h|--help)
      echo 'Run on the intended Linux Docker host: bootstrap-agent.sh --token-file /secure/motor-console/agent.token [--bind-ip NODE_IP] [--upstream-dir ABS_PATH] [--kubeconfig ABS_PATH]'; exit 0;;
    *) echo "Unknown option: $1" >&2; exit 2;;
  esac
done
command -v docker >/dev/null || { echo 'Docker CLI is required.' >&2; exit 2; }
command -v python3 >/dev/null || { echo 'Python 3.9+ is required.' >&2; exit 2; }
[[ "$name" = motor-console-* ]] || { echo 'Use an independent motor-console- container name.' >&2; exit 2; }
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHONPATH="$script_dir/.." python3 - "$workspace" "$data_dir" "$token_file" "$kubeconfig" <<'PY'
import sys
from scripts.product_paths import require_product_path
from scripts.agent_rollout_remote import platform_agents
for value in sys.argv[1:]:
    if value:
        require_product_path(value, "Development Agent path")
data = require_product_path(sys.argv[2], "Development Agent data")
if data.exists() and (not data.is_dir() or any(data.iterdir())):
    raise SystemExit("Use an empty Agent data directory; nothing was replaced")
if platform_agents():
    raise SystemExit("An execution Agent already exists; nothing was replaced")
PY
[[ -f "$token_file" ]] || { echo 'Supply an existing private token file.' >&2; exit 2; }
[[ "$upstream" = /* && "$workspace" = /* && "$data_dir" = /* ]] || { echo 'Use absolute host paths.' >&2; exit 2; }
[[ -f "$upstream/examples/deployer/docker_deploy.py" ]] || { echo 'Pinned Motor checkout is missing.' >&2; exit 2; }
if docker container inspect "$name" >/dev/null 2>&1; then
  echo 'Agent container name already exists; review it explicitly. Nothing was replaced.' >&2; exit 2
fi
docker image inspect "$image" >/dev/null
MOTOR_AGENT_TOKEN="$(cat -- "$token_file")"
[[ ${#MOTOR_AGENT_TOKEN} -ge 24 ]] || { echo 'Agent token must be at least 24 characters; use 32 random bytes.' >&2; exit 2; }
export MOTOR_AGENT_TOKEN
mkdir -p -- "$workspace" "$data_dir"
args=(docker run -d --name "$name" --restart unless-stopped --network host
  -e MOTOR_MODE=agent -e MOTOR_AGENT_TOKEN -e "MOTOR_AGENT_BIND_IP=$bind_ip" -e "MOTOR_AGENT_PORT=$port"
  -e MOTOR_HOST_ROOT=/host -e "MOTOR_UPSTREAM_DIR=$upstream" -e "MOTOR_HOST_WORKSPACE_DIR=$workspace"
  -e MOTOR_DATA_DIR=/var/lib/motor-console
  --mount type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock
  --mount type=bind,src=/,dst=/host,readonly
  --mount "type=bind,src=$upstream,dst=$upstream,readonly"
  --mount "type=bind,src=$workspace,dst=$workspace"
  --mount "type=bind,src=$data_dir,dst=/var/lib/motor-console")
if [[ -n "$kubeconfig" ]]; then
  [[ "$kubeconfig" = /* && -f "$kubeconfig" ]] || { echo 'kubeconfig must be an existing absolute path.' >&2; exit 2; }
  args+=(-e KUBECONFIG=/run/motor-console/kubeconfig --mount "type=bind,src=$kubeconfig,dst=/run/motor-console/kubeconfig,readonly")
fi
args+=("$image")
"${args[@]}"
echo "Agent started at $bind_ip:$port; register it explicitly in the control UI. Model services have not been deployed."
