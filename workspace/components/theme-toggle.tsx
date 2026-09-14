"use client";

import { useSyncExternalStore } from "react";
import { Moon, Sun } from "lucide-react";

export const THEME_STORAGE_KEY = "crawin-theme";

function subscribe(onChange: () => void) {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => observer.disconnect();
}

const isLightMode = () => document.documentElement.classList.contains("light");

export function ThemeToggle() {
  const light = useSyncExternalStore(subscribe, isLightMode, () => false);

  const toggle = () => {
    const next = !isLightMode();
    document.documentElement.classList.toggle("light", next);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next ? "light" : "dark");
    } catch { }
  };

  return (
    <button
      type="button"
      onClick={toggle}
      className="flex items-center justify-center h-9 w-9 bg-slate-900 border border-slate-800 rounded-xl text-slate-400 hover:text-white transition-colors"
      title={light ? "Switch to dark mode" : "Switch to light mode"}
      aria-label={light ? "Switch to dark mode" : "Switch to light mode"}
    >
      {light ? <Moon className="h-4 w-4" /> : <Sun className="h-4 w-4" />}
    </button>
  );
}
