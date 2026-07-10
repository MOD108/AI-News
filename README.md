# The Wire — self-updating AI news briefing

A static site that rebuilds itself daily via GitHub Actions:
RSS feeds → dead-link check → Gemini summaries & tags → `index.html` → GitHub Pages.

## Setup (~10 minutes)

1. **Create a repo** on GitHub (public, or private with Pages enabled on a paid plan)
   and upload everything in this folder, keeping the `.github/workflows/` path intact.

2. **Add your Gemini key** — repo → Settings → Secrets and variables → Actions →
   *New repository secret* → name it `GEMINI_API_KEY`. 
   (Optional: without it the site still builds using raw feed text and keyword tagging.)

3. **Allow the workflow to push** — Settings → Actions → General →
   Workflow permissions → select **Read and write permissions** → Save.

4. **Run the first build** — Actions tab → *Build The Wire* → *Run workflow*.
   This generates `index.html` and `state.json` (issue counter).

5. **Turn on Pages** — Settings → Pages → Source: *Deploy from a branch* →
   Branch: `main`, folder `/ (root)` → Save. Your site appears at
   `https://<username>.github.io/<repo>/` within a couple of minutes.

From then on it rebuilds every morning on the cron schedule and redeploys itself.

## Customise

| What | Where |
|---|---|
| Feeds | `feeds.txt` — one URL per line |
| Site name, tagline, story counts | Top of `build.py` |
| Schedule | `cron` line in `.github/workflows/build.yml` |
| Design | `template.html` (never edit `index.html` — it's overwritten) |
| Gemini model | `GEMINI_MODEL` in `build.py` |

## Preview locally without internet

```
python build.py --demo
```

Writes an `index.html` with sample stories so you can check the design.

## Notes & honest caveats

- **Verify the feed URLs** in `feeds.txt` — publishers move them. A dead feed is
  skipped with a warning, not fatal.
- **Check the Gemini model name** (`gemini-2.0-flash`) against Google's current
  docs; model names change and I can't guarantee it's still current.
- **Link checking** drops URLs that don't return HTTP < 400. Some sites block
  bots, so occasionally a live link gets dropped or a soft-404 slips through —
  it's a good filter, not a perfect one.
- **AI summaries can be wrong.** The prompt forbids inventing facts beyond the
  feed snippet, but no prompt makes that impossible. If this becomes a public
  brand, skim each issue before sharing it.
- GitHub Actions free tier (2,000 min/month) covers a daily ~1-minute build
  many times over.
