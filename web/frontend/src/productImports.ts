import { PRODUCT_ID, TEMPLATE_FORMAT } from "./brand";

/** Native configuration fields, including extensions named format/product_id, stay opaque. */
export function isNativeMotorConfig(value: unknown): boolean {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const config = (value as Record<string, unknown>).motor_deploy_config;
  return !!config && typeof config === "object" && !Array.isArray(config);
}

/** Explicit platform exports stay separate; original native Motor JSON remains importable. */
export function assertProductImport(value: unknown): void {
  if (!value || typeof value !== "object" || Array.isArray(value)) return;
  if (isNativeMotorConfig(value)) return;
  const record = value as Record<string, unknown>;
  if (Object.hasOwn(record, "format") && record.format !== TEMPLATE_FORMAT)
    throw Error("文件属于其他产品或不受支持的导出格式；请使用本平台模板或原生 Motor 配置 JSON。");
  if (Object.hasOwn(record, "product_id") && record.product_id !== PRODUCT_ID)
    throw Error("文件的产品标识与当前平台不一致，未导入。");
}
