import { UiElement, UiRegion, withUiSlot, UiButton, UiText } from "./uiPluginSdk";
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

function DefaultOfflineGuide({ entry, guide }: { entry: Capability; guide: CapabilityGuide }) {
  const sections = useRef<Record<string, HTMLElement | null>>({});
  const sectionRef = (key: string) => (element: HTMLElement | null) => {
    sections.current[key] = element;
  };
  return (
    <UiElement as="div" className="capability-guide">
      <UiElement as="div" className="capability-guide-intro">
        <UiElement as="div" className="capability-guide-meta">
          <UiElement as="span"><UiText>{"离线配置指南"}</UiText></UiElement>
          <UiElement as="span"><UiText>{"固定源码"}</UiText> {guide.motor_commit.slice(0, 12)}</UiElement>
          {entry.maturity === "poc" && <UiElement as="span" className="guide-poc"><UiText>{"POC · 需独立验证收益"}</UiText></UiElement>}
        </UiElement>
        <UiElement as="p">{guide.purpose}</UiElement>
        <UiElement as="div" className="capability-guide-path">
          <UiElement as="code">{guide.config_path}</UiElement>
          <Copy value={guide.config_path} label="复制原生配置路径" />
        </UiElement>
        <UiElement as="div" className="capability-guide-merge-note" role="note">
          <UiElement as="b"><UiText>{"先保留完整基线，再逐字段合并"}</UiText></UiElement>
          <UiElement as="p"><UiText>{"下面的配置是增量片段，不能覆盖整份配置。先从已验证模板创建副本，保留未展示的原生字段；替换示例地址、模型、目录和计数后执行预检。复制示例不会修改配置，也不会运行命令。"}</UiText></UiElement>
          <UiElement as="p"><UiText>{"命令示例面向 Linux 管理员终端。NAMESPACE、POD、CONTAINER 等变量须先设为实际值；命令需要的认证与证书沿用当前环境。"}</UiText></UiElement>
        </UiElement>
      </UiElement>
      <UiElement as="nav" className="capability-guide-nav" aria-label="功能指南章节">
        {guideSections.map(([key, label]) => (
          <UiButton actionId="action.CapabilitiesPage.001" key={key} type="button" onClick={() => {
            const reduceMotion = document.documentElement.dataset.motion === "reduced" || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
            sections.current[key]?.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
          }}>{label}</UiButton>
        ))}
      </UiElement>
      <UiRegion name="layout.panel" as="section" ref={sectionRef("prerequisites")} className="capability-guide-section" aria-label="准备条件">
        <UiElement as="h3"><UiText>{"准备条件"}</UiText></UiElement>
        <UiElement as="ul">{guide.prerequisites.map((text, index) => <UiElement as="li" key={index}>{text}</UiElement>)}</UiElement>
        {guide.notes.length > 0 && <UiElement as="ul">{guide.notes.map((text, index) => <UiElement as="li" key={index}>{text}</UiElement>)}</UiElement>}
      </UiRegion>
      <UiRegion name="layout.panel" as="section" ref={sectionRef("examples")} className="capability-guide-section" aria-label="配置示例">
        <UiElement as="h3"><UiText>{"配置示例"}</UiText></UiElement>
        {guide.examples.map((example, index) => (
          <UiElement as="article" className="capability-guide-example" key={index}>
            <UiElement as="div" className="capability-guide-example-head">
              <UiElement as="div"><UiElement as="h4">{example.title}</UiElement><UiElement as="small">{example.target} <UiText>{"·"}</UiText> {example.format.toUpperCase()}</UiElement></UiElement>
              <Copy value={example.code} label={`复制示例 ${index + 1}：${example.title}`} />
            </UiElement>
            <UiElement as="pre" tabIndex={0} aria-label={example.title}><UiElement as="code">{example.code}</UiElement></UiElement>
            {example.notes.length > 0 && <UiElement as="ul">{example.notes.map((text, noteIndex) => <UiElement as="li" key={noteIndex}>{text}</UiElement>)}</UiElement>}
          </UiElement>
        ))}
      </UiRegion>
      <UiRegion name="layout.panel" as="section" ref={sectionRef("steps")} className="capability-guide-section" aria-label="平台操作">
        <UiElement as="h3"><UiText>{"平台操作"}</UiText></UiElement>
        <UiElement as="ol">{guide.steps.map((text, index) => <UiElement as="li" key={index}>{text}</UiElement>)}</UiElement>
      </UiRegion>
      <UiRegion name="layout.panel" as="section" ref={sectionRef("validation")} className="capability-guide-section" aria-label="验证结果">
        <UiElement as="h3"><UiText>{"验证方式与预期结果"}</UiText></UiElement>
        <UiElement as="dl" className="capability-guide-validation">
          {guide.validation.map((item, index) => <UiElement as="div" key={index}><UiElement as="dt">{item.action}</UiElement><UiElement as="dd">{item.expected}</UiElement></UiElement>)}
        </UiElement>
      </UiRegion>
      <UiRegion name="layout.panel" as="section" ref={sectionRef("rollback")} className="capability-guide-section" aria-label="恢复基线">
        <UiElement as="h3"><UiText>{"回滚与恢复"}</UiText></UiElement>
        <UiElement as="ol">{guide.rollback.map((text, index) => <UiElement as="li" key={index}>{text}</UiElement>)}</UiElement>
      </UiRegion>
      <UiElement as="details" className="capability-guide-sources">
        <UiElement as="summary"><UiText>{"本地源码依据 ·"}</UiText> {guide.sources.length} <UiText>{"处"}</UiText></UiElement>
        <UiElement as="ul">{guide.sources.map((source, index) => <UiElement as="li" key={index}><UiElement as="code">{source.path}</UiElement><UiElement as="span">{source.reason}</UiElement></UiElement>)}</UiElement>
      </UiElement>
    </UiElement>
  );
}

