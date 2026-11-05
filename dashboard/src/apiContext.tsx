import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createClient, type ApiClient } from "./api/client";
import { loadToken, saveToken } from "./api/token";

export interface ApiContextValue {
  client: ApiClient;
  token: string | null;
  setToken(t: string | null): void;
  unauthorized: boolean;
  reportUnauthorized(): void;
}

const ApiContext = createContext<ApiContextValue | null>(null);

interface ApiProviderProps {
  children: ReactNode;
  /** Tests inject a fake client. */
  client?: ApiClient;
  initialToken?: string | null;
}

export function ApiProvider({ children, client, initialToken }: ApiProviderProps) {
  const [token, setTokenState] = useState<string | null>(() =>
    initialToken !== undefined ? initialToken : loadToken(),
  );
  const [unauthorized, setUnauthorized] = useState(false);
  // The client reads the token through a ref so it always sees the current one.
  const tokenRef = useRef(token);
  tokenRef.current = token;

  const [ownClient] = useState<ApiClient>(() => createClient({ getToken: () => tokenRef.current }));

  const setToken = useCallback((t: string | null) => {
    saveToken(t);
    tokenRef.current = t;
    setTokenState(t);
    setUnauthorized(false);
  }, []);
  const reportUnauthorized = useCallback(() => setUnauthorized(true), []);

  const value = useMemo<ApiContextValue>(
    () => ({
      client: client ?? ownClient,
      token,
      setToken,
      unauthorized,
      reportUnauthorized,
    }),
    [client, ownClient, token, setToken, unauthorized, reportUnauthorized],
  );

  return <ApiContext.Provider value={value}>{children}</ApiContext.Provider>;
}

export function useApi(): ApiContextValue {
  const ctx = useContext(ApiContext);
  if (!ctx) throw new Error("useApi must be used inside <ApiProvider>");
  return ctx;
}
