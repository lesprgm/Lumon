import App from "./App";
import LandingPage from "./landing/LandingPage";

const LANDING_ROOT_ENABLED = import.meta.env.VITE_LANDING_ROOT === "1";

export function resolveRootView(pathname: string, landingRootEnabled = LANDING_ROOT_ENABLED): "landing" | "app" {
  const normalized = pathname === "/" ? "/" : pathname.replace(/\/+$/, "");
  if (normalized === "/landing") {
    return "landing";
  }
  if (landingRootEnabled && normalized === "/") {
    return "landing";
  }
  return "app";
}

export function RootApp() {
  return resolveRootView(window.location.pathname) === "landing" ? <LandingPage /> : <App />;
}

export default RootApp;
