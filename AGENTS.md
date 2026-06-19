# Scholarship Copilot Repository Instructions

These instructions apply to the entire repository.

## Engineering priorities

- Prioritize useful, working code and a clear user workflow over abstractions, speculative infrastructure, or overengineering.
- Make the smallest coherent change that solves the current task. Keep modules readable and behavior explicit.
- Preserve the local-first architecture: SQLite and local files are the default stores, and external services are optional.
- Maintain compatibility with Python 3.11 or newer.
- Add or update tests whenever ranking, extraction, source-adapter, or database behavior changes. Run the relevant tests before handing work back.

## Privacy and factual accuracy

- Keep personal data local and collect only what the workflow needs.
- Never commit secrets, `.env`, API keys, databases containing personal data, transcripts, resumes, tax records, identity documents, or other personal documents.
- Store API keys and service credentials only in `.env`; keep placeholders in `.env.example`.
- Treat `data/profile.yaml`, `private/`, generated drafts, and screenshots as private local artifacts.
- Never fabricate or infer GPA, citizenship, residency, income, FAFSA status, awards, service hours, identities, hardships, eligibility, or application answers. Use verified profile facts or mark the field as needing user input.

## Automation boundaries

- Never build or use CAPTCHA bypass, 2FA bypass, fake accounts, paywall bypass, fingerprint evasion, stealth scraping, or anti-bot circumvention.
- Every scraper or source adapter must consult `src/policy.py` before making requests. Check the source's Terms of Service and robots policy, record the decision, and fail closed when access is prohibited or unclear.
- Rate-limit allowed requests, identify the client honestly where appropriate, and prefer configured public feeds or manual import.
- Never auto-submit a scholarship application by default. Submission is allowed only when the scholarship has `pre_approved_submit: true` **and** `src/policy.py` confirms the site is allowed. Both conditions are mandatory.
- Autofill must log every filled field, stop before final submission, and save a review screenshot unless the two explicit submission conditions above are satisfied.
- For Mav ScholarShop and all university or financial-aid portals, support only manual import, ranking, drafting, copy/paste help, and assisted autofill. Do not automate login or blind submission.

## Drafting requirements

- Mark every generated essay or short answer as a draft requiring human review.
- Every draft must include a **Facts used** checklist that maps material claims to verified profile facts.
- Also surface claims needing verification and missing user input. Never quietly fill gaps with plausible-sounding details.
- Follow the configured voice and word limit without exposing private employer details or unrelated sensitive information.
