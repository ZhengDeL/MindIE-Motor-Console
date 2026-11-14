import { api, post } from "./api";

export type AgentRolloutSpec = {
  name: string;
  address: string;
  runtime: "docker" | "kubernetes";
  agent_url: string;
  agent_port: number;
  nic_name: string;
  labels: string[];
  installation_name: string;
  tls_mode: "managed_tls" | "existing_https";
  https_proxy_confirmed: boolean;
  image_source: "registry" | "artifact";
  image: string;
  artifact_id: string;
  kubeconfig_path: string;
  cpus: string;
  memory: string;
  pids: number;
  native_concurrency: number;
};
export type AgentSSHConnection = {
  host: string;
  port: number;
  username: string;
  host_key_sha256: string;
  privilege: "root" | "sudo_n";
};
// Transient input only. Never include this type in a persisted plan or job.
export type AgentSSHCredentials = {
  method: "password" | "private_key";
  password: string;
  private_key: string;
  passphrase: string;
};
export type AgentHostKey = {
  host: string;
  port: number;
  key_type: string;
  fingerprint: string;
  verified: false;
};
export type AgentImageArtifact = {
  schema: number;
  id: string;
  filename: string;
  sha256: string;
  size_bytes: number;
  image_id: string;
  architecture: string;
  console_version: string;
  agent_protocol: number;
  motor_commit: string;
};
export type AgentRolloutCheck = {
  id: string;
  title: string;
  status: "passed" | "warning" | "failed";
  message: string;
};
export type AgentRolloutStep = {
  id: string;
  title: string;
  status?: "pending" | "running" | "completed" | "failed" | "skipped";
  started_at?: string;
  finished_at?: string;
  message?: string;
};
export type AgentRolloutPlan = {
  id: string;
  digest: string;
  expires_at: string;
  spec: AgentRolloutSpec;
  connection: AgentSSHConnection;
  checks: AgentRolloutCheck[];
  remote: Record<string, unknown>;
  steps: AgentRolloutStep[];
  can_execute: boolean;
  existing_installation: {
    container_id: string;
    name: string;
    runtime: string;
    state: string;
    image_id: string;
    project: string;
  }[];
};
export type AgentRolloutStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "interrupted"
  | "cancelled"
  | "installed_unconnected";
export type AgentRolloutJob = {
  id: string;
  plan_id: string;
  name: string;
  runtime: "docker" | "kubernetes";
  address: string;
  status: AgentRolloutStatus;
  stage: string;
  steps: AgentRolloutStep[];
  created_at: string;
  updated_at: string;
  error_code?: string;
  resources_may_exist: boolean;
  agent_url: string;
  node_id?: string;
  can_connect: boolean;
  can_cancel: boolean;
  can_recover: boolean;
  connection?: AgentSSHConnection;
  connection_verified: boolean;
  rollback?: Record<string, unknown> | null;
};

const base = "/agent-rollouts";
export const discoverAgentHostKey = (host: string, port: number) =>
  post<AgentHostKey>(`${base}/host-key`, { host, port });
export const listAgentArtifacts = (signal?: AbortSignal) =>
  api<{ items: AgentImageArtifact[]; errors: string[] }>(`${base}/artifacts`, {
    signal,
  });
export const createAgentRolloutPlan = (
  spec: AgentRolloutSpec,
  connection: AgentSSHConnection,
  credentials: AgentSSHCredentials,
) => post<AgentRolloutPlan>(`${base}/plans`, { spec, connection, credentials });
export const executeAgentRollout = (
  plan: AgentRolloutPlan,
  credentials: AgentSSHCredentials,
  confirmationName: string,
) =>
  post<{ job_id: string }>(
    `${base}/plans/${encodeURIComponent(plan.id)}/execute`,
    {
      plan_digest: plan.digest,
      credentials,
      confirmation_name: confirmationName,
    },
  );
export const listAgentRollouts = (signal?: AbortSignal) =>
  api<{ items: AgentRolloutJob[] }>(`${base}/jobs?limit=50`, { signal });
export const getAgentRollout = (id: string, signal?: AbortSignal) =>
  api<AgentRolloutJob>(`${base}/jobs/${encodeURIComponent(id)}`, { signal });
export const connectAgentRollout = (id: string) =>
  post<AgentRolloutJob>(`${base}/jobs/${encodeURIComponent(id)}/connect`, {});
export const cancelAgentRollout = (id: string) =>
  post<AgentRolloutJob>(`${base}/jobs/${encodeURIComponent(id)}/cancel`, {});
export const recoverAgentRollout = (
  id: string,
  credentials: AgentSSHCredentials,
) =>
  post<AgentRolloutJob>(`${base}/jobs/${encodeURIComponent(id)}/recover`, {
    credentials,
  });

export function agentRolloutStatus(job: AgentRolloutJob) {
  if (job.status === "completed")
    return job.connection_verified ? "已接入" : "安装完成，连接待核验";
  return {
    queued: "等待执行",
    running: "安装中",
    failed: "安装失败",
    interrupted: "任务中断",
    cancelled: "已取消",
    installed_unconnected: "已安装，待连接",
  }[job.status];
}

// A validation error may contain submitted inputs. Do not display arbitrary bodies.
export function agentRolloutError(error: unknown) {
  const status = (error as { status?: number })?.status;
  if (status === 401) return "登录已失效，请重新登录后查看任务记录。";
  if (status === 403) return "只有系统管理员可以推送和管理 Agent 安装任务。";
  if (status === 422) return "安装参数或 SSH 身份不符合要求，请检查后重试。";
  const codes: Record<string, string> = {
    ssh_host_key_mismatch:
      "SSH 主机指纹不匹配，未发送登录凭据。请通过独立渠道重新核对。",
    ssh_authentication_failed: "SSH 身份验证失败，请重新输入正确凭据。",
    ssh_connection_failed: "无法建立 SSH 连接，请检查目标地址、端口和网络。",
    ssh_host_key_discovery_failed:
      "无法读取 SSH 主机指纹，请检查目标地址与 SSH 服务。",
    ssh_private_key_invalid: "SSH 私钥或口令无效，请重新选择文件并输入口令。",
    ssh_stage_connection_lost:
      "SSH 连接中断，远端操作结果尚未确认，请从任务记录执行恢复核对。",
    existing_installation:
      "目标已有 Agent 安装，已拒绝覆盖，请使用手动接入或检查原安装。",
    plan_expired: "安装计划已过期，请重新执行预检。",
    host_installation_busy: "目标主机已有待核验的安装任务，请先检查任务记录。",
    rollout_queue_full: "当前安装队列已满，请等待已有任务完成后重试。",
    console_in_maintenance: "控制台正在维护，请稍后重试。",
  };
  return (
    codes[(error as Error)?.message] ??
    (status === 409
      ? "目标状态或安装计划已变化，请查看任务记录和预检结果，勿重复安装。"
      : "请求未完成，请检查预检结果或任务记录；安装结果未确认前请勿重复推送。")
  );
}
