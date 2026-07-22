# Starter‑repo feedback

Findings from building ClaimAnchor on the `agent_skeleton` starter template, with
reproduction steps, expected vs. actual behavior, and environment. Ordered by impact.
Items marked **Fixed** include a change already applied in this submission and
contributed back as a pull request.

**Environment:** macOS (Darwin 25.5.0), Python 3.14.6, `a2a-sdk` pinned `0.3.2`,
clean clone of the starter template.

**Filed against [`washu-dev/agent-skeleton`](https://github.com/washu-dev/agent-skeleton)** —
each issue carries full Repro / Expected / Actual / Fix detail:

| # | Finding | Filed as |
|---|---|---|
| 1 | `.gitignore` doesn't ignore `.env` (committed‑secret risk) | [issue #9](https://github.com/washu-dev/agent-skeleton/issues/9) · **[PR #10](https://github.com/washu-dev/agent-skeleton/pull/10)** |
| 2 | `serve-handler` has no way to set the advertised URL (ignores `AGENT_A2A_URL`) | [issue #5](https://github.com/washu-dev/agent-skeleton/issues/5) · **[PR #11](https://github.com/washu-dev/agent-skeleton/pull/11)** · rel. #14 |
| 3 | `serve-handler` minimal card advertises the `0.0.0.0` bind wildcard by default | [issue #14](https://github.com/washu-dev/agent-skeleton/issues/14) · **[PR #23](https://github.com/washu-dev/agent-skeleton/pull/23)** · rel. #5 |
| 4 | No‑auth default (`0.0.0.0`, empty `securitySchemes`) | [issue #6](https://github.com/washu-dev/agent-skeleton/issues/6) |
| 5 | Tests fail from a clean clone (import name ≠ folder; `pytest` undeclared) | [issue #4](https://github.com/washu-dev/agent-skeleton/issues/4) |
| 6 | No `.env.example` ships | [issue #2](https://github.com/washu-dev/agent-skeleton/issues/2) |
| 7 | `HandlerExecutor` orphans the handler coroutine on cancel (compute + credential retention) | [issue #15](https://github.com/washu-dev/agent-skeleton/issues/15) |
| 8 | `HandlerExecutor` hangs the task in `working` on a bad `answer` return | [issue #16](https://github.com/washu-dev/agent-skeleton/issues/16) · **[PR #24](https://github.com/washu-dev/agent-skeleton/pull/24)** |
| 9 | Step‑cap exhaustion returns an empty answer, discarding gathered tool results | [issue #17](https://github.com/washu-dev/agent-skeleton/issues/17) |
| 10 | `a2a` protocol path silently drops the model‑supplied `payload` | [issue #18](https://github.com/washu-dev/agent-skeleton/issues/18) |
| 11 | `validate_tool_registry` doesn't validate the schema envelope (`type` / `parameters.type`) | [issue #22](https://github.com/washu-dev/agent-skeleton/issues/22) · **[PR #27](https://github.com/washu-dev/agent-skeleton/pull/27)** |
| 12 | `calculator` can return non‑JSON‑serializable `complex`/`inf`/`nan` | [issue #21](https://github.com/washu-dev/agent-skeleton/issues/21) · **[PR #26](https://github.com/washu-dev/agent-skeleton/pull/26)** |
| 13 | Non‑numeric `AGENT_A2A_PORT` crashes startup with an opaque error | [issue #20](https://github.com/washu-dev/agent-skeleton/issues/20) · **[PR #25](https://github.com/washu-dev/agent-skeleton/pull/25)** |
| 14 | `require_a2a()` install hint omits the required `[http-server]` extra | [issue #19](https://github.com/washu-dev/agent-skeleton/issues/19) |

---

A few of the highest‑impact ones written up in full below; the rest have the same
detail on their linked issues.

## `.gitignore` does not ignore `.env` — credential‑leak risk (security) — **Fixed**

- **Repro:** clean clone → create a real `.env` with `ANTHROPIC_API_KEY=…` (as the
  quickstart requires) → `git status`.
- **Expected:** `.env` is ignored so a real key can't be committed.
- **Actual:** `.gitignore` covers only `__pycache__/`, `*.pyc`, `*.egg-info/`,
  `.venv/`, `.DS_Store`. `.env` shows up as an untracked file and is one
  `git add .` away from being committed — a real credential‑leak risk that "no secrets
  committed" guidance is meant to prevent.
- **Fix (applied):** added `.env` / `.env.*` (with `!.env.example`) to `.gitignore`.

## `serve-handler` advertises an unreachable URL — **Fixed**

Two distinct causes, filed and fixed separately ([#5](https://github.com/washu-dev/agent-skeleton/issues/5) + [#14](https://github.com/washu-dev/agent-skeleton/issues/14)); both are needed for a reachable card.

- **Repro:** `AGENT_A2A_URL=https://host/ python -m agent_skeleton.serve serve-handler
  --file handler.py --class X --card agent.card.json`, then read the served
  `/.well-known/agent-card.json`.
- **Expected:** the advertised card `url` is a URL a planner can actually reach (this is
  how `serve-a2a` already behaves).
- **Actual:** `serve-handler` has no `--advertise-url` flag and never reads
  `AGENT_A2A_URL` (#5), and its minimal card defaults the host to the bind wildcard
  `0.0.0.0` (#14) — which isn't a dialable address. Either way, a Path‑B agent (the
  primary deploy path) advertises a URL nothing can connect to, and discovery silently
  fails.
- **Fix (applied):** `serve-handler` now honors `--advertise-url` / `AGENT_A2A_URL`
  (PR #11) and normalizes a wildcard bind host to loopback for the advertised URL
  (PR #23).

## No‑auth exposure: binds `0.0.0.0` with empty `securitySchemes`

- **Repro:** start the server; note bind address and card `securitySchemes`.
- **Expected:** at least a prominent README warning, or an opt‑in auth scheme.
- **Actual:** the app binds `0.0.0.0` with `securitySchemes: {}` and `security: []` —
  fine for local dev, but easy to expose an unauthenticated agent when deployed. Noted
  only in the repo's internal working notes, not in the README a deployer reads first.

## Tests can't run from a clean clone; `pytest` isn't a declared dev dep — **Fixed (workaround)**

- **Repro:** fresh clone, before `pip install -e .`, run
  `python -m pytest agent_skeleton/tests -q`.
- **Expected:** the documented test command runs.
- **Actual:** `ModuleNotFoundError: No module named 'agent_skeleton'` — the folder name
  differs from the import name, so nothing resolves until the editable install; `pytest`
  is also not declared anywhere, so the command fails twice on a clean machine.
- **Fix (applied, our tests):** added `tests/conftest.py` (plus a per‑file self‑bootstrap)
  that registers `agent_skeleton` at the repo root, so the suite runs with the standard
  library alone — no install, no `pytest` required (`python tests/test_*.py`). Suggest the
  template ship the same shim, and either declare a
  `[project.optional-dependencies].dev = ["pytest"]` or document the install‑first
  requirement.

---

### Checked, no defect found

- **OpenAlex polite‑pool / mailto assumption:** grepped the template for the retired
  OpenAlex keyless `mailto` pattern (OpenAlex went metered in Feb 2026); the starter does
  not use OpenAlex, so no stale assumption to fix. Noting it here because it's a common
  trap for teams that add OpenAlex — ClaimAnchor reads any OpenAlex key from the
  environment and degrades to Crossref if absent.
