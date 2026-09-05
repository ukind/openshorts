// Auth + billing session state for cloud mode.
// - Reads /api/config to learn whether billing is enabled at all.
// - Handles the magic-link and Google OAuth redirect hashes.
// - Exposes the current user, plan and minute balance to the app.
// When billingEnabled is false the provider is inert and the app behaves as the
// classic BYOK dashboard.
import { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { getApiUrl } from '../config';
import { apiFetch, apiJson, getToken, setToken, clearToken } from '../lib/api';
import { track } from '../lib/panel';
import { report as reportAttribution } from '../lib/attribution';

const AuthContext = createContext(null);
// eslint-disable-next-line react-refresh/only-export-components
export const useAuth = () => useContext(AuthContext);

export function AuthProvider({ children }) {
  const [config, setConfig] = useState({ billingEnabled: false, googleAuthEnabled: false });
  const [me, setMe] = useState(null);           // /api/me payload, or null when signed out
  const [loading, setLoading] = useState(true);
  const [signingIn, setSigningIn] = useState(false);

  const refreshMe = useCallback(async () => {
    if (!getToken()) { setMe(null); return null; }
    try {
      const data = await apiJson('/api/me');
      setMe(data);
      return data;
    } catch (e) {
      // Stale/invalid token: drop it and fall back to anonymous BYOK.
      clearToken();
      setMe(null);
      return null;
    }
  }, []);

  // Handle auth redirect hashes: #/auth/verify?ml=... and #/auth/callback?token=...
  const handleAuthHash = useCallback(async () => {
    const hash = window.location.hash || '';
    const match = hash.match(/^#\/auth\/(verify|callback)\??(.*)$/);
    if (!match) return false;
    const [, kind, query] = match;
    const params = new URLSearchParams(query);

    setSigningIn(true);
    let destination = '#app';
    try {
      if (kind === 'callback') {
        const token = params.get('token');
        if (token) {
          setToken(token);
          // Scrub the token from the URL immediately (replaceState, no new
          // history entry) so the bearer token isn't left reachable via Back.
          try {
            window.history.replaceState(null, document.title,
              window.location.pathname + window.location.search);
          } catch (_) { /* ignore */ }
        }
      } else if (kind === 'verify') {
        const ml = params.get('ml');
        if (ml) {
          const data = await apiJson('/api/auth/magic-link/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token: ml }),
          });
          if (data.token) setToken(data.token);
        }
      }
      const signedInMe = await refreshMe();
      // This handler only runs on the auth redirect, so a resolved user here is
      // a fresh sign-in / sign-up — the top of the conversion funnel.
      if (signedInMe?.user) {
        track('Signup', { props: { method: kind === 'verify' ? 'magic_link' : 'google' } });
        // Server-side twin of the Signup event: the one place we learn which
        // channel produced an account. Awaited but never allowed to throw.
        await reportAttribution(apiJson);
      }
      // Everyone lands in the app. Show the welcome plan-choice popup once per
      // browser (on the first auth) for anyone not already on a paid plan —
      // free is the default, paid is one click away. Never a pricing-page dump.
      const paid = ['starter', 'creator', 'pro'].includes(signedInMe?.plan);
      let welcomed = false;
      try { welcomed = localStorage.getItem('os_welcomed') === '1'; } catch (_) { /* ignore */ }
      if (signedInMe?.user && !paid && !welcomed) {
        try {
          localStorage.setItem('os_show_plan_choice', '1');
          localStorage.setItem('os_welcomed', '1');
        } catch (_) { /* ignore */ }
      }
    } catch (e) {
      // fall through — user lands signed-out
    } finally {
      setSigningIn(false);
      // Clear the sensitive hash, land wherever we resolved above.
      window.location.hash = destination;
    }
    return true;
  }, [refreshMe]);

  // --- config fetch with retry (D11) — replaces the one-shot mount fetch -----------
  // A single bare fetch left config at its 2-key init forever on any transient
  // failure — llmConfigured would stay false on a fully env-configured server
  // and the banner would fire permanently. Three attempts, 500ms then 1s
  // backoff; the happy path is still one fetch, once per mount.
  const fetchConfig = useCallback(async () => {
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        const res = await fetch(getApiUrl('/api/config'));
        if (res.ok) return await res.json();
      } catch (_) { /* network error — retry */ }
      if (attempt < 2) await new Promise((r) => setTimeout(r, 500 * (attempt + 1)));
    }
    return null; // stay on the init defaults (BYOK, no LLM fields)
  }, []);

  useEffect(() => {
    (async () => {
      const cfg = await fetchConfig();
      if (cfg) {
        setConfig(cfg);
        if (cfg.billingEnabled) {
          const handled = await handleAuthHash();
          if (!handled) await refreshMe();
        }
      }
      setLoading(false);
    })();
  }, [fetchConfig, handleAuthHash, refreshMe]);

  const requestMagicLink = useCallback(async (email) => {
    const res = await apiFetch('/api/auth/magic-link', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    if (res.status === 429) throw new Error('Too many attempts. Try again in a few minutes.');
    if (!res.ok) throw new Error('Could not send sign-in link.');
    return true;
  }, []);

  const loginWithGoogle = useCallback(() => {
    window.location.href = getApiUrl('/api/auth/google');
  }, []);

  const logout = useCallback(() => {
    clearToken();
    setMe(null);
  }, []);

  // --- /api/config LLM fields (D3): consts before the `value` object --------------
  const llmConfigured = !!config.llmConfigured;
  const llmModel = config.llmModel || null;
  const llmBaseUrl = config.llmBaseUrl || null;

  const value = {
    billingEnabled: config.billingEnabled,
    googleAuthEnabled: config.googleAuthEnabled,
    jobRetentionSeconds: config.jobRetentionSeconds || null,
    llmConfigured,
    llmModel,
    llmBaseUrl,
    loading,
    signingIn,
    user: me?.user || null,
    me,
    plan: me?.plan || null,
    entitled: !!me?.entitled,
    minutes: me?.minutes || null,
    isSignedIn: !!me?.user,
    // Managed = signed-in AND entitled (active plan or top-up credit).
    isManaged: !!(config.billingEnabled && me?.entitled),
    refreshMe,
    requestMagicLink,
    loginWithGoogle,
    logout,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
