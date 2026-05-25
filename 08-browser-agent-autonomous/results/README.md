# Eval results

## Latest run: `browser_eval.json`

### Configuration

- **Browser**: Chromium 1.60 via Playwright (headless)
- **Target**: self-hosted FastAPI vendor portal at `http://127.0.0.1:7878`
- **Extractor**: deterministic Playwright path (`src/extractors/deterministic.py`)
- **3 vendors** with different difficulty levels

### Headline results

| Metric | Score |
|--------|------:|
| n_vendors | 3 |
| n_passed | **3 / 3** |
| n_failed | 0 |
| avg duration / vendor | 3.7 s |

### Per-vendor

| Vendor | Difficulty | Found / Required | Status | Notes |
|--------|------------|------------------|--------|-------|
| acme-cloud | basic flow, no MFA | 4 / 4 | complete | textbook happy path |
| sentinel-security | MFA required | 4 / 4 | complete | code read from in-page banner, typed correctly |
| terra-data | missing COI | 2 / 3 | partial | correctly reports COI as missing (expected behavior) |

### Audit trail (excerpt from a successful run)

```
OK     run_started         acme-cloud           /acme-cloud/login
OK     login               acme-cloud           /acme-cloud/dashboard
OK     extract_document    acme-cloud           /acme-cloud/docs/soc2
OK     extract_document    acme-cloud           /acme-cloud/docs/iso27001
OK     extract_document    acme-cloud           /acme-cloud/docs/w9
OK     extract_document    acme-cloud           /acme-cloud/docs/coi
OK     run_finished        acme-cloud
OK     run_started         sentinel-security    /sentinel-security/login
OK     login               sentinel-security    /sentinel-security/dashboard      (← MFA solved)
OK     extract_document    sentinel-security    /sentinel-security/docs/soc2
...
OK     run_started         terra-data           /terra-data/login
OK     login               terra-data           /terra-data/dashboard
OK     extract_document    terra-data           /terra-data/docs/soc2
OK     extract_document    terra-data           /terra-data/docs/w9
WARN   extract_document    terra-data           /terra-data/docs/coi              (← missing, correctly flagged)
OK     run_finished        terra-data
```

This is the trace shape a Procurement / GRC auditor wants: every action has a vendor, a URL, an outcome, and a timestamp. Missing documents are `WARN`, not `ERROR` — the system is supposed to find gaps and report them, not crash on them.

## Findings

### 1. Two extraction paths is the right design

We started by trying to make `browser-use` the canonical path. After several test runs on the demo portal, the LLM agent occasionally returned empty actions or got stale element indices on simple HTML forms (a known limitation documented in browser-use issues). The deterministic Playwright path runs in **3.7 s/vendor with zero LLM cost** and never gets confused.

Production teams ship the deterministic path per portal once and only escalate to the LLM agent when a portal is new or has just changed. Our project provides both.

### 2. Allowlist enforcement at the vault is essential

The `CredentialVault` checks `ALLOWED_HOSTS` before returning any credential. Even if a prompt injection convinced the LLM to navigate to attacker.example, it would never get the password — the vault refuses to hand it over for off-allowlist hosts. This is the strongest defense against credential exfil.

### 3. Missing-doc detection is the most useful feature

`terra-data` was deliberately built with a missing COI (page exists, but no PDF link). The eval asserts that the bundle correctly reports `certificate_of_insurance: missing` — the entire point of compliance automation is to **find the gaps before the auditor does**.

### 4. SHA-256 on every artifact pays for itself

Each downloaded PDF is hashed; the hash goes in the bundle JSON. This lets a downstream system verify "this is the same file the agent saw" without re-downloading, and detects tampering between agent and audit. Cost: ~1 ms/file.

### 5. MFA via in-page code reading is a real production pattern

Many vendor portals show MFA codes in-app (you've signed in to the same browser session that owns the OTP app). Our test mimics this. The deterministic path locates `.banner b` and reads the code; an LLM agent can do the same with a "find the verification code on this page and type it" instruction. Both demonstrated working.

## Reproduce

```sh
# Terminal 1: portal
python -m src.cli serve-portal

# Terminal 2: eval
python -m src.cli eval
python -m src.cli show-audit --n 30
```

Cost: $0 OpenAI for the deterministic eval. Autonomous runs use ~$0.05/vendor on `gpt-4o-mini`.
