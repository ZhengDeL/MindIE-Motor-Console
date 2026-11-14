export type ReadState = { loaded: boolean; refreshing: boolean; error: string };

/** A successful empty response is distinct from a read that has not completed. */
export const readNotice = ({ loaded, refreshing, error }: ReadState): string => !loaded
  ? error ? "读取失败，请重试" : "正在读取"
  : error ? "刷新失败，显示上次结果" : refreshing ? "正在刷新，显示上次结果" : "";

export function readCount(state: ReadState, count: number, unit: string): string {
  if (!state.loaded) return readNotice(state);
  const notice = readNotice(state);
  return `${count.toLocaleString("zh-CN")} ${unit}${notice ? ` · ${notice}` : ""}`;
}
