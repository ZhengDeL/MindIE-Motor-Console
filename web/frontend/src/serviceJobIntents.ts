import { createOperationKey } from "./api";

const STORAGE_KEY = "qinghuan-platform-service-job-intents-v1";
const MAX_PENDING = 64;
const SIMPLE_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const SERVICE_ACTION =
  /^\/services\/[A-Za-z0-9_.:-]{1,128}\/(deploy|rebuild|restart|stop|scale|undeploy)$/;
const SHA256 = /^(?:[0-9a-f]{64})?$/;
const SCALE_KEYS = new Set([
  "p_instances_num",
  "d_instances_num",
  "hybrid_instances_num",
]);
type Owner = { userId: string; mode: string };
type Entry = {
  owner: string;
  fingerprint: string;
  operationKey: string;
  jobId?: string;
};
export type PendingServiceIntent = Entry & { created: boolean };
const record = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === "object" && !Array.isArray(value);

/** No credentials, configuration, environment, confirmation text, or arbitrary body enters this index. */
export function serviceIntentFingerprint(path: string, body?: unknown): string {
  const matched = SERVICE_ACTION.exec(path);
  if (!matched) throw Error("服务操作路径无效，未提交任务");
  if (body !== undefined && body !== null && !record(body))
    throw Error("服务操作参数无效");
  const value = (body ?? {}) as Record<string, unknown>;
  const approval =
    ["deploy", "rebuild"].includes(matched[1]) &&
    body !== undefined &&
    body !== null;
  const hashes = approval
    ? ["extra_manifests_approved_sha256", "reviewed_configuration_digest"].map(
        (field) => {
          const hash = value[field] ?? "";
          if (typeof hash !== "string" || !SHA256.test(hash))
            throw Error("部署审查摘要无效");
          return hash;
        },
      )
    : null;
  let counts: [string, number][] | null = null;
  if (matched[1] === "scale") {
    if (
      !record(value.counts) ||
      !Object.keys(value.counts).length ||
      Object.keys(value.counts).length > 3 ||
      Object.entries(value.counts).some(
        ([key, amount]) =>
          !SCALE_KEYS.has(key) ||
          !Number.isInteger(amount) ||
          Number(amount) < 1 ||
          Number(amount) > 16,
      )
    )
      throw Error("目标实例数无效");
    counts = Object.entries(value.counts).sort(([left], [right]) =>
      left.localeCompare(right),
    ) as [string, number][];
  }
  return JSON.stringify({ path, approval: hashes, counts });
}

function ownerKey(owner: Owner): string {
  if (
    !owner.userId ||
    owner.userId.length > 256 ||
    !owner.mode ||
    owner.mode.length > 32
  )
    throw Error("登录身份未确认，未提交任务");
  return JSON.stringify([owner.mode, owner.userId]);
}

function sessionStorageForJobs(): Pick<Storage, "getItem" | "setItem"> {
  try {
    return window.sessionStorage;
  } catch {
    throw Error("浏览器会话存储不可用，无法可靠保留待确认操作，未提交任务");
  }
}

export class ServiceJobIntentStore {
  private storage: Pick<Storage, "getItem" | "setItem">;
  constructor(storage = sessionStorageForJobs()) {
    this.storage = storage;
  }
  private read(): Entry[] {
    let raw: string | null;
    try {
      raw = this.storage.getItem(STORAGE_KEY);
    } catch {
      throw Error("无法读取浏览器待确认操作，未提交新任务");
    }
    if (!raw) return [];
    if (raw.length > 256_000)
      throw Error("浏览器待确认操作索引超出范围，未提交新任务");
    let value: unknown;
    try {
      value = JSON.parse(raw);
    } catch {
      throw Error("浏览器待确认操作索引损坏，请先核对任务记录，未提交新任务");
    }
    if (
      !record(value) ||
      value.schema !== 1 ||
      !Array.isArray(value.items) ||
      value.items.length > MAX_PENDING
    )
      throw Error("浏览器待确认操作索引版本无效，未提交新任务");
    const seen = new Set<string>();
    return value.items.map((entry) => {
      if (
        !record(entry) ||
        typeof entry.owner !== "string" ||
        entry.owner.length > 600 ||
        typeof entry.fingerprint !== "string" ||
        entry.fingerprint.length > 2048 ||
        typeof entry.operationKey !== "string" ||
        !SIMPLE_ID.test(entry.operationKey) ||
        (entry.jobId !== undefined &&
          (typeof entry.jobId !== "string" || !SIMPLE_ID.test(entry.jobId)))
      )
        throw Error("浏览器待确认操作内容无效，未提交新任务");
      const identity = `${entry.owner}\n${entry.fingerprint}`;
      if (seen.has(identity))
        throw Error("浏览器待确认操作存在冲突，未提交新任务");
      seen.add(identity);
      return {
        owner: entry.owner,
        fingerprint: entry.fingerprint,
        operationKey: entry.operationKey,
        ...(entry.jobId ? { jobId: entry.jobId } : {}),
      } as Entry;
    });
  }
  private write(items: Entry[]) {
    try {
      this.storage.setItem(STORAGE_KEY, JSON.stringify({ schema: 1, items }));
    } catch {
      throw Error("无法保存浏览器待确认操作；请允许会话存储后再操作");
    }
  }
  get(owner: Owner, path: string, body?: unknown): PendingServiceIntent {
    const ownerId = ownerKey(owner),
      fingerprint = serviceIntentFingerprint(path, body);
    const items = this.read();
    const existing = items.find(
      (entry) => entry.owner === ownerId && entry.fingerprint === fingerprint,
    );
    if (existing) return { ...existing, created: false };
    if (items.length >= MAX_PENDING)
      throw Error("待确认操作过多，请先核对任务记录，未提交新任务");
    const entry = {
      owner: ownerId,
      fingerprint,
      operationKey: createOperationKey("service"),
    };
    this.write([...items, entry]);
    return { ...entry, created: true };
  }
  accepted(pending: PendingServiceIntent, jobId: unknown): string {
    if (typeof jobId !== "string" || !SIMPLE_ID.test(jobId))
      throw Object.assign(
        Error("任务受理结果缺少有效任务标识，请保留原操作并核对记录"),
        { outcomeUnknown: true },
      );
    const items = this.read();
    const entry = items.find(
      (value) =>
        value.owner === pending.owner &&
        value.fingerprint === pending.fingerprint &&
        value.operationKey === pending.operationKey,
    );
    if (!entry)
      throw Object.assign(
        Error(`任务 ${jobId} 已受理，本机操作索引已变化，请先查询该任务`),
        { outcomeUnknown: true, acceptedJobId: jobId },
      );
    entry.jobId = jobId;
    this.write(items);
    return jobId;
  }
  resolved(pending: PendingServiceIntent) {
    this.write(
      this.read().filter(
        (entry) =>
          entry.owner !== pending.owner ||
          entry.fingerprint !== pending.fingerprint ||
          entry.operationKey !== pending.operationKey,
      ),
    );
  }
}
