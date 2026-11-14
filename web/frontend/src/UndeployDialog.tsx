import { useState } from "react";
import { Box, ShieldCheck, Trash2 } from "./icons";
import type { Service } from "./api";
import { Button, ErrorBox, Field, InfoBox, Modal } from "./ui";
import { useRuntimeAccess } from "./consolePlugins";

export default function UndeployDialog({
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
          <Button onClick={onClose}>取消</Button>
          <Button
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
            确认卸载部署
          </Button>
        </>
      }
    >
      {error && <ErrorBox message={error} />}
      {!runtimeAccess.enabled && <InfoBox>{runtimeAccess.reason}确认输入仍保留。</InfoBox>}
      <div className="undeploy-summary">
        <div className="confirm-symbol danger">
          <Trash2 size={24} />
        </div>
        <div>
          <h3>确认卸载当前 Kubernetes 部署？</h3>
          <p>
            该操作会影响本服务的在线请求。卸载会移除受管部署资源，并等待活动工作负载退出。
          </p>
        </div>
      </div>
      <div className="confirm-resource">
        <Box size={17} />
        <b>{service.name}</b>
        <span className="runtime-tag k8s">Kubernetes</span>
      </div>
      <div className="undeploy-retained">
        <ShieldCheck size={17} />
        <span>
          保留
          Namespace、PVC、模型权重与服务配置。完成后服务回到待部署状态，可按当前配置重新部署。
        </span>
      </div>
      <Field
        label="输入服务名以确认"
        required
        hint="请精确输入上方服务名，包含大小写，不添加空格"
      >
        <input
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
