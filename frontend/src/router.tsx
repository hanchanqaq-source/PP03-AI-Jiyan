import { createBrowserRouter, Navigate, type RouteObject } from "react-router-dom";
import { Layout } from "@/components/layout/Layout";
import { DailyReview } from "@/pages/DailyReview";
import { Intel } from "@/pages/Intel";
import { Signals } from "@/pages/Signals";
import { Sectors } from "@/pages/Sectors";
import { SectorDetail } from "@/pages/SectorDetail";
import { Debate } from "@/pages/Debate";
import { Holdings } from "@/pages/Holdings";
import { StockData } from "@/pages/StockData";
import { Watchlist } from "@/pages/Watchlist";
import { MyReports } from "@/pages/MyReports";
import { Notes } from "@/pages/Notes";
import { Settings } from "@/pages/Settings";

export const APP_ROUTES: RouteObject[] = [
  {
    element: <Layout />,
    children: [
      { path: "/", element: <Navigate to="/daily-review" replace /> },
      { path: "/daily-review", element: <DailyReview /> },
      { path: "/intel", element: <Navigate to="/intel/investment-news" replace /> },
      { path: "/intel/:tab", element: <Intel /> },
      { path: "/signals", element: <Signals /> },
      { path: "/signals/:tab", element: <Signals /> },
      { path: "/sectors", element: <Sectors /> },
      { path: "/sectors/:key", element: <SectorDetail /> },
      { path: "/portfolio", element: <Navigate to="/portfolio/funds" replace /> },
      { path: "/portfolio/funds", element: <Holdings /> },
      { path: "/portfolio/stocks", element: <Holdings /> },
      { path: "/portfolio-analysis", element: <Navigate to="/portfolio/funds" replace /> },
      { path: "/stock-data", element: <StockData /> },
      { path: "/debate", element: <Debate /> },
      { path: "/watchlist", element: <Watchlist /> },
      { path: "/my-reports", element: <MyReports /> },
      { path: "/notes", element: <Notes /> },
      { path: "/settings", element: <Settings /> },
      { path: "*", element: <Navigate to="/daily-review" replace /> },
    ],
  },
];

export const router = createBrowserRouter(APP_ROUTES);
