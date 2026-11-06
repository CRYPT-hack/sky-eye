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
  /** Saves the token and starts a new auth epoch, even when the value is unchanged. */
  setToken(t: string | null): void;
  /**
   * Goes up by one on every setToken call. Data effects and the live connection
   * depend on it, so saving a token (even the same one again) always refetches and
   * reconnects, and a page that is still refused asks again.
   */
  authEpoch: number;
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
  const [authEpoch, setAuthEpoch] = useState(0);
  // The client reads the token through a ref so it always sees the current one.
  const tokenRef = useRef(token);
  tokenRef.current = token;

  const [ownClient] = useState<ApiClient>(() => createClient({ getToken: () => tokenRef.current }));

  const setToken = useCallback((t: string | null) => {
    saveToken(t);
    tokenRef.current = t;
    setTokenState(t);
    setAuthEpoch((n) => n + 1);
    setUnauthorized(false);
  }, []);
  const reportUnauthorized = useCallback(() => setUnauthorized(true), []);

  const value = useMemo<ApiContextValue>(
    () => ({
      client: client ?? ownClient,
      token,
      setToken,
      authEpoch,
      unauthorized,
      reportUnauthorized,
    }),
    [client, ownClient, token, setToken, authEpoch, unauthorized, reportUnauthorized],
  );

  return <ApiContext.Provider value={value}>{children}</ApiContext.Provider>;
}

export function useApi(): ApiContextValue {
  const ctx = useContext(ApiContext);
  if (!ctx) throw new Error("useApi must be used inside <ApiProvider>");
  return ctx;
}
