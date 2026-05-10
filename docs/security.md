# Yoink security model

## Threat model

Yoink runs a local HTTP server on `127.0.0.1:5179` so the browser extension can ask it to download YouTube videos and write files to your disk. That arrangement -- a privileged local process taking commands from a browser -- has two failure modes worth defending against:

| Threat | Vector | v1 mitigation |
|---|---|---|
| **CSRF.** A malicious webpage you visit makes a request to `127.0.0.1:5179/extract` to trigger a yoink, list your sessions, or open a file in your home directory. | Cross-origin `fetch()` from the attacker's site to localhost. | Token-gated endpoints (see below). The browser refuses to let the attacker set `X-Yoink-Token` to the right value, and refuses to send the cookie-style credentials we don't use anyway. |
| **Local malware.** Other software running on your machine reads `token.txt` and uses the token to drive Yoink. | Filesystem access. | Out of scope. The local malware already runs with your user privileges; the token doesn't add or remove power. |

What we are **not** trying to defend against:

- A *different* extension you've installed reading the token via its own `chrome-extension://` origin. v2 will pin the published Chrome Web Store extension ID and reject other origins; v1 trusts every extension equally.
- Network-level attackers. The server only binds to `127.0.0.1` and never serves on a public interface; the attacker would already need to be on your machine.
- Compromised dependencies. We pin yt-dlp / Python / ffmpeg versions and lock SHA256 hashes for the directly-downloaded artifacts (see `docs/build-installer.md`); a compromised PyPI release of yt-dlp is still possible, and is the same risk every Python tool faces.

## Authentication mechanism

On first server startup, `server.py` generates a random token via `secrets.token_urlsafe(32)` and persists it to `%LOCALAPPDATA%\Yoink\token.txt` (or, in dev mode, to `<repo>/token.txt` -- gitignored). The token is loaded into memory on every subsequent startup.

Endpoints fall into three groups:

1. **Public probes** -- no auth, no Origin check. `/health` and `/ping` return `{"ok": true, "version": "..."}`. The popup, in-page YouTube button, and `setup.html` use these for the live status indicator. If a malicious site probes them they learn the server is running, which is not a meaningful secret.

2. **Token issuance** -- `/token` returns the token. Gated by three checks layered together:

   - **`X-Yoink-Client: yoink-extension` header.** A webpage cannot set custom request headers cross-origin without triggering a CORS preflight, and the preflight only echoes `Access-Control-Allow-Origin` for our allowlist (extensions + youtube.com). A malicious site's preflight gets no ACAO match, so the browser blocks the actual GET before it ever runs. This is the load-bearing CSRF defense.
   - **Origin allowlist** -- if `Origin` is present, it must start with `chrome-extension://` or `moz-extension://`. *No* `Origin` header is also accepted: some Chromium forks (Comet, observed in v1 testing) issue same-process service-worker fetches with no `Origin` at all, and the strict allowlist locks them out for no security gain (the `X-Yoink-Client` gate above is what stops the real attacker).
   - **Rate limit** -- 10 requests / 60 seconds, server-wide. The legitimate caller (the extension) fetches `/token` once on install plus the rare 403-retry, so this is loose for normal use and tight for an attacker grinding through tokens. Excess requests get 429.

   The historical strict-Origin-only gate was tightened against a threat (CSRF from a webpage) that the browser's CORS preflight already blocks via the `X-Yoink-Client` mechanism above, while breaking real users on Chromium forks that ship slightly different SW behavior. The relaxed gate is more permissive on paper but functionally equivalent against the actual attack model.

3. **Everything else** -- `/extract`, `/recent`, `/session/*`, `/open-prompts`, `/open-index`, `/open-folder`. All require the `X-Yoink-Token` header (or `?token=...` query param). Mismatch -> 403 with no further info. We use `secrets.compare_digest` for the comparison so a timing attack can't recover the token byte-by-byte.

The extension fetches `/token` lazily on the first authed request, caches the value in `chrome.storage.local`, and includes it in every subsequent request. If the server regenerates the token (uninstall/reinstall), the next request returns 403 -- the extension catches that, refreshes from `/token`, and retries once.

## CORS

Endpoints respond with:

```
Access-Control-Allow-Origin: <echoed origin if in allowlist>
Access-Control-Allow-Methods: GET, POST, OPTIONS
Access-Control-Allow-Headers: Content-Type, X-Yoink-Token, X-Yoink-Client
Access-Control-Allow-Private-Network: true
```

The allowlist accepts `https://www.youtube.com`, `https://m.youtube.com`, `https://youtube.com`, and any `chrome-extension://` origin. Everything else gets no CORS headers; the browser then refuses to expose the response to the calling page.

`Access-Control-Allow-Private-Network: true` is required by Chrome's Private Network Access spec when a public-internet origin (youtube.com) preflights a request to a loopback resource. Without it the preflight is rejected.

## POST hardening

Per P1-3 of the v1 review:

- `Content-Type: application/json` is required. Anything else -> `415 Unsupported Media Type`.
- `Content-Length` is capped at 64 KB. Larger -> `413 Payload Too Large`. (Real Yoink requests are sub-1 KB; the cap exists to bound an attacker's memory pressure if they somehow get past the auth gate.)
- The parsed JSON must be a top-level object. Arrays, strings, numbers -> `400 Bad Request`. Without this, downstream `body.get(...)` calls would raise `AttributeError` and leak stack traces.

## Input validation

Per P2.4:

- YouTube URLs are parsed with `urllib.parse.urlparse`, the hostname is checked against an explicit allowlist (`youtube.com`, `www.youtube.com`, `m.youtube.com`, `youtu.be`), the video ID is matched against `^[A-Za-z0-9_-]{6,}$`, and the canonicalized `https://www.youtube.com/watch?v=<id>` form is what gets handed to yt-dlp.
- Session IDs must match `^[A-Za-z0-9_-]{1,64}$`. They become path segments under `SESSIONS_ROOT` -- a `..` or absolute path would let a caller traverse the filesystem.
- `slugify()` produces ASCII-only output with a Windows-reserved-name guard (`CON`, `AUX`, `LPT1`, etc. get an underscore prefix).

## Reporting

If you find a vulnerability, please open a private GitHub Security Advisory or email the maintainer directly. Don't open a public issue with reproduction details until a fix is shipped.
