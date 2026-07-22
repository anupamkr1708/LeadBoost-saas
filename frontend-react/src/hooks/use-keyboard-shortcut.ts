import { useEffect } from "react";

interface ShortcutOptions {
  key: string;
  metaOrCtrl?: boolean;
  onTrigger: () => void;
}

/** Registers a global keyboard shortcut, e.g. Cmd/Ctrl+K for the command palette. */
export function useKeyboardShortcut({ key, metaOrCtrl = false, onTrigger }: ShortcutOptions) {
  useEffect(() => {
    function handler(e: KeyboardEvent) {
      const modifierOk = metaOrCtrl ? e.metaKey || e.ctrlKey : true;
      if (modifierOk && e.key.toLowerCase() === key.toLowerCase()) {
        e.preventDefault();
        onTrigger();
      }
    }
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [key, metaOrCtrl, onTrigger]);
}
