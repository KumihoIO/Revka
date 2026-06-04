import { createContext, useContext, useMemo, useState, type ReactNode } from 'react';

interface AssistantContextValue {
  open: boolean;
  pageContextOverride?: string;
  titleOverride?: string;
  placeholderOverride?: string;
  openAssistant: (options?: { pageContext?: string; title?: string; placeholder?: string }) => void;
  closeAssistant: () => void;
  toggleAssistant: () => void;
}

const AssistantContext = createContext<AssistantContextValue>({
  open: false,
  openAssistant: () => {},
  closeAssistant: () => {},
  toggleAssistant: () => {},
});

export function useV2Assistant() {
  return useContext(AssistantContext);
}

export function V2AssistantProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [pageContextOverride, setPageContextOverride] = useState<string | undefined>();
  const [titleOverride, setTitleOverride] = useState<string | undefined>();
  const [placeholderOverride, setPlaceholderOverride] = useState<string | undefined>();

  const value = useMemo<AssistantContextValue>(() => ({
    open,
    pageContextOverride,
    titleOverride,
    placeholderOverride,
    openAssistant: (options) => {
      setPageContextOverride(options?.pageContext);
      setTitleOverride(options?.title);
      setPlaceholderOverride(options?.placeholder);
      setOpen(true);
    },
    closeAssistant: () => {
      setOpen(false);
      setPageContextOverride(undefined);
      setTitleOverride(undefined);
      setPlaceholderOverride(undefined);
    },
    toggleAssistant: () => {
      if (open) {
        setOpen(false);
        setPageContextOverride(undefined);
        setTitleOverride(undefined);
        setPlaceholderOverride(undefined);
      } else {
        setOpen(true);
      }
    },
  }), [open, pageContextOverride, placeholderOverride, titleOverride]);

  return <AssistantContext.Provider value={value}>{children}</AssistantContext.Provider>;
}
