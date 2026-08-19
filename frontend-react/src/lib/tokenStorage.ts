const ACCESS_KEY = 'ragchat.access_token'
const REFRESH_KEY = 'ragchat.refresh_token'
// Which Storage backend the current session's tokens live in — itself
// always in localStorage (a small, non-sensitive flag) so a page reload
// knows where to look before any token has been read yet.
const BACKEND_KEY = 'ragchat.token_backend'

function backend(): Storage {
  return localStorage.getItem(BACKEND_KEY) === 'session' ? sessionStorage : localStorage
}

export const tokenStorage = {
  getAccess: () => backend().getItem(ACCESS_KEY),
  getRefresh: () => backend().getItem(REFRESH_KEY),
  /** `remember: false` keeps tokens in sessionStorage — cleared when the tab
   * closes — instead of the default persistent localStorage. */
  set: (access: string, refresh: string, remember = true) => {
    localStorage.removeItem(ACCESS_KEY)
    localStorage.removeItem(REFRESH_KEY)
    sessionStorage.removeItem(ACCESS_KEY)
    sessionStorage.removeItem(REFRESH_KEY)
    localStorage.setItem(BACKEND_KEY, remember ? 'local' : 'session')
    backend().setItem(ACCESS_KEY, access)
    backend().setItem(REFRESH_KEY, refresh)
  },
  setAccess: (access: string) => {
    backend().setItem(ACCESS_KEY, access)
  },
  clear: () => {
    localStorage.removeItem(ACCESS_KEY)
    localStorage.removeItem(REFRESH_KEY)
    sessionStorage.removeItem(ACCESS_KEY)
    sessionStorage.removeItem(REFRESH_KEY)
    localStorage.removeItem(BACKEND_KEY)
  },
}
