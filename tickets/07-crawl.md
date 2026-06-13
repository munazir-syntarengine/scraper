# 07 — Shallow Brand Crawl

**Implements:** extends spec §1/§3/§4 (single-page → shallow multi-page); stays within §6 scope (same-domain, no SSRF/evasion, no deep crawl)
**Status:** done

## Goal
Extend the scraper from a single page to a shallow brand crawl: the homepage
plus the single best-matching **about** page and the single best-matching
**products/services** page (max 3 pages total). No hardcoded URLs — pages are
discovered by scoring the homepage's own links.

## Approach
1. Render the homepage via the existing render+extract path.
2. From the homepage, collect all **same-domain** links with their `href` AND
   visible link text.
3. Score links against two pattern groups, matching on **both** path and link
   text (case-insensitive) so naming variations are caught:
   - **About:** about, story, who-we-are, who-we-our, what-we-do, our-mission,
     mission, philosophy, team, company, our-story
   - **Products/services:** product, products, service, services, shop, store,
     collection, collections, what-we-offer, solutions, menu, pricing
   Pick the single highest-scoring match per group. No match → skip the group
   (don't fail).
4. Render + extract each selected page. Homepage + up to 2 = **hard cap of 3**.
5. Merge into the existing contract:
   - **text:** pool from all pages, keeping readable per-page separation/headers.
   - **colors:** merge across pages by frequency, top 3–5 overall.
   - **fonts:** take from the homepage (the hero defines the brand); fall back
     to other pages only for a role the homepage didn't yield.
6. Add the list of pages actually crawled to the response (`pages: [{role,url}]`)
   so the frontend can show "read from N pages: home, about, products".
7. **Per-page best-effort:** any page that blocks / times out / 404s is skipped;
   the crawl continues with what succeeded. (Homepage failure still errors the
   whole request — there's nothing to read.)

## Scope guards (unchanged from spec §6)
- Same registrable domain only — no external links (no twitter, no CDNs).
- No SSRF / URL validation, no bot-evasion.
- No depth beyond these named pages; no link-following from sub-pages.

## Frontend
- Update only the `.src-line` to list the pages read (e.g. "read from
  mailchimp.com · 3 pages: home, about, products · rendered with headless
  Chromium"). Per-page text already renders via the existing `## ` heading
  convention — no text-panel parser change needed.

## Acceptance criteria
- [ ] On a real brand site, the homepage's own links are scored and the best
      about + products pages are selected (no hardcoded URLs).
- [ ] At most 3 pages are rendered; a group with no match is skipped cleanly.
- [ ] A sub-page that fails (block/timeout/404) is skipped; the crawl still
      returns the homepage + whatever else succeeded.
- [ ] Response includes `pages` (role + url); merged colors are top 3–5 across
      all pages by frequency; fonts come from the homepage (fallback otherwise);
      text is pooled with readable per-page separation.
- [ ] Only same-domain pages are crawled.
- [ ] Frontend `.src-line` shows the pages read.
- [ ] Verified on mailchimp.com and stripe.com (pages found/crawled + merged
      result + total time shown).
