# Phase 15: UI/UX Redesign

## Goal

Make the product easier to understand and faster to use without changing the core domain workflows.

## Completed

- Added a consistent Phase 15 visual layer for career profiles, CV management, applications and auto-apply.
- Redesigned the profile page around the active career track and current CV.
- Added a clearer CV replacement area with version history.
- Changed profiles from dense rows into responsive cards.
- Redesigned application status filters as accessible status tabs.
- Redesigned application records as clear cards with status and next action.
- Redesigned auto-apply creation with a visible safety workflow.
- Redesigned auto-apply history and review items.
- Added batch progress stats to auto-apply detail.
- Added a skip-to-content accessibility link.
- Added a stylesheet cache-bust so the new UI is loaded after deployment.
- Kept the existing server-rendered Jinja approach. No frontend framework was added.

## UX rules

1. Primary action must be visible without scanning the whole page.
2. Status must be readable from text and not only colour.
3. Destructive actions stay visually secondary.
4. CV history remains visible and previous versions are not removed by replacement.
5. Auto-apply must make the human review boundary clear.
6. Mobile layouts must stack actions instead of forcing horizontal scrolling.
7. Existing endpoints and form actions remain unchanged.

## Firecrawl

Firecrawl is useful when Vitae needs live web data, such as scraping dynamic job pages, crawling job-board sections, or interacting with pages that require browser rendering. Its current API supports search, scrape, crawl and browser interaction. See https://www.firecrawl.dev/ and https://www.firecrawl.dev/crawl.

Phase 15 now also adds Firecrawl as an optional open-web job discovery source. It is enabled in the source configuration and isolated behind the existing source orchestration boundary. Set `FIRECRAWL_API_KEY` before running catalogue syncs. Firecrawl complements, rather than replaces, structured APIs and company ATS adapters. Its Search API can discover fresh web pages and return scraped page content, while Scrape/Crawl/Interact can be used later for source-specific extraction or dynamic workflows. citeturn0search1turn0search3turn0search6

## Acceptance criteria

- [x] Profile page has clear active-track and CV sections.
- [x] CV replacement keeps version history visible.
- [x] Application status navigation is keyboard accessible.
- [x] Auto-apply safety rules are visible before batch creation.
- [x] Auto-apply detail exposes prepared/submitted/failed counts.
- [x] Mobile layouts stack profile, application and automation controls.
- [x] Existing routes/forms are preserved.
- [x] Firecrawl open-web discovery adapter is registered in the source pipeline.
- [x] Firecrawl is isolated as a source and does not replace existing providers.
- [x] Firecrawl configuration and API-key status are supported.
- [x] Firecrawl helper tests are included.
- [ ] Full CI suite passes on GitHub Actions.
