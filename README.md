# 🚢 DummyReport

**DummyReport** is a multi-app Streamlit platform for **Oracle database troubleshooting, AI-assisted reporting, and internal service operations** — originally built as a real internal support tool and published here as a **sanitized, fully-mocked portfolio demo**.

All company-specific references, real infrastructure, credentials, and business documents have been stripped out and replaced with **100% fictional demo data**. Every feature described below is fully functional against the bundled mock dataset — no real database connection is required to explore the app.

> 📖 See the [Disclaimer](#-disclaimer) section for details on what was sanitized.

---

## 🧩 What it does

DummyReport simulates a day-in-the-life support/ops portal for a team that troubleshoots failed transactions in an Oracle-backed logistics system. It is split into three cooperating Streamlit apps that share the same authentication/session layer:

| App | Port | Purpose |
|---|---|---|
| `portal_app.py` | 8500 | Central login portal — routes an authenticated user to whichever of the two apps below they have access to, plus a unified Admin Dashboard |
| `app.py` | 8501 | **Main troubleshooting console** — the core product: report generation, error triage, AI query building, knowledge base, and autonomous-fix review |
| `psld_app.py` | 8502 | A second, independently-branded workspace for a different internal team/domain, reusing the same engine with its own knowledge base, ticket queue, and neural-net learning pipeline |

Both apps are driven by the same underlying troubleshooting/AI engine (`troubleshooter/`, `ai/`, `database/`) so improvements to matching, learning, or query-generation logic benefit both simultaneously.

## ✨ Key features

**Authentication & administration**
- Username/password auth with PBKDF2 password hashing, per-user RSA keypairs for end-to-end encrypted internal messaging, and a **key-escrow mechanism** so an admin-triggered password reset never destroys a user's message history
- Self-registration with an admin-approval queue, role flags (admin, support, business/app-account, parts reviewer), and granular per-screen access control
- Shared, cross-process session store (cookie-based) so a single login works across all three apps (SSO-like experience)
- Full audit log (logins, password resets, admin actions, integration/queue failures) with category/severity tagging — not just a login trail

**Troubleshooting & reporting**
- Natural-language **AI Query Builder**: converts plain-English/Portuguese questions into validated, read-only SQL against the configured schema
- Fuzzy/TF-IDF **smart error matching** against a knowledge base of known issues + fixes, tolerant of near-duplicate errors that only differ by an ID/date/location
- Batch troubleshooting mode (paste many shipment/order IDs, get a consolidated report)
- Schema explorer, SQL glossary, and a guided visual Query Builder for non-SQL users
- Excel/CSV/PDF/DOCX knowledge-base ingestion with automatic column standardization and versioned backups

**Autonomous Fix (AI-assisted resolution)**
- A continuous-learning pipeline that studies historical errors + their corrections, learns to recognize failure *patterns* (not just exact strings), and proposes fixes for new, matching errors
- Proposed fixes land in a **Pending Approval** queue — nothing is applied automatically; a Support/Admin-tagged user must review and approve every AI-suggested action
- Dedicated **AI Control Center** in the admin dashboard to trigger/monitor training runs, with visible progress feedback (not a silent "mock" button)

**Internal messaging & presence**
- End-to-end encrypted user-to-user messaging (RSA + AES-GCM hybrid scheme) — message bodies are unreadable at rest, even to whoever hosts the app
- Online/last-seen presence indicators, Teams-style deep links, and broadcast announcements from admins

## 🛠️ Technologies used

| Layer | Stack |
|---|---|
| UI / App framework | [Streamlit](https://streamlit.io/) (multi-page, custom theming, light/dark + color-blind-safe palettes) |
| Database | Oracle (`oracledb` driver) — read-only query execution, schema introspection, connection profiles |
| Data wrangling | `pandas`, `openpyxl`, `xlrd` (legacy `.xls`), `python-docx`, `pypdf`, `mammoth` (DOCX→HTML viewer) |
| Machine learning | `scikit-learn` (TF-IDF vectorization, clustering) + `rapidfuzz` for fuzzy string matching; trained state persisted with `joblib` |
| AI / LLM integrations | OpenAI, Anthropic (Claude), Google Gemini (`google-genai`), GitHub Copilot SDK — pluggable providers for the Text-to-SQL and Copilot Chat features |
| Auth & crypto | Stdlib `hashlib`/`secrets` (PBKDF2 password hashing), `cryptography` (RSA-2048 + AES-256-GCM hybrid encryption for messaging, Fernet for key wrapping/escrow) |
| Automation (experimental) | `playwright` (browser-session capture experiment), `msal` (Azure AD/Entra ID SSO proof-of-concept) |
| Reporting | `plotly` (interactive charts), custom exporters (`reports/`) |
| Persistence | Flat JSON files with file-locking (`filelock`) — no external DB server required to run the app itself; only the *inspected* data source is Oracle |

## 🚀 Setup

```bash
pip install -r requirements.txt
streamlit run app.py            # main troubleshooting console (port 8501)
streamlit run psld_app.py        # secondary workspace (port 8502)
streamlit run portal_app.py      # unified login portal (port 8500)
```

No real database connection is needed to log in, browse the knowledge base, or explore the admin dashboard — the bundled `data/*.json` files provide a small, working mock dataset out of the box. Connecting to a live Oracle instance (optional) requires filling in a connection profile from the UI.

### 🔑 Demo logins

| CWS / username | Password | Role |
|---|---|---|
| `demo_admin` | `DemoPass123!` | Administrator |
| `demo_support` | `DemoPass123!` | Support (can approve autonomous fixes) |
| `demo_user` | `DemoPass123!` | Regular user |

## 📂 Project structure

```
app.py                 # Main troubleshooting console
portal_app.py          # Unified login portal / router
psld_app.py            # Secondary standalone workspace
auth/                  # Login, sessions, roles, encrypted messaging, audit log
ai/                     # Schema catalog, Text-to-SQL, Copilot-style analysis
troubleshooter/        # Matching engine, continuous learning, autonomous fix, KB
database/              # Oracle connection handling, query execution, schema introspection
ui/                     # Streamlit tab components (admin, KB, query builder, etc.)
i18n/                   # EN/PT translation strings
config/                 # App settings & DB connection defaults
integrations/          # ServiceNow SSO / session-capture experiments
reports/                # Batch processing & export helpers
data/                   # Mock/demo JSON seed data (safe to reset/inspect)
```

## 🌐 Disclaimer

This is a **sanitized, anonymized portfolio demo** derived from a private internal tool. It is **not affiliated with any specific employer**. Everything in this repository — user accounts, knowledge-base entries, shipment/error history, hostnames, and screenshots-worth of sample data — is **fictional and generated for demonstration purposes only**. Real company names, logos, credentials, internal hostnames, employee names, and proprietary business documents from the original project were removed and replaced with mock equivalents before publishing.
