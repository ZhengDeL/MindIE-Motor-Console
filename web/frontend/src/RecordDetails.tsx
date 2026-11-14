import { ChevronLeft, ChevronRight, FileJson } from "./icons";
import { when } from "./api";
import type { ReactNode } from "react";
import { Badge, Button, Copy, Drawer } from "./ui";
import {
  auditAction,
  auditStatusLabel,
  auditTarget,
  logContext,
  recordActor,
  recordHttpStatus,
  recordText,
  recordTime,
} from "./operationRecords";
import type {
  AuditRecord,
  OperationRecord,
  SystemLog,
} from "./operationRecords";

function Value({
  label,
  value,
  code = false,
}: {
  label: string;
  value: ReactNode;
  code?: boolean;
}) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div className="record-property">
      <dt>{label}</dt>
      <dd>{code ? <code>{value}</code> : value}</dd>
    </div>
  );
}

export default function RecordDetails({
  record,
  audit,
  records,
  onSelect,
  onClose,
}: {
  record: OperationRecord;
  audit: boolean;
  records: OperationRecord[];
  onSelect: (record: OperationRecord) => void;
  onClose: () => void;
}) {
  const entry = record as AuditRecord;
  const log = record as SystemLog;
  const index = records.findIndex((item) => item.id === record.id);
  const time = recordTime(audit ? entry.time : log.timestamp);
  const actor = recordActor(record);
  const name = recordText(record.actor_display_name);
  const raw = JSON.stringify(record, null, 2);
  const title = audit ? auditAction(entry) : recordText(log.body) || "系统日志";
  const target = audit ? auditTarget(entry) : logContext(log);
  const request = recordText(record.request_id);
  const method = recordText(log.attributes?.method);
  const path = recordText(log.attributes?.path);
  return (
    <Drawer
      title={audit ? "操作审计详情" : "系统日志详情"}
      subtitle="在当前列表中核对记录、身份和请求关联"
      contentKey={record.id}
      onClose={onClose}
      footer={(dismiss) => (
        <>
          <div className="record-navigation" aria-label="本页记录导航">
            <button
              type="button"
              className="icon-btn"
              aria-label="上一条记录"
              disabled={index <= 0}
              onClick={() => onSelect(records[index - 1])}
            >
              <ChevronLeft size={17} />
            </button>
            <span>
              {index >= 0
                ? `本页 ${index + 1} / ${records.length}`
                : "所选记录"}
            </span>
            <button
              type="button"
              className="icon-btn"
              aria-label="下一条记录"
              disabled={index < 0 || index >= records.length - 1}
              onClick={() => onSelect(records[index + 1])}
            >
              <ChevronRight size={17} />
            </button>
          </div>
          <Button onClick={dismiss}>关闭详情</Button>
        </>
      )}
    >
      <section className="record-overview" aria-label="记录摘要">
        <div className="record-overview-meta">
          {audit && recordText(entry.status) ? (
            <Badge status={entry.status}>{auditStatusLabel(entry.status)}</Badge>
          ) : null}
          {!audit && recordText(log.severity_text) ? (
            <span
              className={`severity severity-${log.severity_text.toLowerCase()}`}
            >
              {log.severity_text}
            </span>
          ) : null}
          {time && <time dateTime={time}>{when(time)}</time>}
        </div>
        <h3>{title}</h3>
        {!audit && target && <p>{target}</p>}
      </section>
      <section
        className="record-section"
        aria-labelledby="record-information-heading"
      >
        <h3 id="record-information-heading">
          {audit ? "操作信息" : "日志信息"}
        </h3>
        <dl className="record-properties">
          <Value label="操作身份" value={actor} />
          <Value
            label="显示名称"
            value={name && name !== actor ? name : null}
          />
          <Value
            label="身份名称来源"
            value={
              record.actor_name_source === "snapshot"
                ? "记录时的账号信息"
                : record.actor_name_source === "current_directory"
                  ? "当前账号目录"
                  : null
            }
          />
          {audit ? (
            <>
              <Value label="操作对象" value={auditTarget(entry)} />
            </>
          ) : (
            <>
              <Value label="服务" value={recordText(log["service.name"])} />
              <Value label="请求方法" value={method} />
              <Value label="请求路径" value={path} code />
              <Value
                label="HTTP 状态码"
                value={recordHttpStatus(log.attributes?.status_code)}
              />
            </>
          )}
        </dl>
        {record.actor_name_source === "current_directory" && (
          <p className="record-context-note">
            早期记录未保存账号名称，此处按当前账号目录关联；原始身份标识保持不变。
          </p>
        )}
      </section>
      {audit && recordText(entry.detail) && (
        <section className="record-section">
          <h3>执行说明</h3>
          <p className="record-description">{entry.detail}</p>
        </section>
      )}
      {request && (
        <section className="record-section" aria-label="请求关联">
          <h3>请求关联</h3>
          <div className="record-reference">
            <code>{request}</code>
            <Copy value={request} label="复制完整请求 ID" />
          </div>
          <p className="record-context-note">
            可在系统日志的请求 ID 条件中查询同一次请求。
          </p>
        </section>
      )}
      <details className="record-raw">
        <summary>
          <FileJson size={16} />
          <span>原始记录</span>
          <code>JSON</code>
        </summary>
        <div className="record-raw-toolbar">
          <span>保留原始标识与字段</span>
          <Copy value={raw} label="复制记录详情" />
        </div>
        <pre>{raw}</pre>
      </details>
    </Drawer>
  );
}
