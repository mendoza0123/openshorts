// Self-host server password (OPENSHORTS_API_TOKEN on the server, see
// app.APITokenGate). Every request to the API host carries it as
// X-OpenShorts-Token; requests to any other host never do. Installed once in
// main.jsx by wrapping window.fetch, because components call fetch() directly
// in a dozen places and a missed one would fail with a bare 401.
import { API_BASE_URL } from '../config';

export const SERVER_TOKEN_KEY = 'os_server_token';
// Fired when the server answers 401 with X-OpenShorts-Auth: required.
export const SERVER_PASSWORD_REQUIRED = 'openshorts:server-password-required';

export function getServerToken() {
  try { return localStorage.getItem(SERVER_TOKEN_KEY) || ''; } catch { return ''; }
}

export function setServerToken(token) {
  try {
    if (token) localStorage.setItem(SERVER_TOKEN_KEY, token);
    else localStorage.removeItem(SERVER_TOKEN_KEY);
  } catch { /* storage blocked: the password then lasts for this page only */ }
}

function isApiRequest(url) {
  let target;
  try { target = new URL(url, window.location.href); } catch { return false; }
  const apiOrigin = API_BASE_URL
    ? new URL(API_BASE_URL, window.location.href).origin
    : window.location.origin;
  if (target.origin !== apiOrigin) return false;
  return target.pathname.startsWith('/api/') || target.pathname === '/mcp';
}

export function installServerAuth() {
  // Once per page: the app and the free tool pages both call this.
  if (window.__openshortsServerAuth) return;
  window.__openshortsServerAuth = true;
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const url = input instanceof Request ? input.url : String(input);
    const token = getServerToken();
    let options = init;
    if (token && isApiRequest(url)) {
      const headers = new Headers(init?.headers || (input instanceof Request ? input.headers : undefined));
      try {
        if (!headers.has('X-OpenShorts-Token')) headers.set('X-OpenShorts-Token', token);
        options = { ...init, headers };
      } catch { /* a stored value no header can carry: send without it, the 401 asks again */ }
    }
    const res = await nativeFetch(input, options);
    if (res.status === 401 && res.headers.get('X-OpenShorts-Auth') === 'required') {
      window.dispatchEvent(new CustomEvent(SERVER_PASSWORD_REQUIRED));
    }
    return res;
  };
}
