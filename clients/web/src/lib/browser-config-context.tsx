"use client";

import { createContext, useContext, type ReactNode } from "react";
import type { BrowserConfig } from "./browser-config";

const BrowserConfigContext = createContext<BrowserConfig | null>(null);

export function BrowserConfigProvider({ config, children }: {
  config: BrowserConfig;
  children: ReactNode;
}): ReactNode {
  return <BrowserConfigContext.Provider value={config}>{children}</BrowserConfigContext.Provider>;
}

export function useBrowserConfig(): BrowserConfig {
  const config = useContext(BrowserConfigContext);
  if (!config) throw new TypeError("Browser config requires BrowserConfigProvider");
  return config;
}
