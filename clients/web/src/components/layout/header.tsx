"use client";

import { Bell } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { ThemeToggleSimple } from "@/components/theme-toggle-simple";

export function Header() {
  return (
    <header className="flex h-14 items-center justify-between border-b border-border/50 bg-background/80 px-6 backdrop-blur-sm">
      <div className="flex items-center gap-3">
        <h2 className="text-sm font-medium text-muted-foreground">
          Autonomous Red Team Platform
        </h2>
        <Separator orientation="vertical" className="h-4" />
        <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-xs font-medium text-emerald-800 dark:text-emerald-400">
          Online
        </span>
      </div>

      <div className="flex items-center gap-2">
        <ThemeToggleSimple />
        <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-foreground">
          <Bell className="h-4 w-4" />
        </Button>
        <span className="text-xs text-muted-foreground">Local Mode</span>
      </div>
    </header>
  );
}
