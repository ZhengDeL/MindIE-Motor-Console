export function paginationItems(current: number, total: number): (number | "gap-before" | "gap-after")[] {
  if (total <= 7) return Array.from({ length: Math.max(1, total) }, (_, index) => index + 1);
  if (current <= 4) return [1, 2, 3, 4, 5, "gap-after", total];
  if (current >= total - 3) return [1, "gap-before", total - 4, total - 3, total - 2, total - 1, total];
  return [1, "gap-before", current - 1, current, current + 1, "gap-after", total];
}

export default function Pagination({ page, totalPages, onChange, ariaLabel = "服务列表分页", showSizeLabel = true }: {
  page: number; totalPages: number; onChange: (page: number) => void; ariaLabel?: string; showSizeLabel?: boolean;
}) {
  const total = Math.max(1, totalPages);
  return <nav className="pagination" aria-label={ariaLabel}>
    {showSizeLabel && <span>20 条 / 页</span>}
    <button type="button" aria-label="上一页" disabled={page <= 1} onClick={() => onChange(page - 1)}>‹</button>
    {paginationItems(page, total).map((item) => typeof item === "number"
      ? <button type="button" key={item} className={page === item ? "active" : ""} aria-label={`第 ${item} 页`}
          aria-current={page === item ? "page" : undefined} onClick={() => onChange(item)}>{item}</button>
      : <span className="pagination-gap" key={item} aria-hidden="true">…</span>)}
    <button type="button" aria-label="下一页" disabled={page >= total} onClick={() => onChange(page + 1)}>›</button>
  </nav>;
}
