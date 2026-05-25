# 08 — Vendor Compliance Browser Agent

A production-shape **vendor portal compliance automation** for procurement teams. Logs into supplier portals, downloads required compliance documents (SOC 2, ISO 27001, W-9, COI), packages them into a typed evidence bundle with SHA-256 hashes, and emits an append-only JSON audit log of every browser action.

Two extraction paths are provided:

1. **Deterministic Playwright extractor** (`src/extractors/deterministic.py`) — fast, free, 100% reliable when the portal layout is known. **3/3 vendors pass eval.**
2. **Autonomous browser-use agent** (`src/agents/autonomous.py`) — LLM-driven, escalation path for unknown / changing portals. Provided as a demo; documented limitations on simple HTML forms.

## The business problem

Every Fortune 500 procurement team has a recurring headache:

- 200-2,000 active suppliers
- Each must produce SOC 2, ISO 27001, W-9, certificate of insurance, master service agreement
- Many post these on a vendor-specific portal you have to log into (with their per-supplier credentials)
- Procurement analysts manually visit each portal, login, download, file evidence — quarter after quarter

A typical analyst spends **20-30% of their week** on this. It scales linearly with vendor count and zero-percent with their judgment. Every minute saved is real procurement-team capacity.

| Metric | Manual | With this agent |
|--------|-------:|----------------:|
| Time per vendor | 5-10 min | **~3.7 s** (deterministic path) |
| Audit trail | None or per-analyst notes | Structured JSONL with timestamps, URLs, SHA-256 |
| Missing-doc detection | Spotted later | **Flagged at run time** |
| Reproducibility | Analyst-dependent | Deterministic + tested |

## Important: legality and ethics

Late 2025 saw **multiple lawsuits** filed against scrapers — Reddit v. SerpApi/Oxylabs/Perplexity (Oct 2025), Google v. SerpApi (Dec 2025) — for circumventing anti-bot systems on **public** sites. This project does NOT scrape arbitrary public portals. Instead:

- The demo target is a **self-hosted vendor portal** in `src/portal/` — you control it 100%
- An explicit **`ALLOWED_HOSTS` allowlist** prevents the agent from navigating off the listed domains; the credential vault refuses unknown hosts
- Real deployments must replace the demo portal with **portals you have written authorization to access** (your own supplier-portal vendors, with valid credentials and license terms)

The legal pattern is "robotic process automation against systems we have a contract with", not "scraping public sites."

## Stack

| Concern | Choice | Why |
|---------|--------|-----|
| Browser automation | **Playwright 1.60** (deterministic) and **browser-use 0.12** (autonomous) | Playwright is the industry standard; browser-use adds the LLM-driven path |
| Demo target | **Self-hosted FastAPI portal** | Zero legal risk, 100% reproducibility, three difficulty levels (basic, MFA, missing-doc) |
| Document generation | **reportlab** | Real PDF artifacts so SHA-256 / byte-count assertions exercise the actual code paths |
| Output schema | **Pydantic v2** | Typed `VendorEvidenceBundle` with per-item status, hash, validity, source URL |
| Audit log | JSONL append-only (same pattern as projects 06 and 07) | Per-action: vendor_id, principal, URL, outcome, duration_ms |
| Secret handling | browser-use `sensitive_data` substitution | Placeholder tokens in the prompt; real values substituted at action time so the LLM never sees them |
| LLM (autonomous path only) | OpenAI `gpt-4o-mini` | Cheap, plenty good for click-here / type-here decisions |

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                  Self-hosted vendor compliance portal (demo)            │
│   FastAPI + 3 vendors (acme-cloud, sentinel-security MFA, terra-data)  │
│   /<vendor>/login → /<vendor>/dashboard → /<vendor>/docs/<kind>         │
│   /<vendor>/downloads/<filename>.pdf                                    │
└────────────────────────────┬────────────────────────────────────────────┘
                             │ http://127.0.0.1:7878 (demo only)
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                  CredentialVault                                        │
│   • per-vendor credentials (username/password/MFA)                       │
│   • allowed_hosts allowlist enforced before any navigation              │
│   • exposes sensitive_data placeholders for browser-use substitution    │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
              ┌──────────────┴──────────────┐
              ▼                             ▼
   ┌────────────────────┐         ┌─────────────────────────────┐
   │  DETERMINISTIC      │         │  AUTONOMOUS (browser-use)   │
   │  Playwright         │         │  LLM-driven Agent           │
   │  extractor          │         │  Same task, no selectors    │
   │                     │         │                             │
   │  ✓ 3/3 vendors      │         │  Best-effort on unfamiliar │
   │  ✓ ~3.7 s/vendor    │         │  portals; fragile on simple │
   │  ✓ ~$0/run          │         │  HTML forms (documented)    │
   └─────────┬──────────┘         └─────────────┬───────────────┘
             │                                  │
             └──────────────┬───────────────────┘
                            ▼
        ┌───────────────────────────────────────┐
        │  VendorEvidenceBundle (Pydantic)       │
        │  + per-item file_path, sha256, status, │
        │    valid_until, source_url             │
        │  + JSONL audit log per browser action  │
        └───────────────────────────────────────┘
```

## Quick start

```sh
# 1. Configure
copy .env.example .env
# OPENAI_API_KEY only required if you'll use --autonomous

python -m playwright install chromium     # one-time

# 2. Start the demo vendor portal in one terminal
python -m src.cli serve-portal
# Browser: http://127.0.0.1:7878  → click any vendor → log in (password from src/portal/data.py)

