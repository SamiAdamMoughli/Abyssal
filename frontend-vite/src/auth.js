/**
 * JWT authentication — login modal and token storage.
 * Token is kept in sessionStorage (not localStorage) so it clears on tab close.
 */

const API = window.__ENV?.API_URL ?? "http://localhost:8000";
const TOKEN_KEY = "vesselx_token";

export function getToken() {
  return sessionStorage.getItem(TOKEN_KEY);
}

export function clearToken() {
  sessionStorage.removeItem(TOKEN_KEY);
}

export function isAuthenticated() {
  const t = getToken();
  if (!t) return false;
  try {
    const payload = JSON.parse(atob(t.split(".")[1]));
    return payload.exp * 1000 > Date.now();
  } catch {
    return false;
  }
}

// Attach Authorization header to all fetch calls when logged in
const _origFetch = window.fetch.bind(window);
window.fetch = (input, init = {}) => {
  const token = getToken();
  if (token && typeof input === "string" && input.startsWith(API)) {
    init.headers = { ...(init.headers ?? {}), Authorization: `Bearer ${token}` };
  }
  return _origFetch(input, init);
};

// ── Login modal ────────────────────────────────────────────────────────────

function _buildModal() {
  const el = document.createElement("div");
  el.id = "login-modal";
  el.className = "login-modal";
  el.innerHTML = `
    <div class="login-box">
      <div class="login-logo">☠ SPYHOP</div>
      <div class="login-sub">OPERATOR ACCESS</div>
      <form id="login-form" autocomplete="off">
        <input id="login-user" type="text" placeholder="USERNAME"
               autocomplete="username" spellcheck="false" required />
        <input id="login-pass" type="password" placeholder="PASSWORD"
               autocomplete="current-password" required />
        <div id="login-error" class="login-error"></div>
        <button type="submit">AUTHENTICATE</button>
      </form>
      <div class="login-skip">
        <button id="login-skip-btn" type="button">CONTINUE WITHOUT AUTH</button>
      </div>
    </div>
  `;
  document.body.appendChild(el);
  return el;
}

export function showLoginModal() {
  const modal = document.getElementById("login-modal") ?? _buildModal();
  modal.style.display = "flex";

  modal.querySelector("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errEl = modal.querySelector("#login-error");
    errEl.textContent = "";
    const user = modal.querySelector("#login-user").value.trim();
    const pass = modal.querySelector("#login-pass").value;

    try {
      const body = new URLSearchParams({ username: user, password: pass });
      const res = await _origFetch(`${API}/auth/token`, {
        method: "POST",
        body,
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        errEl.textContent = data.detail ?? "Authentication failed";
        return;
      }
      const { access_token } = await res.json();
      sessionStorage.setItem(TOKEN_KEY, access_token);
      modal.style.display = "none";
      _updateAuthButton(user);
    } catch {
      errEl.textContent = "Network error — check connection";
    }
  });

  modal.querySelector("#login-skip-btn").addEventListener("click", () => {
    modal.style.display = "none";
  });
}

function _updateAuthButton(username) {
  const btn = document.getElementById("auth-btn");
  if (btn) {
    btn.textContent = `⚿ ${username.toUpperCase()}`;
    btn.title = "Click to log out";
    btn.onclick = () => {
      clearToken();
      btn.textContent = "⚿ LOGIN";
      btn.title = "Authenticate as operator";
      btn.onclick = () => showLoginModal();
    };
  }
}

export function initAuth() {
  const btn = document.getElementById("auth-btn");
  if (btn) {
    btn.addEventListener("click", () => showLoginModal());
  }
}