function DefaultCapabilitiesPage({
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
      <UiElement as="div" className="capabilities-summary">
        <UiElement as="span">
          <UiText>{"原生能力"}</UiText> <UiElement as="b">{capabilities.length}</UiElement> <UiText>{"项"}</UiText>
        </UiElement>
        <UiElement as="span">
          <UiText>{"当前运行时提供配置入口"}</UiText> <UiElement as="b">{available}</UiElement> <UiText>{"项"}</UiText>
        </UiElement>
        <UiElement as="p"><UiText>{"按固定版本的能力声明与入口约束展示；部署前执行环境预检。"}</UiText></UiElement>
      </UiElement>
      <UiElement as="div" className="features-toolbar">
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
        <Button actionId="action.CapabilitiesPage.002" disabled={!onCreate} onClick={onCreate}>
          <Plus size={14} />
          <UiText>{"新服务配置"}</UiText>
        </Button>
      </UiElement>
      <UiElement as="div" className="feature-categories">
        {categories.map((entry) => (
          <UiButton actionId="action.CapabilitiesPage.003"
            key={entry}
            className={category === entry ? "active" : ""}
            onClick={() => setCategory(entry)}
          >
            {entry}
            <UiElement as="span">
              {entry === "全部能力"
                ? capabilities.length
                : capabilities.filter((item) => item.category === entry).length}
            </UiElement>
          </UiButton>
        ))}
      </UiElement>
      <UiRegion name="layout.panel" as="section" className="panel capabilities-panel">
        {tableOverflows && (
          <UiElement as="div" className="capabilities-scroll-tools">
            <UiElement as="span"><UiText>{"可横向滚动查看全部列"}</UiText></UiElement>
            <UiElement as="div">
              <Button actionId="action.CapabilitiesPage.004" size="small" onClick={() => showColumns("start")}><UiText>{"功能列"}</UiText></Button>
              <Button actionId="action.CapabilitiesPage.005" size="small" onClick={() => showColumns("end")}><UiText>{"说明与配置"}</UiText></Button>
            </UiElement>
          </UiElement>
        )}
        <UiElement as="div" ref={tableScroll} className="table-wrap capabilities-table-scroll" role="region" aria-label="功能能力列表，可横向滚动查看全部列" tabIndex={0}>
          <UiElement as="table" className="capabilities-table">
            <UiElement as="colgroup">
              <UiElement as="col" className="capability-col-name" />
              <UiElement as="col" className="capability-col-engines" />
              <UiElement as="col" className="capability-col-runtime" />
              <UiElement as="col" className="capability-col-description" />
              <UiElement as="col" className="capability-col-actions" />
            </UiElement>
            <UiElement as="thead">
              <UiElement as="tr">
                <UiElement as="th" scope="col"><UiText>{"功能 / 分类"}</UiText></UiElement>
                <UiElement as="th" scope="col"><UiText>{"适用引擎"}</UiText></UiElement>
                <UiElement as="th" scope="col"><UiText>{"当前运行时"}</UiText></UiElement>
                <UiElement as="th" scope="col"><UiText>{"说明"}</UiText></UiElement>
                <UiElement as="th" scope="col"><UiText>{"配置"}</UiText></UiElement>
              </UiElement>
            </UiElement>
            <UiElement as="tbody">
              {displayed.map((entry) => {
                const supported =
                  supportedFor(entry, runtime) &&
                  entry.execution_supported?.[runtime] !== false;
                return (
                  <Fragment key={entry.id}>
                    <UiElement as="tr" className="feature-card">
                      <UiElement as="td" className="capability-name">
                        <UiElement as="b">{entry.name}</UiElement>
                        <UiElement as="span" className="cell-secondary">{entry.category}</UiElement>
                      </UiElement>
                      <UiElement as="td" className="capability-engine-cell">
                        {entry.engines?.length ? (
                          <UiElement as="div" className="capability-engines">
                            {entry.engines.map((engine) => (
                              <UiElement as="span" className="tag" key={engine}>
                                {engine === "vllm"
                                  ? "vLLM Ascend"
                                  : engine === "sglang"
                                    ? "SGLang"
                                    : engine}
                              </UiElement>
                            ))}
                          </UiElement>
                        ) : null}
                      </UiElement>
                      <UiElement as="td" className="capability-runtime">
                        <UiElement as="span"
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
                        </UiElement>
                      </UiElement>
                      <UiElement as="td" className="capability-description">
                        {entry.description}
                      </UiElement>
                      <UiElement as="td" className="capability-actions">
                        <UiButton actionId="action.CapabilitiesPage.006"
                          className="text-link"
                          aria-expanded={expanded === entry.id}
                          onClick={() => setExpanded(entry.id)}
                        >
                          <UiText>{"配置说明"}</UiText>
                          <ChevronRight size={13} />
                        </UiButton>
                      </UiElement>
                    </UiElement>
                    {expanded === entry.id && (
                      <Drawer
                        title={entry.name}
                        subtitle="离线配置指南 · 示例、操作与验证"
                        contentKey={entry.id}
                        onClose={() => setExpanded("")}
                      >
                        {(entry.adapter_note ||
                          entry.execution_supported?.[runtime] === false) && (
                          <UiElement as="div"
                            className="capability-adapter-detail"
                            role="note"
                          >
                            <UiElement as="b"><UiText>{"运行时条件"}</UiText></UiElement>
                            <UiElement as="p">
                              {runtime === "docker" &&
                              entry.execution_supported?.docker === false &&
                              supportedFor(entry, "kubernetes")
                                ? "当前版本通过 Kubernetes 提供此能力的部署入口。切换运行时后，完成原生配置并执行部署预检。"
                                : entry.adapter_note ||
                                  "请核对当前运行时入口的部署条件。"}
                            </UiElement>
                            {runtime === "docker" &&
                              entry.execution_supported?.docker === false &&
                              knownText(entry.adapter_note) && (
                                <UiElement as="details" className="capability-technical-note">
                                  <UiElement as="summary"><UiText>{"原生入口约束"}</UiText></UiElement>
                                  <UiElement as="p">{entry.adapter_note}</UiElement>
                                </UiElement>
                              )}
                            {runtime !== "kubernetes" && (
                              <UiButton actionId="action.CapabilitiesPage.007"
                                className="text-link"
                                onClick={() => setRuntime("kubernetes")}
                              >
                                <UiText>{"切换到 Kubernetes 查看"}</UiText>
                              </UiButton>
                            )}
                          </UiElement>
                        )}
                        {entry.guide?.schema_version === 1 ? (
                          <OfflineGuide entry={entry} guide={entry.guide} />
                        ) : (
                          <UiElement as="div" className="feature-config">
                            <UiElement as="span"><UiText>{"当前控制台版本尚未提供此项离线指南。请升级配套控制台后查看；不会据此自动修改或启用配置。"}</UiText></UiElement>
                            {knownText(entry.config_hint) && (
                              <>
                                <UiElement as="label"><UiText>{"原生配置入口"}</UiText></UiElement>
                                <UiElement as="div" className="feature-config-path">
                                  <UiElement as="code">{entry.config_hint}</UiElement>
                                  <Copy value={entry.config_hint} label="复制配置路径" />
                                </UiElement>
                              </>
                            )}
                          </UiElement>
                        )}
                        <UiElement as="div" className="feature-config">
                          {docsUrl && (
                            <UiElement as="a"
                              href={docsUrl}
                              target="_blank"
                              rel="noreferrer"
                            >
                              <UiText>{"使用文档"}</UiText>
                              <ExternalLink size={13} />
                            </UiElement>
                          )}
                          {entry.validation === "native_declared" && (
                            <UiElement as="span">
                              <UiText>{"按固定版本的原生声明展示，目标环境需通过部署预检。"}</UiText>
                            </UiElement>
                          )}
                        </UiElement>
                      </Drawer>
                    )}
                  </Fragment>
                );
              })}
            </UiElement>
          </UiElement>
        </UiElement>
        {!displayed.length && (
          <Empty
            title="没有匹配的功能"
            description="调整分类或搜索条件后重试。"
          />
        )}
      </UiRegion>
    </>
  );
}

const OfflineGuide = withUiSlot("component.OfflineGuide", DefaultOfflineGuide);
const CapabilitiesPage = withUiSlot("component.CapabilitiesPage", DefaultCapabilitiesPage);
export default CapabilitiesPage;

// Reference package implementation exports.
export { DefaultOfflineGuide, DefaultCapabilitiesPage };
