import { UiElement, withUiSlot, UiText } from "./uiPluginSdk";
import { useState } from "react";
import { Box, ShieldCheck, Trash2 } from "./icons";
import type { Service } from "./api";
import { Button, ErrorBox, Field, InfoBox, Modal } from "./ui";
import { useRuntimeAccess } from "./consolePlugins";

function DefaultUndeployDialog({
  service,
  onClose,
  onUndeploy,
}: {
  service: Service;
  onClose: () => void;
  onUndeploy: (service: Service, confirmationName: string) => Promise<void>;
}) {
  const runtimeAccess = useRuntimeAccess(service.runtime);
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title={`卸载部署 · ${service.name}`}
      subtitle="移除本服务受管部署资源，保留服务配置"
      onClose={onClose}
      footer={
        <>
          <Button actionId="action.UndeployDialog.001" onClick={onClose}><UiText>{"取消"}</UiText></Button>
          <Button actionId="action.UndeployDialog.002"
            kind="danger"
            disabled={!runtimeAccess.enabled || confirmation !== service.name}
            loading={busy}
            onClick={async () => {
              if (!runtimeAccess.enabled) { setError(runtimeAccess.reason); return; }
              setBusy(true);
              setError("");
              try {
                await onUndeploy(service, confirmation);
                onClose();
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            <Trash2 size={14} />
            <UiText>{"确认卸载部署"}</UiText>
          </Button>
        </>
      }
    >
      {error && <ErrorBox message={error} />}
      {!runtimeAccess.enabled && <InfoBox>{runtimeAccess.reason}<UiText>{"确认输入仍保留。"}</UiText></InfoBox>}
      <UiElement as="div" className="undeploy-summary">
        <UiElement as="div" className="confirm-symbol danger">
          <Trash2 size={24} />
        </UiElement>
        <UiElement as="div">
          <UiElement as="h3"><UiText>{"确认卸载当前 Kubernetes 部署？"}</UiText></UiElement>
          <UiElement as="p">
            <UiText>{"该操作会影响本服务的在线请求。卸载会移除受管部署资源，并等待活动工作负载退出。"}</UiText>
          </UiElement>
        </UiElement>
      </UiElement>
      <UiElement as="div" className="confirm-resource">
        <Box size={17} />
        <UiElement as="b">{service.name}</UiElement>
        <UiElement as="span" className="runtime-tag k8s"><UiText>{"Kubernetes"}</UiText></UiElement>
      </UiElement>
      <UiElement as="div" className="undeploy-retained">
        <ShieldCheck size={17} />
        <UiElement as="span">
          <UiText>{"保留 Namespace、PVC、模型权重与服务配置。完成后服务回到待部署状态，可按当前配置重新部署。"}</UiText>
        </UiElement>
      </UiElement>
      <Field
        label="输入服务名以确认"
        required
        hint="请精确输入上方服务名，包含大小写，不添加空格"
      >
        <UiElement as="input"
          aria-label="输入服务名以确认"
          value={confirmation}
          onChange={(event) => setConfirmation(event.target.value)}
          disabled={busy}
          placeholder={service.name}
          autoComplete="off"
          spellCheck={false}
        />
      </Field>
    </Modal>
  );
}

const UndeployDialog = withUiSlot("component.UndeployDialog", DefaultUndeployDialog);
export default UndeployDialog;

// Reference package implementation exports.
export { DefaultUndeployDialog };
