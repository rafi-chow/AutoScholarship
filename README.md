# Scholarship Copilot

A local-first scholarship tracker for Rafi. It provides validated profile data, SQLite persistence, policy-gated manual/public imports, deterministic extraction and drafting, transparent ranking, and a Streamlit review dashboard.

An optional LLM provider remains deferred. Playwright autofill is review-first and fail-closed; the dashboard never enables submit mode and never automates university login.

## Safety defaults

- Never bypass CAPTCHA, 2FA, paywalls, robots.txt, Terms of Service, or anti-bot controls.
- Never fabricate GPA, citizenship, income, awards, service hours, identity, or eligibility.
- Treat all generated essays as drafts for human review.
- Never submit by default. The submit gate requires explicit submit mode, `pre_approved_submit: true`, an allowed source, and no login/CAPTCHA/2FA blocker. The dashboard always uses `submit_mode=False`.
- Keep Mav ScholarShop login, final review, and submission manual.
- `data/profile.yaml`, databases, generated drafts, and screenshots are git-ignored.

## Requirements

- Python 3.11 or newer
- macOS, Linux, or Windows

## Setup

Run these commands from this directory:

```bash
cd /Users/rafichowdhury/Developer/ScholarshipBot/scholarship-copilot
python3 --version  # must report 3.11 or newer
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
```

On Windows PowerShell, activate with `.venv\\Scripts\\Activate.ps1` instead.

The private `data/profile.yaml` is already normalized from the local `scholarship_context/` pack and points to its resume, transcript, story bank, and answers bank. It is excluded from Git. For a fresh clone, create it from the safe template:

```bash
cp data/profile.example.yaml data/profile.yaml
```

Then edit only verified facts. Document paths are resolved relative to the profile YAML file.

Draft generation also requires these private local context paths:

```text
data/profile.yaml
data/story_bank.md
data/scholarship_answers_bank.md
data/bot_context.md
```

They are ignored by Git. In this workspace the Markdown files point to the existing `scholarship_context/` pack; on another machine, copy or link the private files into `data/` yourself.

## Run the dashboard

```bash
cd /Users/rafichowdhury/Developer/ScholarshipBot/scholarship-copilot
source .venv/bin/activate
python -m streamlit run src/dashboard.py
```

The dashboard creates `data/scholarships.db` on first run and opens locally, normally at `http://localhost:8501`.

## End-to-end workflow

Start the app:

```bash
cd /Users/rafichowdhury/Developer/ScholarshipBot/scholarship-copilot
source .venv/bin/activate
python -m streamlit run src/dashboard.py
```

Then use these exact dashboard actions:

1. Manual import: open **Import scholarships → Paste opportunity text**, paste one opportunity, then click **Extract and import**.
2. Mav ScholarShop: log in yourself, copy one opportunity, open **Mav ScholarShop manual check**, paste it, then click **Parse, rank, and import**.
3. Drafts: open a scholarship's **Essay drafts**, then click **Generate draft**. Review **Facts used** and **Missing user input**.
4. Safe autofill: ensure the application URL is covered by a reviewed source in `data/sources.yaml`, then click **Open and autofill safely**. Complete login yourself if requested and stop for CAPTCHA/2FA. Review the log, screenshot, and manual fields. This dashboard button never submits.
5. Exports: open sidebar **Exports** and select the tracker, weekly list, draft packet, quick-apply queue, or Mav checklist. Use **Export application packet** on a scholarship card for its complete packet.

Use **Import scholarships** at the top of the dashboard to paste one complete opportunity or fetch an approved public URL. Manual parsing extracts amounts, deadlines, eligibility flags, documents, prompts, restrictions, and links without an LLM. Imported records are saved and ranked immediately.

Each saved prompt has controls to generate, view, and classify a draft. Draft Markdown is written to `drafts/{scholarship_slug}/{prompt_slug}.md` and contains short/long variants, a facts-used checklist, claims to verify, missing input, and the reason for the selected story. Family/immigration prompts stop for exact user context; financial prompts use cautious education-cost language.

