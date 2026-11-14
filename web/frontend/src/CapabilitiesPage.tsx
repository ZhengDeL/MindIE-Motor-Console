import { Fragment, useEffect, useRef, useState } from "react";
import {
  Box,
  CheckCircle2,
  ChevronRight,
  ExternalLink,
  Layers3,
  Plus,
} from "./icons";
import { supportedFor } from "./api";
import type { Capability, CapabilityGuide } from "./api";
import { knownText } from "./resourcePresentation";
import { Button, Copy, Drawer, Empty, SearchInput, Segment } from "./ui";
import { documentationUrl as safeDocumentationUrl } from "./documentation";
import "./capability-guides.css";
import "./capabilities-table.css";

const guideSections = [
  ["prerequisites", "准备条件"],
  ["examples", "配置示例"],
  ["steps", "平台操作"],
  ["validation", "验证结果"],
  ["rollback", "恢复基线"],
] as const;

function OfflineGuide({ entry, guide }: { entry: Capability; guide: CapabilityGuide }) {
  const sections = useRef<Record<string, HTMLElement | null>>({});
  const sectionRef = (key: string) => (element: HTMLElement | null) => {
    sections.current[key] = element;
  };
  return (
    <div className="capability-guide">
      <div className="capability-guide-intro">
        <div className="capability-guide-meta">
          <span>离线配置指南</span>
          <span>固定源码 {guide.motor_commit.slice(0, 12)}</span>
          {entry.maturity === "poc" && <span className="guide-poc">POC · 需独立验证收益</span>}
        </div>
        <p>{guide.purpose}</p>
        <div className="capability-guide-path">
          <code>{guide.config_path}</code>
          <Copy value={guide.config_path} label="复制原生配置路径" />
        </div>
        <div className="capability-guide-merge-note" role="note">
          <b>先保留完整基线，再逐字段合并</b>
          <p>下面的配置是增量片段，不能覆盖整份配置。先从已验证模板创建副本，保留未展示的原生字段；替换示例地址、模型、目录和计数后执行预检。复制示例不会修改配置，也不会运行命令。</p>
          <p>命令示例面向 Linux 管理员终端。NAMESPACE、POD、CONTAINER 等变量须先设为实际值；命令需要的认证与证书沿用当前环境。</p>
        </div>
      </div>
      <nav className="capability-guide-nav" aria-label="功能指南章节">
        {guideSections.map(([key, label]) => (
          <button key={key} type="button" onClick={() => {
            const reduceMotion = document.documentElement.dataset.motion === "reduced" || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
            sections.current[key]?.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
          }}>{label}</button>
        ))}
      </nav>
      <section ref={sectionRef("prerequisites")} className="capability-guide-section" aria-label="准备条件">
        <h3>准备条件</h3>
        <ul>{guide.prerequisites.map((text, index) => <li key={index}>{text}</li>)}</ul>
        {guide.notes.length > 0 && <ul>{guide.notes.map((text, index) => <li key={index}>{text}</li>)}</ul>}
      </section>
      <section ref={sectionRef("examples")} className="capability-guide-section" aria-label="配置示例">
        <h3>配置示例</h3>
        {guide.examples.map((example, index) => (
          <article className="capability-guide-example" key={index}>
            <div className="capability-guide-example-head">
              <div><h4>{example.title}</h4><small>{example.target} · {example.format.toUpperCase()}</small></div>
              <Copy value={example.code} label={`复制示例 ${index + 1}：${example.title}`} />
            </div>
            <pre tabIndex={0} aria-label={example.title}><code>{example.code}</code></pre>
            {example.notes.length > 0 && <ul>{example.notes.map((text, noteIndex) => <li key={noteIndex}>{text}</li>)}</ul>}
          </article>
        ))}
      </section>
      <section ref={sectionRef("steps")} className="capability-guide-section" aria-label="平台操作">
        <h3>平台操作</h3>
        <ol>{guide.steps.map((text, index) => <li key={index}>{text}</li>)}</ol>
      </section>
      <section ref={sectionRef("validation")} className="capability-guide-section" aria-label="验证结果">
        <h3>验证方式与预期结果</h3>
        <dl className="capability-guide-validation">
          {guide.validation.map((item, index) => <div key={index}><dt>{item.action}</dt><dd>{item.expected}</dd></div>)}
        </dl>
      </section>
      <section ref={sectionRef("rollback")} className="capability-guide-section" aria-label="恢复基线">
        <h3>回滚与恢复</h3>
        <ol>{guide.rollback.map((text, index) => <li key={index}>{text}</li>)}</ol>
      </section>
      <details className="capability-guide-sources">
        <summary>本地源码依据 · {guide.sources.length} 处</summary>
        <ul>{guide.sources.map((source, index) => <li key={index}><code>{source.path}</code><span>{source.reason}</span></li>)}</ul>
      </details>
    </div>
  );
}

