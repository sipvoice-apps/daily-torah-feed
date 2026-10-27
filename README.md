# Daily Learning Feed

A JSON Feed and RSS feed that GitHub rebuilds every 8 hours from:

| Source | What goes into the feed |
|---|---|
| [Kitzur Shulchan Aruch – Wikisource](https://he.wikisource.org/wiki/קיצור_שולחן_ערוך) | One siman a day with the full Hebrew text, cycling סימן א → רכא and then starting over |
| [Rambam – Chabad.org](https://www.chabad.org/dailystudy/rambam_cdo/jewish/1-Chapter-Rambam.htm) | The day's 1-chapter Rambam, with its title and a link |
| [CCHF Daily Companion](https://cchf.global/daily-companion/) | New Daily Companion posts with their full text |

Once it's live, the feeds are at:

- RSS: `https://sipvoice-apps.github.io/daily-torah-feed/feed.xml`
- JSON: `https://sipvoice-apps.github.io/daily-torah-feed/feed.json`
- Web page: `https://sipvoice-apps.github.io/daily-torah-feed/`

## One-time setup (about 5 minutes)

1. **Create the repo.** On GitHub, create a new **public** repository named `daily-torah-feed`. (Pages on a private repo needs a paid plan.)
2. **Upload the files.** Click **Add file → Upload files** and drag in everything from this folder, including the hidden `.github` folder. On a Mac, press Cmd+Shift+. in Finder to show hidden folders. Commit.
3. **Set your URL.** In `config.json`, replace `YOUR-GITHUB-USERNAME` with your GitHub username and commit.
4. **Turn on Pages.** Go to **Settings → Pages → Build and deployment**, choose **Deploy from a branch**, then set Branch to `main` and Folder to `/docs`. Save.
5. **Run it once.** Go to **Actions → Build daily feed → Run workflow**. After it finishes, your `docs/` folder will contain the feed files, and the Pages URL will be live within a minute or two.

After that, GitHub runs the feed every 8 hours by itself.

## Settings (`config.json`)

- `kitzur.start_date` / `kitzur.start_siman`: the siman learned on the start date. By default, Oct 27, 2026 is סימן א. To join an existing cycle, set today's date and the siman you're on.
- `history_days`: how many days of items stay in the feed (default 30).
- `cchf.posts_per_run`: how many recent Daily Companion posts are checked on each run.
- `timezone`: decides when "today" begins (default America/New_York).
- Set `"enabled": false` on any source to turn it off.

## Notes

- If a site is down or blocks a run, that source is skipped and its earlier items stay in the feed. The next run tries again.
- Chabad.org sometimes blocks cloud servers. When that happens, the Rambam title comes from Sefaria's daily calendar instead, and the item still links to Chabad.org.
- GitHub pauses scheduled workflows on repos with no activity for 60 days. The feed's own commits normally keep the repo active, but if the workflow is paused, click **Enable workflow** under Actions.
- To test locally, run `pip install -r requirements.txt && python build_feed.py`.