The Mav ScholarShop tab is manual-only: log in yourself, paste one opportunity, import and rank it locally, generate drafts, then submit manually after review.

Public URL import is fail-closed. `data/sources.yaml` ships with conservative manual/blocked examples; review a source's current Terms of Service and robots policy before changing its `access_mode` to `public_allowed`. A fetch must remain under the configured source URL/path, including after redirects.

## Safe autofill behavior

Common labels are configured in `data/form_mappings.yaml`. Autofill fills only unique, high-confidence matches backed by verified profile values. Parent/guardian/reference fields, missing values, ambiguous labels, file uploads, passwords, and unknown fields remain blank and appear under **Manual fields needed**.

Each run writes a masked JSON audit log under `autofill_logs/` and a screenshot under `screenshots/`. Browser session data stays under `data/browser_profile/`. These paths are local and git-ignored, and logs do not contain raw filled values.

If login is detected, the headed browser pauses for manual login. CAPTCHA, 2FA, paywalls, and anti-bot protections stop the run. Nothing attempts to evade them. Mav ScholarShop and university portals remain manual-login/manual-submit workflows.

The dashboard does not expose submit mode. The lower-level gate permits a final click only when all four conditions are true:

- the caller explicitly uses submit mode;
- the scholarship has `pre_approved_submit: true`;
- source policy permits submission;
- no login, CAPTCHA, 2FA, paywall, or anti-bot blocker exists.

## Exports and weekly planning

Generated files stay under the git-ignored `exports/` directory:

- `scholarship-tracker.csv`
- `weekly_action_list.md`
- per-scholarship application packets
- `all-drafts-packet.md`
- `quick-apply-queue.csv`
- `mav-scholars-shop-weekly-checklist.md`

The dashboard weekly panel shows the top five high-fit opportunities, deadlines within 14 days, quick applications, ready drafts, document needs, missing information, and the Mav ScholarShop reminder.

## Automated discovery

Discovery runs independently of Streamlit. It checks enabled `public_allowed` sources, configured RSS feeds, and optional search API results; applies source and robots policy before every request; follows only allowed scholarship-detail links; extracts and ranks results; merges normalized name/URL duplicates; and retains every source reference.

Run it once:

```bash
cd /Users/rafichowdhury/Developer/ScholarshipBot/scholarship-copilot
source .venv/bin/activate
python -m src.scheduler run-once
```

The supported commands are:

```bash
python -m src.scheduler run-once
python -m src.scheduler discover
python -m src.scheduler weekly-action-list
```

`run-once` performs discovery, writes both summaries, and sends an email only when SMTP is configured. `discover` performs the same local discovery without email. It writes:

```text
exports/latest_discovery_summary.md
exports/weekly_action_list.md
```

Curated and RSS discovery work without an API key. Optional search supports `google`, `serpapi`, `tavily`, or `bing`:

```dotenv
SEARCH_PROVIDER=serpapi
SEARCH_API_KEY=replace_me
# GOOGLE_CSE_ID=required_only_for_google
```

Without a key, the dashboard shows setup instructions and continues with curated/RSS sources.

### Schedule weekly with cron

Run `crontab -e`, then add this Sunday 9:00 AM job as one line:

```cron
0 9 * * 0 cd /Users/rafichowdhury/Developer/ScholarshipBot/scholarship-copilot && /Users/rafichowdhury/Developer/ScholarshipBot/scholarship-copilot/.venv/bin/python -m src.scheduler run-once >> /Users/rafichowdhury/Developer/ScholarshipBot/scholarship-copilot/exports/discovery.log 2>&1
```

For nightly discovery at 2:00 AM, replace the schedule with `0 2 * * *`.

### Schedule weekly with a macOS LaunchAgent

