# GitHub Publish Checklist

## Before Publishing

- [ ] Rotate the old TTFund API key if it was ever committed, copied into notes, or shared.
- [ ] Confirm a repository-wide secret scan returns no API keys or live-key prefixes.
- [ ] Confirm `.env` is not staged.
- [ ] Confirm generated `output/` files are not staged.
- [ ] Run Python compile checks.

## Create the Repository

Suggested repository name:

```text
fund-etf-dashboard
```

Recommended GitHub settings:

- Visibility: Public
- Default branch: `main`
- Add topics: `fund`, `etf`, `dashboard`, `quant`, `python`, `finance`, `open-source`
- Enable Issues
- Enable Discussions if you want user feedback

## Push

```powershell
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/fund-etf-dashboard.git
git push -u origin main
```

## After Publishing

- [ ] Add a screenshot to the README.
- [ ] Create release `v0.1.0`.
- [ ] Open roadmap issues for NAV caching, tests, Docker, sample data, and UI accessibility.
- [ ] Share the repository with a small group for feedback.
- [ ] Submit the Codex for OSS application form.
