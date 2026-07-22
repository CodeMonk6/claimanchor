# Starter‑repo feedback

Findings from building ClaimAnchor on the `agent_skeleton` starter template, with
reproduction steps, expected vs. actual behavior, and environment. Ordered by
impact. Items marked **Fixed** include a change already applied in this submission
and contributed back as a pull request.

**Environment:** macOS (Darwin 25.5.0), Python 3.14.6, `a2a-sdk` pinned `0.3.2`,
clean clone of the starter template.

**All findings are filed against
[`washu-dev/agent-skeleton`](https://github.com/washu-dev/agent-skeleton):**

| # | Finding | Filed as |
|---|---|---|
| 1 | `.gitignore` doesn't ignore `.env` (secret-leak) | [issue #9](https://github.com/washu-dev/agent-skeleton/issues/9) · **[PR #10](https://github.com/washu-dev/agent-skeleton/pull/10)** |
| 2 | No `.env.example` ships | [issue #2](https://github.com/washu-dev/agent-skeleton/issues/2) |
| 3 | Submission-instruction contradiction | [issue #3](https://github.com/washu-dev/agent-skeleton/issues/3) |
| 4 | Tests fail from a clean clone | [issue #4](https://github.com/washu-dev/agent-skeleton/issues/4) |
| 5 | `serve-handler` ignores `AGENT_A2A_URL` (loopback) | [issue #5](https://github.com/washu-dev/agent-skeleton/issues/5) · **[PR #11](https://github.com/washu-dev/agent-skeleton/pull/11)** |
| 6 | No-auth default (`0.0.0.0`, empty `securitySchemes`) | [issue #6](https://github.com/washu-dev/agent-skeleton/issues/6) |
| 7 | `a2a-sdk==0.3.2` pin undocumented | [issue #7](https://github.com/washu-dev/agent-skeleton/issues/7) |
| 8 | Reserved-name ambiguity (`*.card.json`) | [issue #8](https://github.com/washu-dev/agent-skeleton/issues/8) |

---

## 1. `.gitignore` does not ignore `.env` — credential‑leak risk (security) — **Fixed**

- **Repro:** clean clone → create a real `.env` with `ANTHROPIC_API_KEY=…` (as the
  quickstart requires) → `git status`.
- **Expected:** `.env` is ignored so a real key can't be committed.
- **Actual:** `.gitignore` covers only `__pycache__/`, `*.pyc`, `*.egg-info/`,
  `.venv/`, `.DS_Store`. `.env` shows up as an untracked file and is one
  `git add .` away from being committed — a real credential‑leak risk that "no secrets
  committed" guidance is meant to prevent.
- **Fix (applied):** added `.env` / `.env.*` (with `!.env.example`) to
  `.gitignore`.

## 2. No `.env.example` ships, though it is required — **Fixed**

- **Repro:** clean clone → look for a secrets template.
- **Expected:** a value‑less `.env.example` teams copy to configure secrets safely.
- **Actual:** none present; each team must invent the variable names.
- **Fix (applied):** added `.env.example` documenting `ANTHROPIC_API_KEY`,
  `UNPAYWALL_EMAIL`, model/endpoint and A2A overrides.

## 3. `HACKATHON_CHEATSHEET.md` submission block contradicts `dtrc-hackathon.md`

- **Repro:** read `HACKATHON_CHEATSHEET.md` (submission section) and compare with
  `dtrc-hackathon.md`.
- **Expected:** one consistent submission target + deadline.
- **Actual:** the cheatsheet still has unfilled placeholders
  (`Submit to: _<owner / channel — fill in>_ · by: _<deadline>_`), while
  `dtrc-hackathon.md` gives the real deadline (6:00 PM Wed Jul 22) and recipients
  (mdan@ / adith@). A team reading only the cheatsheet has no submission target.

## 4. Tests can't run from a clean clone; `pytest` isn't a declared dev dep — **Fixed (workaround)**

- **Repro:** fresh clone, before `pip install -e .`, run
  `python -m pytest agent_skeleton/tests -q`.
- **Expected:** the documented test command runs.
- **Actual:** `ModuleNotFoundError: No module named 'agent_skeleton'` — the folder
  name differs from the import name, so nothing resolves until the editable
  install. `pytest` is also not declared anywhere, so the command fails twice on a
  clean machine.
- **Fix (applied, our tests):** added `tests/conftest.py` (plus a per‑file
  self‑bootstrap) that registers `agent_skeleton` at the repo root, so the suite
  runs with the standard library alone — no install, no `pytest` required
  (`python tests/test_*.py`). Suggest the template ship the same shim, and either
  declare a `[project.optional-dependencies].dev = ["pytest"]` or document the
  install‑first requirement.

## 5. `serve-handler` ignores `AGENT_A2A_URL` — Path‑B deployments always advertise `127.0.0.1` — **Fixed**

- **Repro:**
  `AGENT_A2A_URL=https://host/ python -m agent_skeleton.serve serve-handler --file handler.py --class X --card agent.card.json`,
  then read the served `/.well-known/agent-card.json`.
- **Expected:** the advertised card `url` is the deployment URL so a planner can
  reach the agent (this is how `serve-a2a` already behaves).
- **Actual:** `serve-handler` has no `--advertise-url` flag and never reads
  `AGENT_A2A_URL`; with `--card` it serves the file's `url` verbatim
  (`http://127.0.0.1:9110/`), and the minimal card uses the local host:port. So the
  two subcommands diverge, and a Path‑B agent — the primary deploy path —
  registers with a loopback URL and discovery silently fails.
- **Fix (applied):** added `--advertise-url` to `serve-handler` (defaulting to
  `AGENT_A2A_URL`) and made it override the card `url`, matching `serve-a2a`.

## 6. No‑auth exposure: binds `0.0.0.0` with empty `securitySchemes`

- **Repro:** start the server; note bind address and card `securitySchemes`.
- **Expected:** at least a prominent README warning, or an opt‑in auth scheme.
- **Actual:** the app binds `0.0.0.0` with `securitySchemes: {}` and
  `security: []` — fine for local dev, but easy to expose an unauthenticated agent
  when deployed. Noted only in the repo's internal working notes, not in the README a
  deployer reads first.

## 7. Exact `a2a-sdk==0.3.2` pin is correct but brittle and undocumented

- **Context:** the `a2a-sdk` line has since moved to 1.x with breaking card‑shape
  changes (`AgentInterface` / `supported_interfaces`), and the `.well-known` card
  path was renamed in 0.3.0. The exact pin is the *right* call, but nothing in the
  README explains why, so a team that "upgrades to latest" or whose environment
  resolves a newer version will silently break serving. Suggest a one‑line note in
  the README/pyproject explaining the exact pin.

## 8. Reserved‑name ambiguity: `*.card.json` vs. the clone‑and‑deploy flow

- **Context:** `INTEGRATION_GUIDE.md` lists `*.card.json` (and `agent_skeleton/`,
  `Dockerfile`) as reserved names the system generates for the Custom‑agent **zip
  upload** flow. But the primary deploy path is "clone the GitHub repo," where
  `agent.card.json` is expected to be present. It's unclear which rule governs a
  cloned repo. Suggest the guide clarify that the reserved‑name
  restriction applies to the upload flow only.

---

### Checked, no defect found

- **OpenAlex polite‑pool / mailto assumption:** grepped the template for the
  retired OpenAlex keyless `mailto` pattern (OpenAlex went metered in Feb 2026);
  the starter does not use OpenAlex, so no stale assumption to fix. Noting it here
  because it's a common trap for teams that add OpenAlex — ClaimAnchor reads any
  OpenAlex key from the environment and degrades to Crossref if absent.
