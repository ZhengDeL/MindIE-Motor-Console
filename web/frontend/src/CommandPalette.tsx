import { UiElement, withUiSlot, UiText } from "./uiPluginSdk";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { ArrowRight, Box, Search, Server } from "./icons";
import type { ConsoleIcon } from "./icons";
import type { NodeInfo, Service } from "./api";
import { statusName } from "./api";
import type { ConsolePage, NavigationGroup } from "./consoleNavigation";
import { Badge, Modal } from "./ui";
import "./command-menu.css";

export type QuickAction = { id: string; label: string; description: string; icon: ConsoleIcon; onSelect: () => void };
type Result = { id: string; label: string; description: string; group: string; icon: ConsoleIcon;
  keywords: string; status?: string; run: () => void };

function DefaultCommandPalette({ groups, services, nodes, actions, externalPages = [], onNavigate, onOpenService, onLocateNode, onClose }: {
  groups: readonly NavigationGroup[]; services: readonly Service[]; nodes: readonly NodeInfo[];
  actions: readonly QuickAction[]; onNavigate: (page: ConsolePage) => void;
  externalPages?: readonly QuickAction[];
  onOpenService: (service: Service) => void; onLocateNode: (node: NodeInfo) => void; onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();
  const results = useMemo(() => {
    // Only public, already-loaded summaries are indexed. Configurations,
    // credentials, logs and query history never enter the command index.
    const all: Result[] = [
      ...actions.map((action) => ({ ...action, group: "快速操作", keywords: action.label + " " + action.description, run: action.onSelect })),
      ...groups.flatMap((group) => group.items.map((item) => ({ id: "page:" + item.id, label: item.label,
        description: group.label, group: "跳转页面", icon: item.icon,
        keywords: `${item.id} ${item.label} ${item.description}`, run: () => onNavigate(item.id) }))),
      ...externalPages.map((item) => ({ ...item, group: "社区插件", keywords: item.label + " " + item.description, run: item.onSelect })),
      ...services.map((service) => ({ id: "service:" + service.id, label: service.name,
        description: `${service.model || "未配置模型"} · ${service.runtime === "docker" ? "Docker" : "Kubernetes"}`,
        group: "推理服务", icon: Box, status: service.status,
        keywords: `${service.name} ${service.model} ${service.engine} ${service.runtime} ${service.runtime === "kubernetes" ? "k8s" : ""} ${statusName(service.status)}`,
        run: () => onOpenService(service) })),
      ...nodes.map((node) => ({ id: "node:" + node.id, label: node.name, description: node.address,
        group: "执行 Agent", icon: Server, status: node.status,
        keywords: `${node.name} ${node.address} ${node.labels.join(" ")} Agent`, run: () => onLocateNode(node) })),
    ];
    const terms = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean).slice(0, 8);
    return all.filter((item) => terms.every((term) => item.keywords.toLocaleLowerCase().includes(term))).slice(0, 40);
  }, [actions, externalPages, groups, services, nodes, query, onNavigate, onOpenService, onLocateNode]);
  const selected = Math.min(active, Math.max(0, results.length - 1));
  useEffect(() => {
    const frame = requestAnimationFrame(() => input.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, []);
  useEffect(() => { setActive(0); }, [query]);
  useEffect(() => { document.getElementById(`${listId}-${selected}`)?.scrollIntoView({ block: "nearest" }); }, [listId, selected]);
  const choose = (item: Result) => { onClose(); item.run(); };
  return <Modal title="快速查找" subtitle="定位已加载的服务与执行 Agent，或跳转控制台页面。" onClose={onClose}>
    <UiElement as="div" className="command-menu">
      <UiElement as="div" className="command-search"><Search size={20} /><UiElement as="input" ref={input} role="combobox" aria-label="搜索资源或页面"
        aria-autocomplete="list" aria-expanded="true" aria-controls={listId}
        aria-activedescendant={results.length ? `${listId}-${selected}` : undefined}
        autoComplete="off" spellCheck={false} maxLength={120} value={query}
        placeholder="搜索服务、模型、Agent、IP 或页面"
        onChange={(event) => setQuery(event.target.value.slice(0, 120))}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing) return;
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            if (results.length) setActive((value) => (value + (event.key === "ArrowDown" ? 1 : -1) + results.length) % results.length);
          } else if (event.key === "Enter" && results[selected]) { event.preventDefault(); choose(results[selected]); }
        }} /><UiElement as="kbd"><UiText>{"Esc"}</UiText></UiElement></UiElement>
      <UiElement as="div" className="command-results" id={listId} role="listbox" aria-label="匹配的资源和页面">
        {results.map((item, index) => <UiElement as="div" key={item.id}>
          {(index === 0 || results[index - 1].group !== item.group) && <UiElement as="div" className="command-group" aria-hidden="true">{item.group}</UiElement>}
          <UiElement as="div" id={`${listId}-${index}`} role="option" aria-selected={index === selected}
            className={`command-option ${index === selected ? "active" : ""}`} onMouseMove={() => setActive(index)}
            onMouseDown={(event) => event.preventDefault()} onClick={() => choose(item)}>
            <UiElement as="span" className="command-option-icon"><item.icon size={18} /></UiElement>
            <UiElement as="span" className="command-option-content"><UiElement as="b">{item.label}</UiElement><UiElement as="small">{item.description}</UiElement></UiElement>
            {item.status && <Badge status={item.status} />}<ArrowRight size={14} />
          </UiElement>
        </UiElement>)}
        {!results.length && <UiElement as="div" className="command-empty"><Search size={25} /><UiElement as="b"><UiText>{"未找到匹配项"}</UiText></UiElement><UiElement as="p"><UiText>{"试试服务名称、模型、执行 Agent 名称、IP 或页面名称。"}</UiText></UiElement></UiElement>}
      </UiElement>
      <UiElement as="div" className="command-footer"><UiElement as="span"><UiElement as="kbd"><UiText>{"↑"}</UiText></UiElement><UiElement as="kbd"><UiText>{"↓"}</UiText></UiElement><UiText>{"选择"}</UiText> <UiElement as="kbd"><UiText>{"Enter"}</UiText></UiElement><UiText>{"打开"}</UiText></UiElement><UiElement as="span">{results.length ? `${results.length} 个匹配项` : "支持中英文关键词"}</UiElement></UiElement>
    </UiElement>
  </Modal>;
}

const CommandPalette = withUiSlot("component.CommandPalette", DefaultCommandPalette);
export default CommandPalette;

// Reference package implementation exports.
export { DefaultCommandPalette };
