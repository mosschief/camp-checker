# Hold request templates

One JSON file per `auto_hold: true` watch, referenced from `config.yaml` as
`hold_template: holds/<name>.json`. Real templates are git-ignored; only
`example.hold.json` is committed.

The hold/cart endpoint is **not documented** and is never guessed — each
template comes from a request you capture in your browser. See the main
README section **"Capturing a hold request"** for the DevTools walkthrough.

Template format:

```json
{
  "url": "…",                 // from your capture
  "method": "POST",
  "headers": { … },           // from your capture; put {{SESSION_COOKIE}} / {{CSRF_TOKEN}} where the live values go
  "json": { … }               // request body from your capture, with placeholders
}
```

Available placeholders (injected at fire time):

| Placeholder | Value |
|---|---|
| `{{SESSION_COOKIE}}` | `SESSION_<NAME>_COOKIE` from `.env` |
| `{{CSRF_TOKEN}}` | `SESSION_<NAME>_CSRF` from `.env` |
| `{{CAMPSITE_ID}}` / `{{RESOURCE_ID}}` | the specific site camply found open |
| `{{RESOURCE_LOCATION_ID}}` | from the watch entry |
| `{{MAP_ID}}` | from the watch entry |
| `{{CAMPGROUND_ID}}` | from the watch entry |
| `{{START_DATE}}` / `{{END_DATE}}` | the detected opening's dates (ISO) |
| `{{NIGHTS}}` | the watch's night count |

A JSON value that is *exactly* one placeholder (e.g. `"mapId": "{{MAP_ID}}"`)
is replaced with the typed value (integers stay integers). Placeholders inside
longer strings are substituted as text.

**Scrub any card/payment data from the capture before saving it here.**
