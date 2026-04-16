import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { AlertItem } from "@/components/ui/AlertItem";
import { useAlerts } from "@/hooks/useAlerts";
import { MODULES, MODULE_BY_KEY } from "@/components/layout/modules";
import type { ModuleKey } from "@/types/alerts";
import { Link } from "react-router-dom";

export default function Dashboard() {
  const alerts = useAlerts();

  return (
    <div className="flex flex-col gap-6 p-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold">Command center</h1>
        <p className="text-sm text-textSecondary">
          Live alerts across every module, in one place.
        </p>
      </header>

      <section className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {MODULES.map((m) => {
          const Icon = m.icon;
          return (
            <Link
              key={m.key}
              to={m.path}
              className="flex items-center gap-3 rounded-lg border border-borderSubtle bg-bgCard p-3 hover:bg-bgHover"
            >
              <span
                className="flex h-9 w-9 items-center justify-center rounded-md"
                style={{ backgroundColor: `${m.color}22`, color: m.color }}
              >
                <Icon size={18} />
              </span>
              <div className="flex flex-col">
                <span className="text-sm font-medium">{m.label}</span>
                <span className="text-xs text-textSecondary">Open</span>
              </div>
            </Link>
          );
        })}
      </section>

      <Card>
        <CardHeader>
          <h2 className="text-sm font-semibold">Live alerts</h2>
          <span className="text-xs text-textSecondary">{alerts.length} in buffer</span>
        </CardHeader>
        <CardBody className="flex flex-col gap-2">
          {alerts.length === 0 ? (
            <p className="text-sm text-textSecondary">
              Waiting for alerts… the feed appears here as modules fire.
            </p>
          ) : (
            alerts.slice(0, 10).map((a, i) => {
              const moduleKey = (a.data?.module as ModuleKey) ?? "radarx";
              const mod = MODULE_BY_KEY[moduleKey] ?? MODULE_BY_KEY.radarx;
              const symbol = (a.data?.symbol as string) ?? a.type;
              const ts = a.receivedAt ? new Date(a.receivedAt).toLocaleTimeString() : "";
              return (
                <AlertItem
                  key={i}
                  symbol={symbol}
                  moduleLabel={mod.label}
                  accentColor={mod.color}
                  timestamp={ts}
                  context={<span>{a.type}</span>}
                />
              );
            })
          )}
        </CardBody>
      </Card>
    </div>
  );
}