Create `~/Library/LaunchAgents/com.rafi.scholarship-copilot.plist` containing:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.rafi.scholarship-copilot</string>
  <key>ProgramArguments</key><array>
    <string>/Users/rafichowdhury/Developer/ScholarshipBot/scholarship-copilot/.venv/bin/python</string>
    <string>-m</string><string>src.scheduler</string><string>run-once</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/rafichowdhury/Developer/ScholarshipBot/scholarship-copilot</string>
  <key>StartCalendarInterval</key><dict>
    <key>Weekday</key><integer>0</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer>
  </dict>
  <key>StandardOutPath</key><string>/Users/rafichowdhury/Developer/ScholarshipBot/scholarship-copilot/exports/discovery.log</string>
  <key>StandardErrorPath</key><string>/Users/rafichowdhury/Developer/ScholarshipBot/scholarship-copilot/exports/discovery-error.log</string>
</dict></plist>
```

Load it:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.rafi.scholarship-copilot.plist
```

The **Discovery** dashboard tab shows run counts, warnings, search status, and top new matches. The **Sources** tab shows access mode and fetch state and lets you enable or disable a source without changing its safety policy.

## Autopilot and approval queue

Run the complete non-browser pipeline with one command:

```bash
cd /Users/rafichowdhury/Developer/ScholarshipBot/scholarship-copilot
source .venv/bin/activate
python -m src.autopilot run
```

Autopilot runs discovery, extraction, deduplication, ranking, draft generation for new Apply Now opportunities, and queue exports. No-essay opportunities remain in Quick Apply and are ordered first as low-effort, low-expected-value items. It writes:

```text
exports/latest_autopilot_summary.md
exports/approval_queue.md
exports/quick_apply_queue.md
exports/weekly_action_list.md
```

Use the dashboard **Approval Queue** to review the draft preview, facts, claims, missing input, documents, and risk flags. **Approve for autofill** permits prepare mode. **Approve for safe submit** is disabled until all stored checks pass and records explicit submit approval; the live browser checks can still veto submission.

Prepare an approved scholarship without submitting:

```bash
python -m src.autofill --scholarship-id ID --mode prepare
```

The optional explicit submit command is:

```bash
python -m src.autofill --scholarship-id ID --mode submit-approved
```

`prepare` is the default. `submit-approved` fails closed unless the scholarship and autofill approvals exist, the source separately sets `allow_submit_automation: true`, every requirement flag is explicitly safe, all drafts are reviewed with no missing input or unsupported markers, the application URL matches policy, no manual fields remain, and the live page has no login/CAPTCHA/2FA/paywall/anti-bot blocker. Public fetch permission alone never grants submit permission.

## Run tests

```bash
cd /Users/rafichowdhury/Developer/ScholarshipBot/scholarship-copilot
source .venv/bin/activate
python -m pytest -q
```

## Current structure

```text
scholarship-copilot/
├── AGENTS.md
├── README.md
├── requirements.txt
├── .env.example
├── data/
│   ├── profile.example.yaml
│   ├── profile.yaml              # private, local, ignored
│   ├── sources.yaml
│   ├── form_mappings.yaml
│   └── search_queries.yaml
├── src/
│   ├── models.py
│   ├── db.py
│   ├── profile.py
│   ├── ranker.py
│   ├── finder.py
│   ├── extract.py
│   ├── drafter.py
│   ├── mav_import.py
│   ├── autofill.py
│   ├── export.py
│   ├── discovery.py
│   ├── scheduler.py
│   ├── notifications.py
│   ├── source_adapters/
│   ├── dashboard.py
│   ├── policy.py
│   └── future LLM provider module
├── drafts/
├── screenshots/
├── autofill_logs/
├── exports/
└── tests/
```

## Ranking model

The deterministic score is `45% fit + 15% effort + 15% urgency + 15% amount + 10% competition`. Fit strongly favors UTA, Texas/DFW, computer science, software, STEM, engineering, aerospace, Bell-adjacent, undergraduate, and local opportunities. Explanations and component scores are stored with each ranking.

Known hard conflicts—such as a passed deadline, explicit unmet GPA minimum, first-generation-only requirement, incomplete required FAFSA, recommendation requirement, or award below the configured minimum—force `Skip`. FAFSA and recommendation rules support explicit manual overrides. Strict need-only awards receive a score penalty instead of a fabricated hardship claim. No-essay/lottery opportunities become `Quick Apply` unless a hard conflict applies. Ambiguous eligibility remains a manual review item rather than being guessed.
