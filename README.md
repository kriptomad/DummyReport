![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)

# DummyReport

DummyReport is a multi-app Streamlit platform for Oracle database troubleshooting, AI-assisted reporting, and internal service operations. It was originally built as an internal support tool and is published here as a sanitized, fully-mocked portfolio demo.

Company-specific references, real infrastructure, credentials, and business documents have been removed and replaced with fictional demo data. Every feature below works against the bundled mock dataset — no real database connection is required to explore the app. See the Disclaimer section at the end for details on what was sanitized.

## What it does

DummyReport simulates a support/ops portal for a team that troubleshoots failed transactions in an Oracle-backed logistics system. It is split into three cooperating Streamlit apps that share the same authentication/session layer:

| App | Port | Purpose |
|---|---|---|
| `portal_app.py` | 8500 | Central login portal — routes an authenticated user to whichever of the two apps below they have access to, plus a unified admin dashboard |
| `app.py` | 8501 | Main troubleshooting console: report generation, error triage, AI query building, knowledge base, and autonomous-fix review |
| `psld_app.py` | 8502 | A second, independently-branded workspace for a different internal team, reusing the same engine with its own knowledge base, ticket queue, and learning pipeline |

Both apps run on the same underlying troubleshooting/AI engine (`troubleshooter/`, `ai/`, `database/`), so improvements to matching, learning, or query-generation logic benefit both.

## Key features

Authentication and administration
- Username/password auth with PBKDF2 password hashing, per-user RSA keypairs for end-to-end encrypted internal messaging, and a key-escrow mechanism so an admin-triggered password reset doesn't destroy a user's message history
- Self-registration with an admin-approval queue, role flags (admin, support, business/app-account, parts reviewer), and per-screen access control
- Shared, cross-process session store (cookie-based) so a single login works across all three apps
- Audit log covering logins, password resets, admin actions, and integration/queue failures, with category and severity tagging

Troubleshooting and reporting
- Natural-language AI query builder that converts plain-English/Portuguese questions into validated, read-only SQL
- Fuzzy/TF-IDF error matching against a knowledge base of known issues and fixes, tolerant of near-duplicate errors that only differ by an ID, date, or location
- Batch troubleshooting mode: paste many shipment/order IDs and get a consolidated report
- Schema explorer, SQL glossary, and a guided visual query builder for non-SQL users
- Excel/CSV/PDF/DOCX knowledge-base ingestion with automatic column standardization and versioned backups

Autonomous fix (AI-assisted resolution)
- A continuous-learning pipeline that studies historical errors and their corrections, learns to recognize failure patterns rather than exact strings, and proposes fixes for new, matching errors
- Proposed fixes land in a pending-approval queue — nothing is applied automatically, a support/admin user has to review and approve each one
- An AI control center in the admin dashboard to trigger and monitor training runs, with visible progress feedback

Internal messaging and presence
- End-to-end encrypted user-to-user messaging (RSA + AES-GCM hybrid scheme), so message bodies stay unreadable at rest
- Online/last-seen presence indicators, Teams-style deep links, and broadcast announcements from admins

## Technologies used

| Layer | Stack |
|---|---|
| UI / app framework | Streamlit, multi-page, custom theming (light/dark, color-blind-safe palettes) |
| Database | Oracle via `oracledb` — read-only query execution, schema introspection, connection profiles |
| Data wrangling | pandas, openpyxl, xlrd (legacy .xls), python-docx, pypdf, mammoth (DOCX to HTML viewer) |
| Machine learning | scikit-learn (TF-IDF, clustering) and rapidfuzz for fuzzy string matching; trained state persisted with joblib |
| AI / LLM integrations | OpenAI, Anthropic, Google Gemini, GitHub Copilot SDK — pluggable providers for text-to-SQL and chat |
| Auth and crypto | stdlib hashlib/secrets for PBKDF2 password hashing, `cryptography` for RSA-2048 + AES-256-GCM message encryption and Fernet-based key escrow |
| Automation (experimental) | playwright (browser-session capture experiment), msal (Azure AD/Entra ID SSO proof-of-concept) |
| Reporting | plotly for interactive charts, custom exporters in `reports/` |
| Persistence | flat JSON files with file locking (filelock) — no external DB server needed to run the app itself; Oracle is only the data source being inspected |

## Setup

```bash
pip install -r requirements.txt
streamlit run app.py            # main troubleshooting console (port 8501)
streamlit run psld_app.py        # secondary workspace (port 8502)
streamlit run portal_app.py      # unified login portal (port 8500)
```

No real database connection is needed to log in, browse the knowledge base, or explore the admin dashboard — the bundled `data/*.json` files provide a small, working mock dataset out of the box. Connecting to a live Oracle instance is optional and configured from the UI.

### Demo logins

| CWS / username | Password | Role |
|---|---|---|
| `demo_admin` | `DemoPass123!` | Administrator |
| `demo_support` | `DemoPass123!` | Support (can approve autonomous fixes) |
| `demo_user` | `DemoPass123!` | Regular user |

## Project structure

```
app.py                 # Main troubleshooting console
portal_app.py          # Unified login portal / router
psld_app.py            # Secondary standalone workspace
auth/                  # Login, sessions, roles, encrypted messaging, audit log
ai/                     # Schema catalog, text-to-SQL, Copilot-style analysis
troubleshooter/        # Matching engine, continuous learning, autonomous fix, KB
database/              # Oracle connection handling, query execution, schema introspection
ui/                     # Streamlit tab components (admin, KB, query builder, etc.)
i18n/                   # EN/PT translation strings
config/                 # App settings and DB connection defaults
integrations/          # ServiceNow SSO / session-capture experiments
reports/                # Batch processing and export helpers
data/                   # Mock/demo JSON seed data (safe to reset/inspect)
```

## Disclaimer

This is a sanitized, anonymized portfolio demo derived from a private internal tool. It is not affiliated with any specific employer. Everything in this repository — user accounts, knowledge-base entries, shipment/error history, hostnames, and sample data — is fictional and generated for demonstration purposes only. Real company names, logos, credentials, internal hostnames, employee names, and proprietary business documents from the original project were removed and replaced with mock equivalents before publishing.
