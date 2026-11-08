import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

interface TitleState {
  title: string;
  setTitle: (t: string) => void;
}

const Ctx = createContext<TitleState>({ title: "", setTitle: () => undefined });

export function TitleProvider({ children }: { children: ReactNode }) {
  const [title, setTitle] = useState("");
  return <Ctx.Provider value={{ title, setTitle }}>{children}</Ctx.Provider>;
}

export const usePageTitleValue = () => useContext(Ctx).title;

/** Each screen names itself; the top bar and the browser tab show it. */
export function usePageTitle(title: string) {
  const { setTitle } = useContext(Ctx);
  useEffect(() => {
    setTitle(title);
    document.title = `${title} · Byit Receiver`;
  }, [title, setTitle]);
}
