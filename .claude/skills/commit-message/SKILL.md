---
name: commit-message
description: >-
  Write a Conventional-Commit message (50/72 style) for the staged changes and
  create the commit. Use when the user asks to commit, to write or finalize a
  commit message, or says "commit this".
allowed-tools: Bash(git diff:*), Bash(git status:*), Bash(git log:*), Bash(git add:*), Bash(git commit:*), Read
---

# Commit message

Generate a Conventional Commit for the **staged** changes and create the commit, following the
format below exactly. The repo's `commit-msg` hook enforces this same format — write to it so the
commit is accepted on the first try.

## 1. Gather context (run these — don't assume)
- `git diff --staged --stat` — what's in scope
- `git diff --staged` — the actual change
- `git log -n 10 --oneline` — match this repo's subject style and scopes

If **nothing is staged**, stop and say so (offer `git add -p` or to stage specific files). Never
commit an unintended set.

## 2. Check atomicity
If the staged diff mixes unrelated concerns (e.g. a feature + an unrelated refactor + a dep bump),
say so and recommend splitting before continuing. One commit = one logical change.

## 3. Capture the "Why" — never hallucinate it
If the *why* behind a non-obvious decision (a chosen threshold, a tradeoff, a workaround) isn't
evident from the diff or this conversation, ASK the user for a one-sentence rationale first. Never
invent rationale, tradeoffs, or issue numbers.

## 4. Format (Conventional Commits + 50/72)
**Subject (line 1):** `type(optional-scope): description`
- Types: feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert
- Imperative mood, lowercase after the type, **no trailing period**
- Target **≤50 chars**, **hard cap 72** (the hook warns over 50, rejects over 72)
- Breaking change: `type!:` (e.g. `feat!:`) plus a `BREAKING CHANGE:` footer

**Line 2:** blank.

**Body (line 3+), only if it adds information:**
- Wrap at **72 chars**
- Explain the problem/previous state, the solution, and the tradeoffs/why —
  **what and why, not how** (the diff shows how). 1–3 short paragraphs or bullets.

**Footers (after a blank line):**
- `BREAKING CHANGE: …` if applicable
- `Refs: #123` / `Closes #123` if a real issue is known — never invent one

End with a blank line, then the trailer `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` to credit the assistant's contribution.

## 5. Commit & hand off
Show the proposed message, then create the commit with a heredoc to preserve formatting
(`git commit -F - <<'EOF' … EOF`). If on the default branch and the project uses feature branches,
branch first. Never push unless asked.

**Never merge PRs, and never ask to merge one.** The user reviews and merges every PR
themselves. After opening a PR, hand off the link and stop — do not offer to merge it.
