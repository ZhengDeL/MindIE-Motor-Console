import { UiElement, UiRegion, withUiSlot, UiButton, UiText } from "./uiPluginSdk";
import { useEffect, useRef, useState } from "react";
import { readCount } from "./readState";
import {
  ChevronLeft,
  ChevronRight,
  Download,
  RefreshCw,
  Search,
} from "./icons";
import { api, download, downloadFile, when } from "./api";
import RecordDetails from "./RecordDetails";
import {
  auditAction,
  auditStatusLabel,
  auditTarget,
  logContext,
  recordActor,
  recordText,
  recordTime,
} from "./operationRecords";
import type {
  AuditRecord,
  OperationRecord,
  SystemLog,
} from "./operationRecords";
import {
  Badge,
  Button,
  Copy,
  Empty,
  ErrorBox,
  Field,
  Select,
  Spinner,
} from "./ui";

type PageResult<T> = {
  items: T[];
  total: number;
  limit: number;
  offset: number;
};
type Filters = {
  q: string;
  severity: string;
  actor: string;
  request_id: string;
  event: string;
  path: string;
  status_code: string;
  since: string;
  until: string;
  action: string;
  status: string;
};
const initial: Filters = {
  q: "",
  severity: "",
  actor: "",
  request_id: "",
  event: "",
  path: "",
  status_code: "",
  since: "",
  until: "",
  action: "",
  status: "",
};
function queryFor(
  filters: Filters,
  offset: number,
  limit: number,
  audit: boolean,
) {
  const query = new URLSearchParams({
    offset: String(offset),
    limit: String(limit),
  });
  const keys = audit
    ? ["actor", "request_id", "action", "status", "since", "until"]
    : [
        "q",
        "severity",
        "actor",
        "request_id",
        "event",
        "path",
        "status_code",
        "since",
        "until",
      ];
  for (const key of keys) {
    const value = filters[key as keyof Filters].trim();
    if (value)
      query.set(
        audit && key === "actor" &&
          !/^(?:usr_|oidc:)|^(?:system|anonymous)$/i.test(value)
          ? "actor_username"
          : key,
        key === "since" || key === "until"
          ? new Date(value).toISOString()
          : value,
      );
  }
  return query.toString();
}

