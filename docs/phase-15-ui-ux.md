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

Firecrawl is useful when Vitae needs live web data, such as scraping dynamic job pages, crawling job-board sections, or interacting with pages that require browser rendering. Its current API supports search, scrape, crawl and browser interaction. citeturn0search2turn0search5

Phase 15 is a UI/UX phase, so there is no Firecrawl call in the application runtime here. Adding it only to redesign templates would add an unnecessary dependency. The appropriate integration point remains the job-source/data pipeline, where it can be introduced behind the existing source adapter interface when a source needs browser rendering or structured extraction.

## Acceptance criteria

- [x] Profile page has clear active-track and CV sections.
- [x] CV replacement keeps version history visible.
- [x] Application status navigation is keyboard accessible.
- [x] Auto-apply safety rules are visible before batch creation.
- [x] Auto-apply detail exposes prepared/submitted/failed counts.
- [x] Mobile layouts stack profile, application and automation controls.
- [x] Existing routes/forms are preserved.
- [ ] Full CI suite passes on GitHub Actions.
