/** Optional runtime documentation only; never fall back to upstream URLs. */
export function documentationUrl(value: unknown): string {
  if (
    typeof value !== "string" ||
    !value ||
    value.length > 4096 ||
    /[\s\\\u0000-\u001f\u007f-\u009f]/.test(value)
  ) return "";
  const authority = /^https?:\/\/([^/?#]+)/i.exec(value)?.[1];
  if (!authority || authority.includes("@") || authority.includes("%")) return "";
  const parts = authority.startsWith("[")
    ? /^\[[0-9a-f:.]+\](?::([0-9]{1,5}))?$/i.exec(authority)
    : /^[^:]+(?::([0-9]{1,5}))?$/.exec(authority);
  if (!parts || (parts[1] !== undefined && (Number(parts[1]) < 1 || Number(parts[1]) > 65535))) return "";
  try {
    const parsed = new URL(value);
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password || !parsed.hostname) return "";
    if (!parsed.hostname.startsWith("[")) {
      const domain = parsed.hostname.replace(/\.$/, "");
      if (!domain || domain.length > 253 || domain.split(".").some((label) => !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(label))) return "";
      const literalHost = authority.split(":", 1)[0].replace(/\.$/, "");
      if (/^[0-9.]+$/.test(literalHost) && literalHost !== domain) return "";
    }
    return value;
  } catch {
    return "";
  }
}
