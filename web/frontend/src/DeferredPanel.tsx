import { Component, Suspense } from "react";
import type { ReactNode } from "react";
import { Button, ErrorBox, Modal, Spinner } from "./ui";

type Props = { label: string; children: ReactNode; onClose?: () => void };

class ModuleBoundary extends Component<Props, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      const message = `${this.props.label}暂时无法显示，请刷新页面后重试。已提交的后台任务会继续执行，请从任务记录核对结果。`;
      const reload = <Button onClick={() => window.location.reload()}>刷新页面</Button>;
      if (this.props.onClose) {
        return <Modal title="模块加载失败" onClose={this.props.onClose} footer={reload}>
          <ErrorBox message={message} />
        </Modal>;
      }
      return (
        <div role="alert" className="deferred-panel-error">
          <ErrorBox message={message} />
          {reload}
        </div>
      );
    }
    return this.props.children;
  }
}

export default function DeferredPanel({ label, children, onClose }: Props) {
  return (
    <ModuleBoundary label={label} onClose={onClose}>
      <Suspense fallback={<Spinner label={`正在加载${label}`} />}>
        {children}
      </Suspense>
    </ModuleBoundary>
  );
}
