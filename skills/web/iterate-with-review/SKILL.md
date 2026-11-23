---
name: iterate-with-review
description: Use when the user wants a website or landing page improved in autonomous rounds - each round the agent analyzes the page, makes changes and takes desktop and mobile screenshots, then reviewer subagents playing personas fitted to the site (by default two - the audience the site speaks to and someone else who influences the decision) judge the screenshots with fresh eyes, and the agent keeps the notes that make sense as input for the next round. Needs the site's source code (a local project or a git repo URL).
license: MIT
compatibility: Needs subagents and a way to screenshot a web page (the bundled script uses Python and Playwright; a browser MCP or headless Chrome also works). Git is optional.
argument-hint: "<project folder or repo URL> [rounds=3] [who the site is for] [personas]"
---

# Iterate with review

Improve a website in rounds without the user. Each round you change the site, reviewers who know nothing about your plans look at screenshots as specific people, and you keep the notes that make sense for the next round.

## Inputs

- **Target** - the site's source: a local project folder or a git repo URL (clone it). A live URL alone is not enough.
- **Rounds** - default 3.
- **Optional:** who the site is for (e.g. a customer description), own personas, number of personas (default 2).

Ask only if you cannot get the source or run the site. After that, go through all rounds without asking.

## Setup

1. Work folder `.iterations/` in the project root. In a git repo add `.iterations/` to `.git/info/exclude`, so it stays out of commits.
2. Run the site locally (the project's dev or preview script; for plain HTML any static server, e.g. `python -m http.server`).
3. **Personas.** If the user gave personas, use them. Otherwise read the site first (hero, headings, copy, calls to action, who the offer is for), add what the user said, and build personas for this site:
   - the audience the site speaks to;
   - someone who looks from another side and influences the decision: pays, approves or doubts. Only as an illustration: for a startup landing that may be an investor, for a toy shop a parent, for a B2B offer a head of department or an accountant.

   For each: who they are, what they already know, what they worry about, what decision they make on this site. Every fact comes from the site or the user; anything missing is written as a gap ("unknown: budget"), never guessed. Save to `.iterations/personas.md`, show the user 2-3 lines about them and start round 1 without waiting.
4. Baseline screenshots into `.iterations/round-0/`.

## Round N

Folder `.iterations/round-N/`.

1. **Analysis** -> `analysis.md`: what to change, why, how. Input in round 1: your own look at round 0 through the personas' eyes. From round 2: `decisions.md` from round N-1 and the latest screenshots; every accepted note gets a change or a reason to postpone it.
2. **Changes** in the source. Keep the stack, brand and facts. Never invent content: no made-up testimonials, clients, numbers or logos. Run the build and tests if the project has them.
3. **Screenshots** of the changed site into the round folder. Look at them yourself and fix anything your changes broke before you go on.
4. **Commit**, only in a git repo: stage the files you changed and make a normal commit `Round N: <what and why>`. No push, amend or `--no-verify`; if a hook fails, fix the problem and commit again.
5. **Review**: one subagent per persona, all in parallel, with the brief below. Save each answer verbatim as `review-<persona>.md`.
6. **Decisions** -> `decisions.md`: you, not a subagent, accept or reject every note with a one-line reason. Reject notes that need facts you do not have (keep them as questions for the user), clash with another persona's key need, or are taste without a reason. Accepted notes are the input for round N+1.

If a round accepts no notes, stop early and write the summary.

## Reviewer brief

A reviewer gets only its persona from `personas.md` and the list of screenshot paths. No analysis, plan, code, diff, earlier reviews or decisions, and no hint that the site was changed. Fresh eyes, no anchoring.

> You are <the persona's full entry from personas.md, gaps included>. You are seeing this website for the first time. Screenshots, top to bottom (desktop, then mobile): <paths>. Open only these files.
> Go through the page as this person. Where your description says something is unknown, do not make it up. Return:
> 1. In 2-3 sentences: what the site offers, whether you would <the persona's decision> and why.
> 2. Up to 10 concrete notes, most important first. Each: screenshot file and place, what is wrong or missing for you, why it matters to you.
> No compliments, no general advice.

## Screenshots

Desktop 1440 px wide and mobile 390 px wide, the whole page cut into screen-height pieces (one tall image gets scaled down until the text is unreadable), with reduced motion so animated sections are visible. Names `<page>-desktop-01.png`, `<page>-mobile-01.png`. Screenshot every page that matters for the personas, and key states hidden behind a click (a demo, tabs, a menu) as extra shots named after the state. Any tool works; the bundled script does the full-page part:

```
python "${CLAUDE_SKILL_DIR}/scripts/screenshots.py" URL OUT_DIR [--name home]
```

It needs `python -m pip install playwright` and `python -m playwright install chromium`. Look at a few shots before the review: empty or half-loaded sections are a screenshot problem, not a site problem.

## Summary

`.iterations/summary.md`: before and after (paths to round-0 and last-round screenshots), what changed in each round (with commit hashes in git), rejected notes with reasons, accepted notes the last round did not get to, and questions for the user. Tell the user the path and the main changes in 3-5 sentences.
