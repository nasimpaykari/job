# Unified Job Coach 6.0

Unified Job Coach is a local-first workspace for organizing a job search and preparing application materials. It supports academic and industry roles, public job-source searches, job tracking, document drafting, interview practice, reminders, and optional AI assistance.

This clean source package contains no saved user workspace, personal profile, account, API key, email credential, or bundled job database. New installations begin empty. LinkedIn is user-led only: search in LinkedIn Jobs and add a selected position manually.

## Features

- Search control for supported public job boards, employer or university career pages, and user-configured sources.
- Job cards with status, deadline, source links, profile fit evidence, filters, duplicate review, and ignore controls.
- Application document workspace with LaTeX, PDF and Word output, temporary preview, saved versions, position folders, and application packages.
- Interview preparation, application timeline, reusable contacts, reminders, and presentation planning.
- Optional AI providers, local Ollama support, reviewed coach memory, and user-controlled preferences.
- Optional browser extensions for reviewed job capture and limited form autofill. They do not submit applications.
- Local accounts, workspace sharing, versioned backups, and mobile-friendly web installation.

Search coverage depends on public site access and supported page formats. The app cannot guarantee that it finds every vacancy and does not bypass sign-in pages, CAPTCHAs, access controls, or site restrictions. Review jobs and generated materials yourself.

## Requirements

- Python 3.10 or newer.
- Internet access for the first setup to download Python packages. Network access inside the app is off by default.
- Windows, macOS, or Linux.

## Install and run

1. Download this repository as a ZIP or clone it.
2. Open the `job_coach` folder.
3. Start the launcher for your system:

   - Windows: double-click `start_windows.bat`.
   - macOS/Linux: run `sh start_mac_linux.sh` in a terminal.

The launcher creates a private `.venv`, installs `requirements.txt`, and starts the app. Keep the terminal open while using Job Coach. The app is bound to `127.0.0.1` by default and opens in your browser. If it does not, open the local setup URL printed in the terminal. Keep that URL private; it contains a one-time setup token.

Create the first owner account with a unique username and a password of at least 10 characters. Add your own profile and search settings after signing in. Do not copy a personal `data` folder into a public repository.

## Configure AI and privacy

AI is optional and disabled by default. In **Profile & settings**, choose a provider and model, enable AI/network access as needed, and provide your own key. Supported choices include OpenAI, Gemini, Claude, DeepSeek, and local Ollama, subject to the provider configuration in the app. No API keys are included in this repository.

When a hosted AI provider is enabled, the selected job text, profile facts, relevant memory, and document content may be sent to that provider to answer your request. Review the provider's terms and the information being sent. Local Ollama requests can remain on the computer after Ollama and a model are installed. Model downloads still require internet access and disk space.

Credentials and workspace data are stored under the configured data folder. Keep that folder and all backups private. The `.gitignore` excludes common local data and credential paths; check `git status` before pushing.

## Search and social networks

Add or select sources in **Search control**, review the saved plan, then run the search. Results are candidates for human review, not a guarantee of complete coverage. The app does not try to evade robots policies, authentication, rate limits, or anti-bot controls.

LinkedIn is manual-only in this release. Search through LinkedIn's Jobs interface, open the listing you want, and add it with **+ Add job**. Automated fetching, link screening, posting checks, and browser capture are blocked for LinkedIn pages. Other social-network feeds should be used only when the site's terms and the feed's access policy permit it.

## Documents and browser extensions

Choose a job and document type in **Application studio**. Preview changes first; preview files are temporary. **Save & approve** stores the reviewed PDF/source version in the workspace's position folder. Inspect the saved output and verify every claim before using it.

PDF output may need a local compiler or LaTeX packages. The app provides setup controls under **Profile & settings → PDF engine**. Online compilation, if selected and explicitly allowed, sends the document to the selected third-party service.

To load an extension in Chrome or Edge, open the browser's Extensions page, enable Developer mode, choose **Load unpacked**, and select either `browser_saver` or `autofill_extension`. The browser saver copies one reviewed public job page into the app; it is disabled on LinkedIn. Autofill is limited to supported contact fields on selected application sites. It does not upload documents, fill sensitive disclosures, solve CAPTCHAs, or submit applications.

## Email and shared hosting

Gmail and Outlook require the user to register an OAuth application with the provider and enter the client details in **Profile & settings**. Use the exact redirect URI shown by Job Coach when configuring the provider. Mail access is optional. Drafts and any sending action require review; messages are never sent automatically.

For access from another device, configure a reverse proxy with HTTPS and follow the deployment requirements in `serve_secure.py`. Do not expose Flask's development server directly to the public internet. The app is not a hosted service: your computer/server must stay running, and you are responsible for firewall, TLS, account, and backup configuration. The phone must use the server's HTTPS hostname, not `localhost`.

## Backups and upgrades

Use **Profile & settings → Data management → Back up now** to create a workspace snapshot. Snapshots do not include login accounts, API keys, email credentials, other users' workspaces, or some downloaded model files. Store them securely. A complete move that retains credentials and accounts requires stopping the app and copying the full private data folder plus any separate document root.

When upgrading, stop the app and scheduler, back up the active data folder and any external document root, extract the new release separately, and copy your existing data folder into the new `job_coach` folder. Do not copy `.venv`. Keep the old release and backup until you confirm your jobs and files are available.

An older compatible SQLite jobs database can be imported explicitly with:

```bash
python main.py --import-db "/path/to/your/old-jobs.db"
```

This imports job records only. It does not import user accounts, credentials, documents stored outside the database, or model files. Back up the destination first and keep the original database unchanged.

## Tests

From the `job_coach` folder, create an environment, install the application requirements and pytest, then run:

```bash
python -m pip install -r requirements.txt pytest
python -m pytest -q
```

Some browser UI tests require Node.js. Optional model-training and voice dependencies are listed separately in `requirements-training.txt` and `requirements-voice.txt`; they are not required to run the basic app.

## Project layout

- `coach/` — application services, data access, search, documents, accounts, and routes.
- `templates/` and `static/` — web interface and generic document starters.
- `browser_saver/` — reviewed job-page capture extension.
- `autofill_extension/` — limited, review-first form autofill extension.
- `tests/` — unit, integration, browser, and UI test helpers.
- `assets/fonts/` — bundled font assets and their license notice.

## Development acknowledgment and license

OpenAI tools were used as development assistance. The AI features in the application are configurable and are not an OpenAI-only service.
