import type * as React from 'react';
import type { PluginCatalog, AuthStatus, Service, NodeInfo, Template, DeploymentApproval, Overview, ReadState, NpuResourcesState } from './dto.js';
import type { UiActions, UiButtonProps, UiComponentProps, UiElements, UiI18n, UiRegionRenderProps } from './index.js';
export type ConsolePage = 'overview' | 'services' | 'templates' | 'nodes' | 'images' | 'gateways' | 'logs' | 'features' | 'audit' | 'systemlogs' | 'accounts' | 'maintenance' | 'settings';
export type ServiceEditorProps = {
  nodes: NodeInfo[];
  nodesRead: ReadState;
  original?: Service;
  initialTemplate?: Template;
  onClose: () => void;
  onSaved: () => void;
  onDeploy: (service: Service, approval?: DeploymentApproval) => Promise<void>;
};
export type ServiceDetailProps = {
  service: Service;
  nodes: NodeInfo[];
  onBack: () => void;
  onEdit: (s: Service) => void;
  onAction: (s: Service, action: string) => void;
  notify: (message: string) => void;
  canOperate: boolean;
  stopChecking?: boolean;
};
export type LoginPageProps = {
  status: AuthStatus;
  onAuthenticated: () => Promise<void>;
};
export type NodesPageProps = {
  nodes: NodeInfo[];
  resources: NpuResourcesState;
  search: string;
  setSearch: (s: string) => void;
  refresh: () => void;
  notify: (s: string, tone?: string) => void;
  onDelete: (n: NodeInfo) => void;
  demo: boolean;
  canAdminister: boolean;
  onRolloutHistory?: () => void;
  onAddNode?: () => void;
  onRolloutAgent?: () => void;
};
export type OverviewPageProps = {
  overview: Overview | null;
  services: Service[];
  nodes: NodeInfo[];
  servicesRead: ReadState;
  nodesRead: ReadState;
  npuResources: NpuResourcesState;
  demo: boolean;
  canOperate: boolean;
  canAdminister: boolean;
  refreshing: boolean;
  onRefresh: () => void;
  onNavigate: (page: ConsolePage) => void;
  onCreateService: () => void;
  onOpenService: (service: Service) => void;
};
export type ModalProps = {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  onClose: () => void;
  footer?: React.ReactNode | ((dismiss: () => void) => React.ReactNode);
  wide?: boolean;
  size?: "small" | "standard";
  closeDisabled?: boolean;
  contentKey?: string;
  layout?: "dialog" | "drawer";
  headerIcon?: React.ReactNode;
};
export type ButtonProps = {
  children: React.ReactNode;
  onClick?: React.MouseEventHandler<HTMLButtonElement>;
  kind?: "primary" | "default" | "danger" | "text";
  disabled?: boolean;
  loading?: boolean;
  className?: string;
  type?: "button" | "submit";
  title?: string;
  size?: "small" | "medium" | "large";
  actionId?: string;
  actionContext?: Record<string, unknown>;
};
export type UiNotificationsProps = { messages: { id: number; text: string }[]; dismiss(id: number): void };
export type UiIconProps = React.SVGProps<SVGSVGElement> & React.RefAttributes<SVGSVGElement> & { size?: number | string; "data-icon"?: string; "data-icon-theme"?: string };
export type UiIcons = { render(id: string, props: UiIconProps, Default: React.ComponentType<UiIconProps>): React.ReactNode };
export type BuiltinPageProps = { page: ConsolePage; catalog: PluginCatalog | null; children?: React.ReactNode };
export type BuiltinUiContracts = {
 'ui.actions': UiActions; 'ui.i18n': UiI18n; 'ui.element': UiElements; 'ui.icons': UiIcons;
 'ui.button': React.ComponentType<UiButtonProps & React.RefAttributes<HTMLButtonElement>>;
 'ui.shell': React.ComponentType<UiComponentProps>; 'ui.appearance': React.ComponentType<UiComponentProps>;
 'layout.sidebar': React.ComponentType<UiComponentProps<UiRegionRenderProps>>; 'layout.topbar': React.ComponentType<UiComponentProps<UiRegionRenderProps>>;
 'layout.workspace': React.ComponentType<UiComponentProps<UiRegionRenderProps>>; 'layout.panel': React.ComponentType<UiComponentProps<UiRegionRenderProps>>;
 'ui.theme': React.ComponentType<UiComponentProps>; 'ui.notifications': React.ComponentType<UiNotificationsProps>;
 'component.ServiceEditor': React.ComponentType<UiComponentProps<ServiceEditorProps>>;
 'component.ServiceDetail': React.ComponentType<UiComponentProps<ServiceDetailProps>>;
 'page.login': React.ComponentType<UiComponentProps<LoginPageProps>>;
 'component.NodesPage': React.ComponentType<UiComponentProps<NodesPageProps>>;
 'component.OverviewPage': React.ComponentType<UiComponentProps<OverviewPageProps>>;
 'component.Modal': React.ComponentType<UiComponentProps<ModalProps>>;
 'component.Drawer': React.ComponentType<UiComponentProps<Omit<ModalProps,'layout'>>>;
 'component.Button': React.ComponentType<UiComponentProps<ButtonProps>>;
 'component.Badge': React.ComponentType<UiComponentProps<BadgeProps>>;
 'component.Empty': React.ComponentType<UiComponentProps<EmptyProps>>;
 'component.Spinner': React.ComponentType<UiComponentProps<SpinnerProps>>;
 'component.Field': React.ComponentType<UiComponentProps<FieldProps>>;
 'component.SearchInput': React.ComponentType<UiComponentProps<SearchInputProps>>;
 'component.ErrorBox': React.ComponentType<UiComponentProps<ErrorBoxProps>>;
 'component.InfoBox': React.ComponentType<UiComponentProps<InfoBoxProps>>;
 'component.Copy': React.ComponentType<UiComponentProps<CopyProps>>;
 'component.Confirm': React.ComponentType<UiComponentProps<ConfirmProps>>;
 'component.Segment': React.ComponentType<UiComponentProps<SegmentProps>>;
 'component.Select': React.ComponentType<UiComponentProps<SelectProps>>;
 'component.StatBar': React.ComponentType<UiComponentProps<StatBarProps>>;
 'component.JsonEditor': React.ComponentType<UiComponentProps<JsonEditorProps>>;
} & { [K in `page.${ConsolePage}`]: React.ComponentType<UiComponentProps<BuiltinPageProps>> };
export type BadgeProps = {
  status: string;
  children?: React.ReactNode;
};
export type EmptyProps = {
  title?: string;
  description?: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
};
export type SpinnerProps = { label?: string };
export type FieldProps = {
  label: string;
  children: React.ReactNode;
  hint?: string;
  required?: boolean;
  layout?: "vertical" | "horizontal";
};
export type SearchInputProps = {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  ariaLabel?: string;
};
export type ErrorBoxProps = { message: string };
export type InfoBoxProps = { children: React.ReactNode };
export type CopyProps = {
  value: string;
  label?: string;
};
export type ConfirmProps = {
  title: string;
  description: React.ReactNode;
  confirmText?: string;
  onConfirm: () => void;
  onClose: () => void;
  danger?: boolean;
  busy?: boolean;
  confirmDisabled?: boolean;
  error?: string;
};
export type SegmentProps = {
  options: { value: string; label: string; icon?: React.ReactNode }[];
  value: string;
  onChange: (value: string) => void;
};
export type SelectProps = {
  value: string;
  onChange: (value: string) => void;
  children: React.ReactNode;
  ariaLabel?: string;
  disabled?: boolean;
};
export type StatBarProps = {
  value: number | null | undefined;
  kind?: string;
};
export type JsonEditorProps = {
  value: string;
  onChange: (value: string) => void;
  label?: string;
  rows?: number;
  disabled?: boolean;
};
