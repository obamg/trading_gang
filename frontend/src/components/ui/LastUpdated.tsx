import { useEffect, useState } from "react";

function formatAgo(date: Date): string {
  const sec = Math.floor((Date.now() - date.getTime()) / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hrs = Math.floor(min / 60);
  if (hrs < 24) return `${hrs}h ${min % 60}m ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export function LastUpdated({ date, label = "Last alert" }: { date: Date | null; label?: string }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 10_000);
    return () => clearInterval(id);
  }, []);

  if (!date) return null;

  const ageSec = (Date.now() - date.getTime()) / 1000;
  const color = ageSec > 3600 ? "text-loss" : ageSec > 600 ? "text-warning" : "text-textMuted";

  return (
    <span className={`text-xs ${color}`} title={date.toLocaleString()}>
      {label}: {formatAgo(date)}
    </span>
  );
}