export default function CapabilitiesPage({
  capabilities,
  documentationUrl,
  onCreate,
}: {
  capabilities: Capability[];
  documentationUrl?: string;
  onCreate?: () => void;
}) {
  const docsUrl = safeDocumentationUrl(documentationUrl);
  const [runtime, setRuntime] = useState("docker");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("全部能力");
  const [expanded, setExpanded] = useState("");
  const tableScroll = useRef<HTMLDivElement | null>(null);
  const [tableOverflows, setTableOverflows] = useState(false);
  useEffect(() => {
    const container = tableScroll.current;
    if (!container) return;
    const measure = () => setTableOverflows(container.scrollWidth > container.clientWidth + 2);
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(container);
    window.addEventListener("resize", measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, []);
  const showColumns = (side: "start" | "end") => {
    const container = tableScroll.current;
    if (!container) return;
    const reduced = document.documentElement.dataset.motion === "reduced" || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    container.scrollTo({
      left: side === "start" ? 0 : container.scrollWidth - container.clientWidth,
      behavior: reduced ? "auto" : "smooth",
    });
  };
  const categories = [
    "全部能力",
    ...new Set(capabilities.map((entry) => entry.category)),
  ];
  const available = capabilities.filter(
    (entry) =>
      supportedFor(entry, runtime) &&
      entry.execution_supported?.[runtime] !== false,
  ).length;
  const displayed = capabilities.filter(
    (entry) =>
      (category === "全部能力" || entry.category === category) &&
      `${entry.name} ${entry.description} ${entry.config_hint}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  return (
    <>
      <div className="capabilities-summary">
        <span>
          原生能力 <b>{capabilities.length}</b> 项
        </span>
        <span>
          当前运行时提供配置入口 <b>{available}</b> 项
        </span>
        <p>按固定版本的能力声明与入口约束展示；部署前执行环境预检。</p>
      </div>
      <div className="features-toolbar">
        <Segment
          options={[
            { value: "docker", label: "Docker", icon: <Box size={14} /> },
            {
              value: "kubernetes",
              label: "Kubernetes",
              icon: <Layers3 size={14} />,
            },
          ]}
          value={runtime}
          onChange={setRuntime}
        />
        <SearchInput
          value={query}
          onChange={setQuery}
          placeholder="搜索功能、描述或配置路径"
        />
        <Button disabled={!onCreate} onClick={onCreate}>
          <Plus size={14} />
          新服务配置
        </Button>
      </div>
      <div className="feature-categories">
        {categories.map((entry) => (
          <button
            key={entry}
            className={category === entry ? "active" : ""}
            onClick={() => setCategory(entry)}
          >
            {entry}
            <span>
              {entry === "全部能力"
                ? capabilities.length
                : capabilities.filter((item) => item.category === entry).length}
            </span>
          </button>
        ))}
      </div>
      <section className="panel capabilities-panel">
        {tableOverflows && (
          <div className="capabilities-scroll-tools">
            <span>可横向滚动查看全部列</span>
            <div>
              <Button size="small" onClick={() => showColumns("start")}>功能列</Button>
              <Button size="small" onClick={() => showColumns("end")}>说明与配置</Button>
            </div>
          </div>
        )}
        <div ref={tableScroll} className="table-wrap capabilities-table-scroll" role="region" aria-label="功能能力列表，可横向滚动查看全部列" tabIndex={0}>
          <table className="capabilities-table">
            <colgroup>
              <col className="capability-col-name" />
              <col className="capability-col-engines" />
              <col className="capability-col-runtime" />
              <col className="capability-col-description" />
              <col className="capability-col-actions" />
            </colgroup>
            <thead>
              <tr>
                <th scope="col">功能 / 分类</th>
                <th scope="col">适用引擎</th>
                <th scope="col">当前运行时</th>
                <th scope="col">说明</th>
                <th scope="col">配置</th>
              </tr>
            </thead>
            <tbody>
              {displayed.map((entry) => {
                const supported =
                  supportedFor(entry, runtime) &&
                  entry.execution_supported?.[runtime] !== false;
                return (
                  <Fragment key={entry.id}>
                    <tr className="feature-card">
                      <td className="capability-name">
                        <b>{entry.name}</b>
                        <span className="cell-secondary">{entry.category}</span>
                      </td>
                      <td className="capability-engine-cell">
                        {entry.engines?.length ? (
                          <div className="capability-engines">
                            {entry.engines.map((engine) => (
                              <span className="tag" key={engine}>
                                {engine === "vllm"
                                  ? "vLLM Ascend"
                                  : engine === "sglang"
                                    ? "SGLang"
                                    : engine}
                              </span>
                            ))}
                          </div>
                        ) : null}
                      </td>
                      <td className="capability-runtime">
                        <span
                          className={`support-label ${supported ? "yes" : ""}`}
                        >
                          {supported ? (
                            <CheckCircle2 size={13} />
                          ) : (
                            <Layers3 size={13} />
                          )}
                          {supported
                            ? "提供原生配置入口"
                            : runtime === "docker" &&
                                supportedFor(entry, "kubernetes")
                              ? "使用 Kubernetes 部署"
                              : "当前运行时不支持"}
                        </span>
                      </td>
                      <td className="capability-description">
                        {entry.description}
                      </td>
                      <td className="capability-actions">
                        <button
                          className="text-link"
                          aria-expanded={expanded === entry.id}
                          onClick={() => setExpanded(entry.id)}
                        >
                          配置说明
                          <ChevronRight size={13} />
                        </button>
                      </td>
                    </tr>
                    {expanded === entry.id && (
                      <Drawer
                        title={entry.name}
                        subtitle="离线配置指南 · 示例、操作与验证"
                        contentKey={entry.id}
                        onClose={() => setExpanded("")}
                      >
                        {(entry.adapter_note ||
                          entry.execution_supported?.[runtime] === false) && (
                          <div
                            className="capability-adapter-detail"
                            role="note"
                          >
                            <b>运行时条件</b>
                            <p>
                              {runtime === "docker" &&
                              entry.execution_supported?.docker === false &&
                              supportedFor(entry, "kubernetes")
                                ? "当前版本通过 Kubernetes 提供此能力的部署入口。切换运行时后，完成原生配置并执行部署预检。"
                                : entry.adapter_note ||
                                  "请核对当前运行时入口的部署条件。"}
                            </p>
                            {runtime === "docker" &&
                              entry.execution_supported?.docker === false &&
                              knownText(entry.adapter_note) && (
                                <details className="capability-technical-note">
                                  <summary>原生入口约束</summary>
                                  <p>{entry.adapter_note}</p>
                                </details>
                              )}
                            {runtime !== "kubernetes" && (
                              <button
                                className="text-link"
                                onClick={() => setRuntime("kubernetes")}
                              >
                                切换到 Kubernetes 查看
                              </button>
                            )}
                          </div>
                        )}
                        {entry.guide?.schema_version === 1 ? (
                          <OfflineGuide entry={entry} guide={entry.guide} />
                        ) : (
                          <div className="feature-config">
                            <span>当前控制台版本尚未提供此项离线指南。请升级配套控制台后查看；不会据此自动修改或启用配置。</span>
                            {knownText(entry.config_hint) && (
                              <>
                                <label>原生配置入口</label>
                                <div className="feature-config-path">
                                  <code>{entry.config_hint}</code>
                                  <Copy value={entry.config_hint} label="复制配置路径" />
                                </div>
                              </>
                            )}
                          </div>
                        )}
                        <div className="feature-config">
                          {docsUrl && (
                            <a
                              href={docsUrl}
                              target="_blank"
                              rel="noreferrer"
                            >
                              使用文档
                              <ExternalLink size={13} />
                            </a>
                          )}
                          {entry.validation === "native_declared" && (
                            <span>
                              按固定版本的原生声明展示，目标环境需通过部署预检。
                            </span>
                          )}
                        </div>
                      </Drawer>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
        {!displayed.length && (
          <Empty
            title="没有匹配的功能"
            description="调整分类或搜索条件后重试。"
          />
        )}
      </section>
    </>
  );
}
