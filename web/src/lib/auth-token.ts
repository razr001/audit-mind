const ACCESS_TOKEN_KEY = 'auditmind-access-token'

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_TOKEN_KEY)
}

export function setAccessToken(token: string | null): void {
  if (token) localStorage.setItem(ACCESS_TOKEN_KEY, token)
  else localStorage.removeItem(ACCESS_TOKEN_KEY)
}
