---
name: bilibili-arrangement3
description: Collect Bilibili (and optionally YouTube) videos from saved follows, ad-hoc UP/video links, and keyword searches, then turn the candidate pool into a weekly plan with freshness, diversity, a 14-day no-repeat window, and follow-along-specific filtering for yoga, workout, or healing-music schedules.
---

# Bilibili Arrangement 3

Use this skill when the user wants to collect Bilibili content into a usable weekly plan rather than a loose search result.

## Environment

All CLI commands must be run from the project root: `$BILI_ARRANGEMENT3_ROOT` (default: the directory containing `src/`, `config/`, and `outputs/`).
The installed entry-point is `bili-arrangement3`. If not installed, prefix every command with `PYTHONPATH=src python3 -m bili_arrangement3`.

## Workflow

1. If scope is still unclear, read `references/onboarding.md` and ask the user to choose the short menu.
2. Check user memory first: read `data/user_memory.json` if it exists. It holds one rolling log of past sessions; use only the entries whose `topic` matches this request and whose `date` falls inside `context_window_days`, newest first, to pre-fill request parameters. Never carry a note across topics — a yoga body constraint does not apply to a music arrangement.
3. Use the local CLI from the project root:
   - `bili-arrangement3 plan`
   - `bili-arrangement3 plan --offline`
   - `bili-arrangement3 sync`
   - `bili-arrangement3 brief --run-dir ...`
   - `bili-arrangement3 draft --run-dir ...`
4. Treat saved sources as persistent follows in `config/sources.json`.
5. Combine saved follows, temporary UP/video links, and the user's own keywords when the scope allows it.
6. Do not treat UP-name search as latest-content ranking. UP-name search only resolves identity; the latest posts should come from the space API, with keyword search as fallback.
7. When the topic is yoga follow-along, exclude intro/FAQ/explanation-style videos such as 档位介绍、练习答疑、课程说明、合集介绍、UP 自述、vlog, even if they carry yoga tags.
8. Exclude commercial and restricted videos by default.
9. Use `references/planning_prompt.md` to turn the candidate pack into the final AI-led arrangement. Prioritize topic match, freshness, diversity, and two-week dedupe, in that order unless the user says otherwise.
10. Before writing the final reply, read the `OUTPUT_FORMAT` block in `references/planning_prompt.md` and follow it exactly. That block is the only place the reply format is defined; this file deliberately does not restate it, and each run's `outputs/runs/<run-id>/prompt.md` embeds the same block verbatim.
