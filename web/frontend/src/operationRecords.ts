import type { Json } from "./api";
import { statusName } from "./api";
import { knownText } from "./resourcePresentation";

type RecordIdentity = {
  id: string;
  actor: string;
  request_id: string;
  actor_display?: string | null;
  actor_username?: string | null;
  actor_display_name?: string | null;
  actor_kind?: string | null;
  actor_name_source?: string | null;
};
export type SystemLog = RecordIdentity & {
  timestamp: string;
  severity_text: string;
  body: string;
  attributes: Json;
  "service.name": string;
};
export type AuditRecord = RecordIdentity & {
  time: string;
  action: string;
  target: string;
  status: string;
  detail: string;
  action_label?: string | null;
  target_display?: string | null;
};
export type OperationRecord = SystemLog | AuditRecord;

export function auditStatusLabel(status: string): string {
  return status === "partial" ? "未完整" : statusName(status);
}

export function recordText(value: unknown): string | null {
  return knownText(value) ? value.trim() : null;
}
export function recordTime(value: unknown): string | null {
  const text = recordText(value);
  return text && Number.isFinite(Date.parse(text)) ? text : null;
}
function readableText(value: unknown): string | null {
  const text = recordText(value);
  if (
    !text ||
    /^(?:usr|svc|node|tpl|job|agt|plan|rollout|backup|session)_[a-z0-9_-]+$/i.test(
      text,
    ) ||
    /^(?:(?:sha256|docker):)?[0-9a-f]{32,}$/i.test(text) ||
    /^oidc:[^\s]+$/i.test(text)
  )
    return null;
  return text;
}
export function recordActor(record: OperationRecord): string | null {
  return (
    recordText(record.actor_display) ||
    recordText(record.actor_username) ||
    readableText(record.actor)
  );
}
const legacyActionLabels: Record<string, string> = {
  "auth.login": "账号登录",
  "auth.logout": "账号退出",
  "auth.bootstrap": "首次初始化",
  "auth.password_change": "修改个人密码",
};
export function auditAction(record: AuditRecord): string {
  return (
    recordText(record.action_label) ||
    legacyActionLabels[record.action] ||
    (/[\u3400-\u9fff]/.test(record.action) ? record.action : "操作记录")
  );
}
export function auditTarget(record: AuditRecord): string | null {
  return readableText(record.target_display) || readableText(record.target);
}
export function logContext(record: SystemLog): string | null {
  const method = recordText(record.attributes?.method);
  const path = recordText(record.attributes?.path);
  const status = recordHttpStatus(record.attributes?.status_code);
  return [method, path, status].filter(Boolean).join(" · ") || null;
}
export function recordHttpStatus(value: unknown): string | null {
  const text = typeof value === "number" ? String(value) : recordText(value);
  return text && /^[1-5]\d{2}$/.test(text) ? text : null;
}
