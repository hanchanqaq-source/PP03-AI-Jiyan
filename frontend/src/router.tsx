import { createBrowserRouter, Navigate, type RouteObject } from "react-router-dom";
import { Layout } from "@/components/layout/Layout";
import { DailyReview } from "@/pages/DailyReview";
import { Intel } from "@/pages/Intel";
import { Sectors } from "@/pages/Sectors";
import { SectorDetail } from "@/pages/SectorDetail";
import { Debate } from "@/pages/Debate";
import { Portfolio } from "@/pages/Portfolio";
import { StockData } from "@/pages/StockData";
import { Watchlist } from "@/pages/Watchlist";
import { MyReports } from "@/pages/MyReports";
import { Notes } from "@/pages/Notes";
import { Settings } from "@/pages/Settings";
import { ResearchHome } from "@/pages/ResearchHome";
import { MarketNews } from "@/pages/MarketNews";
import { IndustryResearch } from "@/pages/IndustryResearch";
import { PortfolioAnalysis } from "@/pages/PortfolioAnalysis";
import { EvidenceCenter } from "@/features/evidence-center/EvidenceCenterReal";

export const APP_ROUTES: RouteObject[] = [
  {
    element: <Layout />,
    children: [
      { path: "/", element: <Navigate to="/research-home" replace /> },
      { path: "/research-home", element: <ResearchHome /> },
      { path: "/market-news", element: <MarketNews /> },
      { path: "/industry-research", element: <IndustryResearch /> },
      { path: "/portfolio-analysis", element: <PortfolioAnalysis /> },
      { path: "/evidence-center", element: <EvidenceCenter /> },
      { path: "/daily-review", element: <DailyReview /> },
      { path: "/intel", element: <Intel /> },
      { path: "/sectors", element: <Sectors /> },
      { path: "/sectors/:key", element: <SectorDetail /> },
      { path: "/portfolio", element: <Portfolio /> },
      { path: "/stock-data", element: <StockData /> },
      { path: "/debate", element: <Debate /> },
      { path: "/watchlist", element: <Watchlist /> },
      { path: "/my-reports", element: <MyReports /> },
      { path: "/notes", element: <Notes /> },
      { path: "/settings", element: <Settings /> },
    ],
  },
];

export const router = createBrowserRouter(APP_ROUTES);
