import { useState, type ReactNode } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { cn } from "./cn";

export interface Column<T> {
  key: string;
  header: string;
  accessor: (row: T) => ReactNode;
  sortValue?: (row: T) => number | string;
  align?: "left" | "right" | "center";
  sortable?: boolean;
  className?: string;
}

interface Props<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  dense?: boolean;
  onRowClick?: (row: T) => void;
  emptyMessage?: string;
}

export function Table<T>({ columns, rows, rowKey, dense, onRowClick, emptyMessage }: Props<T>) {
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const sorted = [...rows];
  if (sortKey) {
    const col = columns.find((c) => c.key === sortKey);
    if (col?.sortValue) {
      sorted.sort((a, b) => {
        const va = col.sortValue!(a);
        const vb = col.sortValue!(b);
        const cmp = va > vb ? 1 : va < vb ? -1 : 0;
        return sortDir === "asc" ? cmp : -cmp;
      });
    }
  }

  const toggleSort = (key: string) => {
    if (sortKey === key) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else {
      setSortKey(key);
      setSortDir("desc");
    }
  };

  const rowH = dense ? "h-9" : "h-12";

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-bgSecondary sticky top-0">
          <tr>
            {columns.map((c) => {
              const isSorted = sortKey === c.key;
              const clickable = c.sortable !== false && !!c.sortValue;
              return (
                <th
                  key={c.key}
                  onClick={clickable ? () => toggleSort(c.key) : undefined}
                  className={cn(
                    "px-3 py-2 text-xs font-semibold uppercase tracking-wide text-textMuted sm:px-4",
                    c.align === "right" ? "text-right" : c.align === "center" ? "text-center" : "text-left",
                    clickable && "cursor-pointer select-none hover:text-textSecondary",
                    c.className,
                  )}
                >
                  <span className="inline-flex items-center gap-1">
                    {c.header}
                    {isSorted ? (
                      sortDir === "asc" ? <ChevronUp size={12} /> : <ChevronDown size={12} />
                    ) : null}
                  </span>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="py-10 text-center text-textMuted">
                {emptyMessage ?? "No data"}
              </td>
            </tr>
          ) : (
            sorted.map((row) => (
              <tr
                key={rowKey(row)}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                className={cn(
                  "bg-bgCard border-b border-borderSubtle transition-colors",
                  rowH,
                  onRowClick && "cursor-pointer hover:bg-bgHover",
                )}
              >
                {columns.map((c) => (
                  <td
                    key={c.key}
                    className={cn(
                      "px-3 sm:px-4",
                      c.align === "right" ? "text-right" : c.align === "center" ? "text-center" : "text-left",
                      c.className,
                    )}
                  >
                    {c.accessor(row)}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
