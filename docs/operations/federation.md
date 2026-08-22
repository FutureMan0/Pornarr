# Federation: sharing a library with another household

Two people running their own Pornarr can see one combined library. Each side keeps
its files, its accounts and its database; what crosses is read access over HTTPS,
issued as an API key and revocable at any time.

Setting this up has two halves, and both sides do both if the sharing is mutual:

1. **Publish** your instance so a friend's server can reach it.
2. **Issue** that friend a key, and **register** their instance as a peer of yours.

See `docs/adr/0037-federated-libraries.md` for what this deliberately does not do.

## 1. Publish the instance through a Cloudflare Tunnel

A tunnel is used rather than a forwarded port: nothing is opened on the router, and
the origin's address is never published.

```bash
cloudflared tunnel login
cloudflared tunnel create pornarr
cloudflared tunnel route dns pornarr pornarr.example.com
```

`~/.cloudflared/config.yml`:

```yaml
tunnel: pornarr
credentials-file: /home/you/.cloudflared/<tunnel-id>.json
ingress:
  - hostname: pornarr.example.com
    service: http://127.0.0.1:8000
    originRequest:
      # A film is streamed in ranges over a long-lived response. The defaults
      # cut those off mid-playback.
      connectTimeout: 30s
      noTLSVerify: false
  - service: http_status:404
```

Run it as a service (`cloudflared service install`) and confirm from outside your
network:

```bash
curl -sS https://pornarr.example.com/api/health
```

Then, in Cloudflare:

- **Do not** put Cloudflare Access in front of `/api`. Access answers an API-key
  request with a login page, and a peer cannot log in. If you want Access on the web
  interface, bypass it for `/api/peers` and `/api/library`, or expose federation on a
  second hostname without Access.
- Leave caching off for `/api/*`. A cached poster is harmless; a cached library page
  is somebody else's page.

Two settings on your own instance are worth checking before you invite anyone:

- `base_path` must match what the tunnel serves (empty for a hostname root).
- `private_libraries` still applies. A peer sees exactly what the account behind its
  key would see when browsing, and nothing else.

## 2. Issue a key to the other household

The key is created on **your** instance and it inherits the scope of the account that
created it: a peer sees exactly what that account would see when browsing. With
`private_libraries` on, a key made by a guest account exposes that guest's titles and
the shared pool; a key made by an administrator exposes the whole pool. Pick the
account accordingly, and use a separate one per household — one key per peer is what
makes access revocable one household at a time.

1. Sign in as the account whose view you want to share.
2. Open **Account → API keys**, add a key labelled with the household it is for, and
   copy it. It is shown once.
3. Send the key and your tunnel URL over something end-to-end encrypted. Either the
   address you reach the instance at or its API root will do — `/api` is appended
   when it is missing, so both of these register the same peer:

   ```
   https://pornarr.example.com
   https://pornarr.example.com/api
   ```

To revoke, delete that key on your instance. Nothing needs to happen on the other
side: their next call degrades to "this peer is unavailable".

## 3. Register their instance as a peer

On your instance, as an administrator: **Settings → Peers → Add peer**, or directly:

```bash
curl -X POST https://pornarr.example.com/api/admin/peers \
  -H 'Content-Type: application/json' \
  -H "X-CSRF-Token: $CSRF" -b cookies.txt \
  -d '{"name":"karin","base_url":"https://karin.example.com/api","api_key":"pnr_…","enabled":true}'
```

Then press **Test**, or:

```bash
curl -X POST https://pornarr.example.com/api/admin/peers/<peer-id>/test \
  -H "X-CSRF-Token: $CSRF" -b cookies.txt
```

A healthy peer answers with `"health": "healthy"` and the number of titles it holds.
The key is never returned by any of these endpoints, and a failure is reported as a
short reason rather than an error message:

| `health_reason`    | What to check                                                          |
| ------------------ | ---------------------------------------------------------------------- |
| `unreachable`      | The tunnel is down, or the hostname does not resolve from this machine. |
| `timed_out`        | The origin is not answering; check `cloudflared` and the API container. |
| `unauthorized`     | The key was revoked, mistyped, or belongs to a deactivated account.     |
| `invalid_response` | Something that is not Pornarr answered — usually an Access login page.  |

Browsing now offers "Everything" alongside your own library. A peer's titles carry
its name, and playing one streams through your instance rather than exposing the
peer's URL to your browser.

## Day-to-day

- **A peer is down.** Browsing continues without it and the screen names it. Nothing
  needs to be disabled; it rejoins by itself.
- **Stopping.** Remove the peer, or register it with `"enabled": false` if you want
  the row without the reads. A disabled peer disappears from browsing and its proxy
  answers 404 immediately, without a call going out. The other side stops your access
  the same way, by deleting the key it issued you.
- **Playback is slow.** Every byte of a remote film crosses two home uplinks: the
  peer's upload and your download. Direct play over a tunnel is usually fine at
  1080p; a remote transcode is bounded by the peer's GPU, not yours.
- **What is logged.** The peer's id and name, never its key. If you are asked for a
  log to debug a connection, no redaction is needed.