# 3. From another terminal, run the deterministic eval
python -m src.cli eval

# 4. Or pull one vendor manually
python -m src.cli pull-evidence --vendor acme-cloud
python -m src.cli pull-evidence --vendor sentinel-security
python -m src.cli pull-evidence --vendor terra-data

# 5. Inspect the audit log
python -m src.cli show-audit --n 30

# 6. (Optional) try the autonomous agent path
python -m src.cli pull-evidence --vendor acme-cloud --autonomous
```

## Verified results

3/3 vendors pass the deterministic eval:

| Vendor | Difficulty | Status | Found | Notes |
|--------|------------|--------|-------|-------|
| acme-cloud | textbook flow | complete | 4/4 | clean login, all docs present |
| sentinel-security | MFA required | complete | 4/4 | reads code from in-page banner, types it |
| terra-data | missing COI | partial | 2/3 (1 expected missing) | correctly reports the gap |

Avg duration: **3.7 s/vendor**. See [`results/README.md`](./results/README.md) for the full breakdown.

## Demo vendors

| vendor_id | Difficulty | Required docs |
|-----------|------------|---------------|
| `acme-cloud` | basic | SOC 2, ISO 27001, W-9, COI (all present) |
| `sentinel-security` | MFA | SOC 2, ISO 27001, W-9, COI (all present, MFA required) |
| `terra-data` | missing-doc | SOC 2, W-9, **COI missing** (page exists, no PDF) |

The eval proves the agent both **finds** the right evidence AND **reports the gap** for terra-data correctly — that's what compliance teams actually need.

## Project layout

```
src/
├── cli.py                            # serve-portal / list-vendors / pull-evidence / eval / show-audit
├── config.py                         # typed Settings from .env (allowed_hosts, paths, headless)
├── audit.py                          # JSONL audit log (action / outcome / url / duration_ms)
├── credentials.py                    # vendor cred vault + allowlist enforcer
├── evidence.py                       # Pydantic VendorEvidenceBundle + EvidenceItem + sha256
├── portal/
│   ├── app.py                        # self-hosted FastAPI portal
│   ├── data.py                       # 3 demo vendors with different flows
│   └── pdfs.py                       # generates compliance PDFs at runtime via reportlab
├── extractors/
│   └── deterministic.py              # production-shape Playwright path (3/3 in eval)
├── agents/
│   └── autonomous.py                 # browser-use LLM-driven path
└── eval/
    ├── golden.py                     # per-vendor expectations (must_be_found / expected_missing)
    └── runner.py                     # runs deterministic path over goldens, asserts
```

## Production design choices

1. **Allowlist enforced in the credential vault.** The vault refuses to hand out credentials for any host outside `ALLOWED_HOSTS`. Even if the LLM tries to navigate elsewhere, it can't get the password. This is the strongest defense against prompt-injection-driven exfil.
2. **Two extraction paths, same output type.** The deterministic and autonomous extractors both return `VendorEvidenceBundle`. Production teams write the deterministic path per portal once and only escalate to the LLM agent when a portal is new or just changed.
3. **Self-hosted demo target.** Avoids the legal exposure that comes with scraping arbitrary public sites (Google v. SerpApi, Reddit v. Oxylabs, etc., late 2025). Real deployments must run against portals you have written authorization to access.
4. **`sensitive_data` substitution for the LLM path.** browser-use replaces placeholder tokens (`{vendor_username}`, `{vendor_password}`, `{vendor_mfa_code}`) with real values at action time so the LLM never sees the secret in plain text.
5. **SHA-256 + byte count on every downloaded artifact.** Auditors verify the hash; tamper detection is built into the bundle schema.
6. **JSONL audit log compatible with Splunk/Datadog/Loki.** One event per browser action, with `vendor_id`, `principal`, `outcome`, `duration_ms`.
7. **Honest about agent limits.** browser-use on simple HTML forms can return empty actions or stale element indices — a documented behavior worth being explicit about. The deterministic path is the eval baseline because that's how production actually ships.

## Inspiration (motivation only — no code copied)

- [`browser-use/browser-use`](https://github.com/browser-use/browser-use) — the autonomous browser agent library
- [`microsoft/playwright`](https://github.com/microsoft/playwright) — the deterministic browser engine
- [Skyvern's comparison post on Browser-Use vs Stagehand](https://www.skyvern.com/blog/browser-use-vs-stagehand-which-is-better/) — informed the two-path design
- [`illusory.io` web scraping compliance article (2026)](https://www.illusory.io/blog/web-scraping-compliance-2026-legal-ethical-proxy) — informed the legal framing

## Status

- [x] Self-hosted vendor compliance portal (FastAPI, 3 vendors, MFA, missing-doc)
- [x] Deterministic Playwright extractor with login + MFA + download + SHA-256
- [x] Allowlist-enforcing credential vault
- [x] Pydantic-typed VendorEvidenceBundle with per-item status
- [x] JSONL audit log per browser action
- [x] Eval over 3 golden vendors — **3/3 pass**
- [x] Autonomous browser-use agent path with `sensitive_data` substitution
- [ ] Real-portal adapter (replace demo portal with a customer's supplier portal)
- [ ] Email-out of evidence bundles to Procurement / GRC tools (ServiceNow, Vanta, Drata)
- [ ] Recurring schedule with delta detection (only re-pull docs when changed)
- [ ] Vault / 1Password / AWS Secrets Manager backend for credentials
- [ ] Per-vendor concurrency cap (avoid getting throttled by aggregator portals)
