const KEY = "skyeye.token";

// Storage can be missing or throw (private mode, blocked site data); never let that break the app.
export function loadToken(): string | null {
  try {
    return window.localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function saveToken(token: string | null): void {
  try {
    if (token === null) window.localStorage.removeItem(KEY);
    else window.localStorage.setItem(KEY, token);
  } catch {
    // ignore: the token just will not persist
  }
}
