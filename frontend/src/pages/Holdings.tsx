import { Link, useLocation } from "react-router-dom";
import { PageHeader } from "@/components/ui/PageHeader";
import { cn } from "@/lib/utils";
import { Portfolio } from "@/pages/Portfolio";
import { PortfolioAnalysis } from "@/pages/PortfolioAnalysis";

const TABS = [
  { to: "/portfolio/funds", label: "基金持仓" },
  { to: "/portfolio/stocks", label: "股票持仓" },
] as const;

export function Holdings() {
  const { pathname } = useLocation();
  const stockActive = pathname === "/portfolio/stocks";

  return (
    <div>
      <PageHeader title="我的持仓" subtitle="基金与股票台账分别保存，互不覆盖" />
      <nav aria-label="持仓类型" className="mb-5 inline-flex rounded-xl border border-border bg-muted/20 p-1">
        {TABS.map((tab) => {
          const active = pathname === tab.to;
          return (
            <Link
              key={tab.to}
              to={tab.to}
              aria-current={active ? "page" : undefined}
              className={cn(
                "rounded-lg px-5 py-2 text-sm font-medium transition-colors",
                active ? "bg-primary text-primary-foreground shadow-glow" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {tab.label}
            </Link>
          );
        })}
      </nav>

      <section hidden={stockActive} aria-label="基金持仓面板">
        <PortfolioAnalysis embedded />
      </section>
      <section hidden={!stockActive} aria-label="股票持仓面板">
        <Portfolio embedded />
      </section>
    </div>
  );
}