function DefaultSystemLogs({ audit = false }: { audit?: boolean }) {
  const [draft, setDraft] = useState<Filters>(initial);
  const [filters, setFilters] = useState<Filters>(initial);
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(50);
  const [data, setData] = useState<PageResult<OperationRecord>>({
    items: [],
    total: 0,
    offset: 0,
    limit: 50,
  });
  const [loading, setLoading] = useState(true);
  const [successfulQuery, setSuccessfulQuery] = useState<string | null>(null);
  const queryKey = `${audit ? "/audit" : "/system/logs"}?${queryFor(filters, offset, limit, audit)}`;
  const hasSuccess = successfulQuery === queryKey;
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState("");
  const [more, setMore] = useState(false);
  const [selected, setSelected] = useState<OperationRecord | null>(null);
  const requestNumber = useRef(0);
  const load = async () => {
    const currentRequest = ++requestNumber.current;
    setLoading(true);
    setError("");
    try {
      const response = await api<PageResult<OperationRecord>>(
        queryKey,
      );
      if (requestNumber.current === currentRequest) { setData(response); setSuccessfulQuery(queryKey); }
    } catch (e) {
      if (requestNumber.current === currentRequest) {
        setError((e as Error).message);
      }
    } finally {
      if (requestNumber.current === currentRequest) setLoading(false);
    }
  };
  useEffect(() => {
    setSelected(null);
    void load();
    return () => {
      requestNumber.current++;
    };
  }, [filters, offset, limit, audit]);
  const hasActors = data.items.some(
    (item) => recordText(item.actor) || recordActor(item),
  );
  const hasRequests = data.items.some((item) => recordText(item.request_id));
  const field = (key: keyof Filters, label: string, type = "text") => (
    <Field label={label}>
      <UiElement as="input"
        aria-label={label}
        type={type}
        value={draft[key]}
        onChange={(e) =>
          setDraft((values) => ({ ...values, [key]: e.target.value }))
        }
      />
    </Field>
  );
  return (
    <>
      <UiRegion name="layout.panel" as="section" className="panel query-panel">
        <UiElement as="form"
          className="record-filters"
          onSubmit={(event) => {
            event.preventDefault();
            if (
              draft.since &&
              draft.until &&
              new Date(draft.since) > new Date(draft.until)
            ) {
              setError("开始时间须早于结束时间");
              return;
            }
            setFilters({ ...draft });
            setOffset(0);
          }}
        >
          {audit ? field("action", "操作名称") : field("q", "搜索日志内容")}
          {audit ? (
            <Field label="操作结果">
              <Select
                value={draft.status}
                onChange={(value) =>
                  setDraft((values) => ({ ...values, status: value }))
                }
                ariaLabel="操作结果"
              >
                <UiElement as="option" value=""><UiText>{"全部结果"}</UiText></UiElement>
                <UiElement as="option" value="success"><UiText>{"成功"}</UiText></UiElement>
                <UiElement as="option" value="partial"><UiText>{"未完整"}</UiText></UiElement>
                <UiElement as="option" value="error"><UiText>{"异常"}</UiText></UiElement>
                <UiElement as="option" value="failed"><UiText>{"失败"}</UiText></UiElement>
              </Select>
            </Field>
          ) : (
            <Field label="日志级别">
              <Select
                value={draft.severity}
                onChange={(value) =>
                  setDraft((values) => ({ ...values, severity: value }))
                }
                ariaLabel="日志级别"
              >
                <UiElement as="option" value=""><UiText>{"全部级别"}</UiText></UiElement>
                {["TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL"].map(
                  (value) => (
                    <UiElement as="option" key={value} value={value}>
                      {value}
                    </UiElement>
                  ),
                )}
              </Select>
            </Field>
          )}
          {field("since", "开始时间", "datetime-local")}
          {field("until", "结束时间", "datetime-local")}
          <UiElement as="div" className="query-actions">
            <Button actionId="action.SystemLogs.001" type="submit" kind="primary" loading={loading}>
              <Search size={14} />
              <UiText>{"查询"}</UiText>
            </Button>
            <Button actionId="action.SystemLogs.002"
              onClick={() => {
                setDraft(initial);
                setFilters({ ...initial });
                setOffset(0);
              }}
            >
              <UiText>{"重置"}</UiText>
            </Button>
            <Button actionId="action.SystemLogs.003" kind="text" onClick={() => setMore(!more)}>
              {more ? "收起条件" : "更多条件"}
            </Button>
          </UiElement>
          {more && (
            <UiElement as="div" className="record-more-filters">
              {field("actor", audit ? "操作账号或身份标识" : "操作身份标识")}
              {field("request_id", "请求 ID")}
              {!audit && (
                <>
                  {field("event", "事件名称")}
                  {field("path", "请求路径")}
                  {field("status_code", "HTTP 状态码", "number")}
                </>
              )}
            </UiElement>
          )}
        </UiElement>
        <UiElement as="div" className="query-note">
          <UiText>{"时间按当前浏览器时区填写；记录显示北京时间 UTC+8。"}</UiText>
          {audit
            ? "操作审计记录身份、对象与结果。"
            : "控制台系统日志按保留窗口查询；导出当前筛选结果，最多 10,000 条。"}
        </UiElement>
      </UiRegion>
      <UiRegion name="layout.panel" as="section" className="panel">
        <UiElement as="div" className="table-toolbar">
          <UiElement as="b">{audit ? "操作审计" : "系统日志"}</UiElement>
          {hasSuccess && (
            <UiElement as="span" className="toolbar-right-note">
              {readCount({ loaded: true, refreshing: loading, error }, data.total, "条")}
            </UiElement>
          )}
          <Button actionId="action.SystemLogs.004" onClick={() => void load()} loading={loading}>
            <RefreshCw size={14} />
            <UiText>{"刷新"}</UiText>
          </Button>
          <Button actionId="action.SystemLogs.005"
            disabled={!hasSuccess || loading || !!error || !data.items.length}
            loading={downloading}
            onClick={async () => {
              setDownloading(true);
              setError("");
              try {
                if (audit)
                  download(
                    data.items,
                    `mindie-motor-console-audit-page-${Math.floor(offset / limit) + 1}.json`,
                  );
                else {
                  const params = new URLSearchParams(
                    queryFor(filters, 0, 10000, false),
                  );
                  params.delete("offset");
                  await downloadFile(
                    `/system/logs/export?${params}`,
                    "mindie-motor-console-system-logs-filtered.jsonl",
                  );
                }
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setDownloading(false);
              }
            }}
          >
            <Download size={14} />
            {audit ? "导出当前页" : "导出筛选结果"}
          </Button>
        </UiElement>
        {error && <ErrorBox message={error} />}
        {loading && !hasSuccess ? (
          <Spinner />
        ) : hasSuccess && data.items.length ? (
          <UiElement as="div" className="table-wrap">
            <UiElement as="table" className="records-table">
              <UiElement as="thead">
                <UiElement as="tr">
                  <UiElement as="th"><UiText>{"时间（北京时间）"}</UiText></UiElement>
                  <UiElement as="th">{audit ? "结果" : "级别"}</UiElement>
                  {hasActors && <UiElement as="th"><UiText>{"操作身份"}</UiText></UiElement>}
                  <UiElement as="th">{audit ? "操作 / 目标" : "事件 / 请求路径"}</UiElement>
                  {hasRequests && <UiElement as="th"><UiText>{"请求 ID"}</UiText></UiElement>}
                  <UiElement as="th"><UiText>{"详情"}</UiText></UiElement>
                </UiElement>
              </UiElement>
              <UiElement as="tbody">
                {data.items.map((item) => {
                  const record = item as SystemLog;
                  const entry = item as AuditRecord;
                  const time = recordTime(
                    audit ? entry.time : record.timestamp,
                  );
                  const actor = recordActor(item);
                  const request = recordText(item.request_id);
                  const context = audit
                    ? auditTarget(entry)
                    : logContext(record);
                  return (
                    <UiElement as="tr"
                      key={item.id}
                      data-selected={selected?.id === item.id || undefined}
                    >
                      <UiElement as="td" className="tabular nowrap">{time && when(time)}</UiElement>
                      <UiElement as="td">
                        {audit ? (
                          recordText(entry.status) && (
                            <Badge status={entry.status}>
                              {auditStatusLabel(entry.status)}
                            </Badge>
                          )
                        ) : recordText(record.severity_text) ? (
                          <UiElement as="span"
                            className={`severity severity-${record.severity_text.toLowerCase()}`}
                          >
                            {record.severity_text}
                          </UiElement>
                        ) : null}
                      </UiElement>
                      {hasActors && (
                        <UiElement as="td" className="record-actor-cell">
                          {actor ||
                            (recordText(item.actor) && (
                              <UiButton actionId="action.SystemLogs.006"
                                className="text-link"
                                onClick={() => setSelected(item)}
                                aria-haspopup="dialog"
                              >
                                <UiText>{"身份记录"}</UiText>
                              </UiButton>
                            ))}
                        </UiElement>
                      )}
                      <UiElement as="td" className="record-subject-cell">
                        <UiElement as="b">
                          {audit ? auditAction(entry) : recordText(record.body)}
                        </UiElement>
                        {context && (
                          <UiElement as="span" className="cell-secondary">{context}</UiElement>
                        )}
                      </UiElement>
                      {hasRequests && (
                        <UiElement as="td">
                          {request && (
                            <>
                              <UiElement as="span" className="request-id" title={request}>
                                {request.slice(0, 12)}
                              </UiElement>
                              <Copy value={request} label="复制请求 ID" />
                            </>
                          )}
                        </UiElement>
                      )}
                      <UiElement as="td">
                        <UiButton actionId="action.SystemLogs.007"
                          className="text-link"
                          aria-haspopup="dialog"
                          aria-expanded={selected?.id === item.id}
                          onClick={() => setSelected(item)}
                        >
                          <UiText>{"查看"}</UiText>
                        </UiButton>
                      </UiElement>
                    </UiElement>
                  );
                })}
              </UiElement>
            </UiElement>
          </UiElement>
        ) : (
          <Empty
            title={!hasSuccess ? error ? "本次查询未完成" : "正在读取记录" : "暂无匹配记录"}
            description={
              error
                ? "请根据错误提示调整条件或重新查询。"
                : "调整时间范围或筛选条件后重新查询。"
            }
          />
        )}
        {hasSuccess && !loading && !error && (
          <UiElement as="div" className="table-footer">
            <UiElement as="span">
              {data.total
                ? `${offset + 1}–${Math.min(offset + data.items.length, data.total)}`
                : "0"}{" "}
              <UiText>{"/"}</UiText> {data.total} <UiText>{"条"}</UiText>
            </UiElement>
            <Select
              value={String(limit)}
              onChange={(value) => {
                setLimit(Number(value));
                setOffset(0);
              }}
              ariaLabel="每页记录数"
            >
              {[20, 50, 100].map((value) => (
                <UiElement as="option" key={value} value={value}>
                  {value} <UiText>{"条 / 页"}</UiText>
                </UiElement>
              ))}
            </Select>
            <UiElement as="div" className="pagination">
              <UiButton actionId="action.SystemLogs.008"
                disabled={offset === 0 || loading}
                onClick={() => setOffset(Math.max(0, offset - limit))}
                aria-label="上一页记录"
              >
                <ChevronLeft size={15} />
              </UiButton>
              <UiElement as="span">
                {Math.floor(offset / limit) + 1} <UiText>{"/"}</UiText>{" "}
                {Math.max(1, Math.ceil(data.total / limit))}
              </UiElement>
              <UiButton actionId="action.SystemLogs.009"
                disabled={offset + limit >= data.total || loading}
                onClick={() => setOffset(offset + limit)}
                aria-label="下一页记录"
              >
                <ChevronRight size={15} />
              </UiButton>
            </UiElement>
          </UiElement>
        )}
      </UiRegion>
      {selected && (
        <RecordDetails
          record={selected}
          audit={audit}
          records={data.items}
          onSelect={setSelected}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  );
}

const SystemLogs = withUiSlot("component.SystemLogs", DefaultSystemLogs);
export default SystemLogs;

// Reference package implementation exports.
export { DefaultSystemLogs };
