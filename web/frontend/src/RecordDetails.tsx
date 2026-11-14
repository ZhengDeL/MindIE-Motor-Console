import { UiElement, UiRegion, withUiSlot, UiButton, UiText } from "./uiPluginSdk";
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

function DefaultValue({
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
    <UiElement as="div" className="record-property">
      <UiElement as="dt">{label}</UiElement>
      <UiElement as="dd">{code ? <UiElement as="code">{value}</UiElement> : value}</UiElement>
    </UiElement>
  );
}

function DefaultRecordDetails({
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
          <UiElement as="div" className="record-navigation" aria-label="本页记录导航">
            <UiButton actionId="action.RecordDetails.001"
              type="button"
              className="icon-btn"
              aria-label="上一条记录"
              disabled={index <= 0}
              onClick={() => onSelect(records[index - 1])}
            >
              <ChevronLeft size={17} />
            </UiButton>
            <UiElement as="span">
              {index >= 0
                ? `本页 ${index + 1} / ${records.length}`
                : "所选记录"}
            </UiElement>
            <UiButton actionId="action.RecordDetails.002"
              type="button"
              className="icon-btn"
              aria-label="下一条记录"
              disabled={index < 0 || index >= records.length - 1}
              onClick={() => onSelect(records[index + 1])}
            >
              <ChevronRight size={17} />
            </UiButton>
          </UiElement>
          <Button actionId="action.RecordDetails.003" onClick={dismiss}><UiText>{"关闭详情"}</UiText></Button>
        </>
      )}
    >
      <UiRegion name="layout.panel" as="section" className="record-overview" aria-label="记录摘要">
        <UiElement as="div" className="record-overview-meta">
          {audit && recordText(entry.status) ? (
            <Badge status={entry.status}>{auditStatusLabel(entry.status)}</Badge>
          ) : null}
          {!audit && recordText(log.severity_text) ? (
            <UiElement as="span"
              className={`severity severity-${log.severity_text.toLowerCase()}`}
            >
              {log.severity_text}
            </UiElement>
          ) : null}
          {time && <UiElement as="time" dateTime={time}>{when(time)}</UiElement>}
        </UiElement>
        <UiElement as="h3">{title}</UiElement>
        {!audit && target && <UiElement as="p">{target}</UiElement>}
      </UiRegion>
      <UiRegion name="layout.panel" as="section"
        className="record-section"
        aria-labelledby="record-information-heading"
      >
        <UiElement as="h3" id="record-information-heading">
          {audit ? "操作信息" : "日志信息"}
        </UiElement>
        <UiElement as="dl" className="record-properties">
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
        </UiElement>
        {record.actor_name_source === "current_directory" && (
          <UiElement as="p" className="record-context-note">
            <UiText>{"早期记录未保存账号名称，此处按当前账号目录关联；原始身份标识保持不变。"}</UiText>
          </UiElement>
        )}
      </UiRegion>
      {audit && recordText(entry.detail) && (
        <UiRegion name="layout.panel" as="section" className="record-section">
          <UiElement as="h3"><UiText>{"执行说明"}</UiText></UiElement>
          <UiElement as="p" className="record-description">{entry.detail}</UiElement>
        </UiRegion>
      )}
      {request && (
        <UiRegion name="layout.panel" as="section" className="record-section" aria-label="请求关联">
          <UiElement as="h3"><UiText>{"请求关联"}</UiText></UiElement>
          <UiElement as="div" className="record-reference">
            <UiElement as="code">{request}</UiElement>
            <Copy value={request} label="复制完整请求 ID" />
          </UiElement>
          <UiElement as="p" className="record-context-note">
            <UiText>{"可在系统日志的请求 ID 条件中查询同一次请求。"}</UiText>
          </UiElement>
        </UiRegion>
      )}
      <UiElement as="details" className="record-raw">
        <UiElement as="summary">
          <FileJson size={16} />
          <UiElement as="span"><UiText>{"原始记录"}</UiText></UiElement>
          <UiElement as="code"><UiText>{"JSON"}</UiText></UiElement>
        </UiElement>
        <UiElement as="div" className="record-raw-toolbar">
          <UiElement as="span"><UiText>{"保留原始标识与字段"}</UiText></UiElement>
          <Copy value={raw} label="复制记录详情" />
        </UiElement>
        <UiElement as="pre">{raw}</UiElement>
      </UiElement>
    </Drawer>
  );
}

const Value = withUiSlot("component.Value", DefaultValue);
const RecordDetails = withUiSlot("component.RecordDetails", DefaultRecordDetails);
export default RecordDetails;

// Reference package implementation exports.
export { DefaultValue, DefaultRecordDetails };
