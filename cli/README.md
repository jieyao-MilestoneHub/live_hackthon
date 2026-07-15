# crestcut

**Ride your livestream's crests into clips.** A command-line tool for the 浪 LIVE
AI highlight-detection + clip editor — create a project, feed it a chat log or a
video, and get back detected highlights, a cut timeline, and a rendered short.

`crestcut` is built to be driven by humans **and** AI agents: structured `--json`
output, a self-describing `describe` command, clean exit codes, and a
non-interactive mode.

---

## Architecture — decoupled by design

`crestcut` is a **standalone, stdlib-only HTTP client bound to the API contract**
(`contracts/openapi.yaml`) — a Python port of the web frontend's `lib/api.ts`. It
has **zero import edges into `backend-api`** and **no AWS SDK**: the deployed API
Gateway/Lambda backend is the AWS boundary, and the CLI reaches AWS only through
the API + presigned URLs (the same PUT/GET the browser does).

That is the thin-client-over-a-versioned-API pattern used by `kubectl`, `gh`, and
the Stripe CLI. The payoff is **portability**: the backend is a *backing service
addressed by a URL*, so migrating it (Lambda → Cloud Run/K8s, S3 → GCS/MinIO,
Cognito → any OIDC IdP) is a **profile change, not a code change**. The one place
an IdP could leak is isolated behind an `AuthProvider` seam in `auth.py`.

---

## Install

```bash
# from the repo root
pip install ./cli          # gives you a `crestcut` command (pipx works too)
# or run without installing:
cd cli && python -m crestcut --help
```

Requires Python ≥ 3.11. No third-party dependencies.

## Quickstart — local, zero AWS

```bash
# 1) boot a local in-memory backend (own terminal). Zero AWS, no credentials.
crestcut up

# 2) one-shot the whole pipeline from a chat log
crestcut clip --chat mychat.csv --seconds 20 --source-duration-ms 600000

# …with a (stub) render + download
crestcut clip --chat mychat.csv --render --out ./clip.mp4
```

`clip` runs **create → upload → analyze → compose → (render → download)**. In
local mode the render uses a stub encoder (placeholder bytes) — the real value
offline is the **highlights + timeline + annotations**. A real encoded `.mp4`
comes from the `dev` profile.

## Quickstart — dev (real AWS)

```bash
crestcut --profile dev login                 # Cognito → cached bearer token
crestcut --profile dev clip --video vod.mp4 --render --out ./clip.mp4 --wait
```

The `dev` profile targets the deployed API; the video path uses Transcribe
(fired by the S3 upload event) and produces a real rendered clip.

## Commands

| Command | What it does |
|---|---|
| `clip (--chat\|--video) FILE` | one-shot pipeline (headline) |
| `up` | boot a local in-memory backend (zero AWS) |
| `login` / `logout` | obtain / clear a bearer token |
| `project create` / `project get ID` | create / inspect a project |
| `upload ID (--video\|--chat) FILE` | upload media / chat log |
| `analyze ID` | run chat-log analysis |
| `highlights list ID` / `highlights patch ID HID` | list / correct highlights |
| `compose ID [--seconds N --lock… --exclude…]` | (re)build the timeline |
| `timeline get ID [--version N]` | read a timeline |
| `annotate ID [--get]` | 5-dimension + narrative-beat annotations |
| `render submit ID [--wait --out]` / `render status RID` | render + poll |
| `download AID --out FILE` | download a finished artifact |
| `describe` | machine catalog of commands + live API schema |

Run `crestcut <command> -h` for full flags.

## Configuration

Precedence: **flags › env (`CRESTCUT_*`) › `./.crestcut.toml` › `~/.config/crestcut/config.toml` › defaults.**

- `--profile {local,dev}` selects an environment (base URL + auth).
- `--api-base URL` overrides the base URL; `--token` / `CRESTCUT_TOKEN` supplies a bearer token.
- `CRESTCUT_COGNITO_CLIENT_ID` / `CRESTCUT_COGNITO_REGION` configure `login`.

Example `~/.config/crestcut/config.toml`:

```toml
profile = "local"
[profiles.dev]
api_base = "https://your-api.example.com"
cognito_client_id = "xxxxxxxx"
```

## For AI agents

- **Always pass `--json`** (or set `CRESTCUT_JSON=1`). Results go to **stdout**;
  progress/logs/errors go to **stderr** — so `crestcut … --json | jq` is clean.
- **Self-discover** the surface: `crestcut describe --json` lists every command,
  the global flags, exit codes, and the live API schema.
- **Exit codes:** `0` ok · `2` usage · `3` not found / invalid state · `4` auth ·
  `5` backend unreachable · `6` wait timed out. On failure in `--json` mode a
  `{"error": {code, message, hint}}` object is written to stdout.
- **Non-interactive:** pass `--no-input` (and `--token`) so nothing ever prompts.
- **`--plain`** gives tab-separated rows for `grep`/`awk`.

Recipe — detect highlights from a chat log and read them as JSON:

```bash
crestcut up &                                             # or a real --profile dev
pid=$(crestcut clip --chat chat.csv --json | jq -r .project_id)
crestcut highlights list "$pid" --json | jq '.highlights[] | {id: .highlight_id, score, title: .suggested_title}'
```

## Notes & limits

- **Offline (`local`) = chat flow.** Video/transcribe analysis needs real AWS
  (`--profile dev`); offline it can't run, and the local render is a stub.
- `crestcut up` shells out to `uvicorn` (a process boundary, not an import) and
  needs the backend runtime deps (`fastapi`, `uvicorn`) importable. It sets
  `USE_INMEMORY=1` + `RENDER_INLINE_ENCODE=1` and serves on port **8080** (the
  stub presigned URLs assume 8080).

## Tests

```bash
cd cli && python -m pytest      # stdlib-only, no backend needed (fake transport)
```
