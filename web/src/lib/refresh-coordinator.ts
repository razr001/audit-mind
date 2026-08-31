export function createRefreshCoordinator(
  requestToken: () => Promise<string>,
  storeToken: (token: string) => void,
): () => Promise<string> {
  let pending: Promise<string> | null = null

  return () => {
    if (!pending) {
      pending = requestToken()
        .then((token) => {
          storeToken(token)
          return token
        })
        .finally(() => {
          pending = null
        })
    }
    return pending
  }
}
