# Updating the website (bolding-bruggeman.com/oceanicu_3d)

Two separate Python scripts, two separate repos. Both require `--apply` —
without it, they print help and do nothing (a deliberate confirmation gate,
not a dry-run preview).

```
1. GENERATE   ./regenerate_hugo.py --apply      (this repo, oceanicu_3d)
2. DEPLOY     ./deploy_ghpages.py --apply       (ocean-post repo)
```

Run them in that order, from anywhere — both auto-relay over ssh to
**bb-server1** if you're not already there, since that's where the real
analyses data and Hugo content live. You do not need to `ssh` yourself first.

## What each one actually does

**`regenerate_hugo.py --apply`** (this repo's root) runs
`ocean-post`'s `cli.reporting` on bb-server1, reading real analyses data
from `/data/OceanICU/oceanicu_3d/analyses/` and writing generated Hugo
content directly into the real, persistent
`/data/OceanICU/oceanicu_3d/{content,static}/` directories. That's it —
one subprocess call, no temp-dir indirection. Nothing is public yet at
this point; this only updates the *source* the next deploy will build from.

**`deploy_ghpages.py --apply`** (in `ocean-post`, run from anywhere —
it also relays to bb-server1) builds the Hugo site from
`~/source/repos/OceanICU/oceanicu_3d/hugo/` (which reads the content
`regenerate_hugo.py` just wrote) into its own **throwaway temp directory**,
then checks out `gh-pages` into a **second, separate temp git worktree**,
copies the build in, commits, and force-pushes. This is real, public, and
hard to reverse (`gh-pages` is always fully replaced).

## The one gotcha that matters most: `content/areas/nse-boundaries.md` (and similar pages) are CODE-GENERATED

**Never hand-edit a file under `content/` or `static/` directly.**
Every page under `content/areas/` is written from scratch by
`ocean-post/lib/reporting.py` every time `regenerate_hugo.py --apply` runs
— e.g. `_generate_nse_boundaries_page()` produces
`content/areas/nse-boundaries.md` entirely from hardcoded Python string
literals plus plot/table files discovered under `analyses/areas/NSe/...`.
There is no free-text passthrough. A manual edit to the `.md` file
survives exactly until the next regeneration, then is silently gone with
no warning (confirmed the hard way, 2026-09-21).

**If you want new prose/tables/sections on one of these pages, edit the
generator function in `ocean-post/lib/reporting.py`, not the output.**
Commit + push there, `git pull` on bb-server1, then regenerate.

## A directory that looks relevant but isn't: `/data/OceanICU/oceanicu_3d/public`

The Hugo config's own `publishDir` points here
(`hugo/config.bbserver1.yaml`), so it's easy to assume this is what gets
deployed. It isn't — `deploy_ghpages.py` always passes an explicit
`--destination <tempdir>` on the `hugo` command line, which overrides
`publishDir` entirely. `public/` is only ever touched by someone manually
running plain `hugo` from the `hugo/` directory (e.g. for a quick local
look), and can sit stale indefinitely without affecting what's actually
live. Don't use its mtime as a signal for anything.

## Sanity-check before deploying

`regen_hosts.yaml`'s `enabled_areas` is a publish **allowlist** — an area
not listed there gets its already-published pages actively *removed* by
the next regeneration, not just skipped. Check that list before assuming
"regenerate everything" is safe, and don't chain regenerate → deploy
blindly; look at what actually changed first.

## A real bug this surfaced (2026-09-21), in case it recurs

`ocean-post/lib/reporting.py`'s `_generate_scenarios_page()` crashed with
`AttributeError: type object 'BCDiagnosticsLayout' has no attribute
'iter_all_plot_dirs'` on every regeneration since 2026-06-23 (commit
`28abad5`, which moved `BCDiagnosticsLayout` to `ocean-prep` and dropped
4 reporter-side directory-walking methods that `reporting.py` still
called). It went unnoticed for three months because the crash happens
*after* other pages (like nse-boundaries) already wrote successfully —
so per-page output looked fine while the overall script silently exited
non-zero the whole time. Fixed by restoring the missing walkers as local
functions in `reporting.py` (commit `e48c882`). If a similar
"AttributeError on some Layout class" ever reappears after a refactor in
`ocean-prep`, check `git log -p -- lib/layout.py` in `ocean-post` first —
it's likely the same class of drift.
