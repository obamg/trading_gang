import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { MetricCard } from "@/components/ui/MetricCard";
import { Skeleton } from "@/components/ui/Skeleton";
import { LiveIndicator } from "@/components/ui/LiveIndicator";
import { LastUpdated } from "@/components/ui/LastUpdated";
import { newsApi, type NewsArticle } from "@/api/modules";

type SentimentFilter = "all" | "bullish" | "bearish" | "neutral";
type ImportanceFilter = "all" | "high";

const SENTIMENT_BADGE: Record<string, { variant: "bullish" | "bearish" | "neutral"; label: string }> = {
  bullish: { variant: "bullish", label: "BULLISH" },
  bearish: { variant: "bearish", label: "BEARISH" },
  neutral: { variant: "neutral", label: "NEUTRAL" },
};

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ${mins % 60}m ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

export default function NewsPulsePage() {
  const [sentimentFilter, setSentimentFilter] = useState<SentimentFilter>("all");
  const [importanceFilter, setImportanceFilter] = useState<ImportanceFilter>("all");
  const [coinFilter, setCoinFilter] = useState("");

  const { data: articles, isLoading } = useQuery({
    queryKey: ["news", "articles", sentimentFilter, importanceFilter, coinFilter],
    queryFn: () =>
      newsApi.articles({
        limit: 50,
        sentiment: sentimentFilter === "all" ? undefined : sentimentFilter,
        importance: importanceFilter === "all" ? undefined : importanceFilter,
        coin: coinFilter || undefined,
      }),
    refetchInterval: 60_000,
  });

  const { data: stats } = useQuery({
    queryKey: ["news", "stats"],
    queryFn: newsApi.stats,
    refetchInterval: 60_000,
  });

  const lastUpdated = useMemo(() => {
    const items = articles?.items ?? [];
    return items.length ? new Date(items[0].published_at) : null;
  }, [articles]);

  const items = articles?.items ?? [];

  return (
    <div className="flex flex-col gap-4 md:gap-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold md:text-2xl">NewsPulse</h1>
          <p className="text-sm text-textSecondary">Market-moving crypto news with sentiment analysis.</p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <LiveIndicator />
          <LastUpdated date={lastUpdated} label="Latest article" />
        </div>
      </header>

      <section className="grid grid-cols-2 gap-2 md:grid-cols-4 md:gap-3">
        <MetricCard label="Articles (24h)" value={stats?.articles_24h ?? null} valueDecimals={0} />
        <MetricCard label="Bullish" value={stats?.bullish_24h ?? null} valueDecimals={0} />
        <MetricCard label="Bearish" value={stats?.bearish_24h ?? null} valueDecimals={0} />
        <MetricCard label="High Impact" value={stats?.high_impact_24h ?? null} valueDecimals={0} />
      </section>

      <div className="flex flex-wrap items-center gap-2">
        <div className="flex gap-1">
          {(["all", "bullish", "bearish", "neutral"] as SentimentFilter[]).map((v) => (
            <button
              key={v}
              onClick={() => setSentimentFilter(v)}
              className={`rounded px-3 py-1 text-xs font-semibold capitalize transition ${
                sentimentFilter === v
                  ? "bg-primary-500 text-white"
                  : "bg-bgElevated text-textSecondary hover:text-textPrimary"
              }`}
            >
              {v}
            </button>
          ))}
        </div>
        <button
          onClick={() => setImportanceFilter(importanceFilter === "all" ? "high" : "all")}
          className={`rounded px-3 py-1 text-xs font-semibold transition ${
            importanceFilter === "high"
              ? "bg-warning-subtle text-warning"
              : "bg-bgElevated text-textSecondary hover:text-textPrimary"
          }`}
        >
          High Impact Only
        </button>
        <input
          type="text"
          placeholder="Filter by coin (BTC, ETH...)"
          value={coinFilter}
          onChange={(e) => setCoinFilter(e.target.value.toUpperCase())}
          className="rounded border border-borderSubtle bg-bgElevated px-3 py-1 text-xs text-textPrimary placeholder:text-textMuted outline-none focus:border-primary-500 w-48"
        />
      </div>

      <Card>
        <CardHeader>
          <h2 className="text-sm font-semibold">News Feed</h2>
        </CardHeader>
        <CardBody className="flex flex-col gap-2">
          {isLoading ? (
            Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-16" />)
          ) : items.length === 0 ? (
            <p className="text-sm text-textSecondary">No articles found.</p>
          ) : (
            items.map((article) => <ArticleRow key={article.id} article={article} />)
          )}
        </CardBody>
      </Card>
    </div>
  );
}

function ArticleRow({ article }: { article: NewsArticle }) {
  const sentimentMeta = SENTIMENT_BADGE[article.sentiment ?? "neutral"] ?? SENTIMENT_BADGE.neutral;

  return (
    <a
      href={article.url}
      target="_blank"
      rel="noopener noreferrer"
      className="flex flex-col gap-1.5 rounded-md border border-borderSubtle bg-bgElevated px-3 py-2.5 transition hover:border-primary-500/40 sm:flex-row sm:items-start sm:justify-between"
    >
      <div className="flex flex-1 flex-col gap-1">
        <span className="text-sm font-medium leading-snug text-textPrimary">{article.title}</span>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-textMuted">{article.source}</span>
          <Badge variant={sentimentMeta.variant}>{sentimentMeta.label}</Badge>
          {article.importance === "high" && <Badge variant="warning">HIGH IMPACT</Badge>}
          {article.coins.length > 0 &&
            article.coins.slice(0, 5).map((coin) => (
              <Badge key={coin} variant="new">{coin}</Badge>
            ))}
        </div>
      </div>
      <span className="shrink-0 text-xs text-textMuted sm:ml-3 sm:mt-0.5">
        {timeAgo(article.published_at)}
      </span>
    </a>
  );
}
