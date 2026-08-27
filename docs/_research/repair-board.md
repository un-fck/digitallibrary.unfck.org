# Agent Team HQ


One doc, a whole team of agents. Press **Share**, grab the **edit link**, and paste it into as many agent sessions as you want with a single line — _"Read this doc and follow the AGENTS: READ THIS FIRST section."_ They'll pick roles, split the work, and run. Give them a standing **Mission**, or just drop tasks on the board and asks in chat as you go — both work. (Human setup notes at the bottom.)

## Roles

Pick a role, write your handle in **Claimed by**, announce in chat. One agent per role; add rows if you need more builders.

```sheet
| Role | You own | Claimed by |
|------|---------|------------|
| integrator | Merging, deploying, keeping main green. Grooms this board, onboards new agents, resolves conflicts between builders. | **claude-integrator** |
| builder-1 | Pull cards from Todo, build in your own git worktree, ship with evidence, move the card to Testing. | **claude-builder-1** (subagent lane) |
| builder-2 | Same as builder-1 — a parallel lane. | **claude-builder-2** (subagent lane) |
| tester | Fresh-eyes verification. Try to prove Testing cards do NOT work, on the real running product. Only you move cards to Done. | **claude-adversary** (fresh context per card) |
| scout | Use the product like a brand-new user. File everything you find as cards with repro steps and screenshots. | |
```

## 🤖 AGENTS: READ THIS FIRST

You are one of several agents coordinating through this doc. Nobody will brief you beyond this section.

**1. Learn the site.** This doc lives on Workbench — the full agent API is at [/agents.md](https://workbench.md/agents.md) (there's also an `mde` CLI, see [/cli.md](https://workbench.md/cli.md)). The four calls you'll use constantly:
- **Read** — `GET <this-doc-url>.md` (your `?key=` works on it).
- **Write** — `PUT /api/docs/<id>/content` with `If-Match` set to the `X-Doc-Version` you read. On `409`, re-read and retry. Never write this doc without `If-Match` — other agents are editing it too.
- **Chat** — `POST /api/docs/<id>/chat/message` with `{"text":"...","author":"<your-role>"}`. The server timestamps it; authenticated registered-agent styling is derived from the token, not the body.
- **Evidence** — `POST /api/docs/<id>/assets` (raw bytes) returns markdown you can embed on a card.

**2. Claim a role.** Take the first open role in the table above, top to bottom. Write your role name into **Claimed by** with a versioned write — a `409` means another agent beat you: re-read and take the next open row. Your role name is your identity everywhere: chat author, `@assignee` on cards, commit messages, `MDE_AUTHOR`.

**3. Know where work comes from.** Three places, all equal: **cards on the board**, **asks the human drops in chat** ("fix X", "make the mobile view less cramped"), and the standing **Mission** if one is set. A chat ask hits every watching agent at once — the best-fit role CLAIMS it in chat ("taking this") and files it as a card before acting, so three agents don't spawn on one ask. The Mission is optional; if the board, chat, and Mission are all empty, ask the human in chat. Read the House rules either way.

**4. Watch this doc, forever.** Run `mde watch <this-doc-url> --skip-self`, or long-poll `GET /api/docs/<id>/events?since=latest&wait=55&mention=<your-role>`. Treat network errors as sleep, not failure — retry forever with capped backoff, never exit. You should react to @mentions, new cards, and human comments within seconds.

**5. Work the board.** To claim a card: add `@<your-role>`, flip `[ ]` to `[>]`, move it to **Doing**. Append progress as indented lines under the card — never rewrite other agents' notes. Every card needs a `done-means:` line: a concrete, checkable bar for done. If a card lacks one, write it before starting.

**6. Builders: use worktrees.** One card = one branch in your own worktree (`git worktree add ../<repo>-<your-role> -b <your-role>/<card>`), so parallel work never collides. Only the integrator merges and deploys — and only from a clean tree.

**7. Never grade your own work.** When you finish a card, attach evidence (screenshot, video, command output) and move it to **Testing** — not Done. The tester re-verifies it with fresh eyes against its `done-means:` on the real running product, then moves it to **Done** or **Needs work** with notes. A builder's "done" is a hypothesis.

**8. Chat for coordination, cards for decisions.** Post to chat when you claim, ship, get blocked, or learn something others need — and @mention the role you need. Anything that must survive the scroll goes on a card, not in chat.

**9. Don't stop.** Card done → pull the next one. Todo empty → derive the next cards from the Mission if there is one, or scout the product for problems and file what you find. You're finished when the human says so, not when the obvious tasks run out.

## Mission (optional)

**Repair the UN fulltext corpus until adversarial verification cannot break it.**

Outcome: every document mandates.un.org serves is faithful to its UN source — no invented
text, no silent truncation, no invisible content — and every coverage number we publish has a
denominator taken from the source documents, not from our own output.

Context: an adversarial audit (2026-07-22) found fabricated text (~2,600 docs), 145 documents
silently stored at ~21% of source, 12% of the corpus rendering nothing to a reader, 499 printed
decisions never stored, and only 15 of 50 gate negative-controls firing. Full evidence:
`docs/_research/adversarial-{gates,exceptions,content,numbers}.md` @ commit 04fe151.

Code: `/Users/david/UN/documents.unfck.org` branch `feature/fulltexts` (pipeline, parser, gates)
and `/Users/david/UN/mandates` branch `worktree-fulltexts-website` (site rendering).
DB: Azure Postgres via `.env` in each repo; schema `digitallibrary`. Archive: /Volumes/SSDAStorage.
Run gates: `uv run python python/fulltext_<gate>.py` — never through a pipe (exit codes lie).

Out of scope: OCR. Documents with no text layer stay out; they must be *named and counted*, not
silently dropped. Where old scanned material has a hopeless effort/impact ratio, narrow scope
explicitly and record the boundary on the scope card.

Done: the tester cannot find a defect, every card's `done-means:` is met with source-derived
evidence, and the scope table accounts for all 41,802 catalog symbols in exactly one bucket.

## House rules

The things that must stay true no matter how the team reaches the goal. Agents check their work against these before shipping. Edit freely:

1. Only the integrator deploys, and only from a clean git tree.
2. Don't hard-code special cases to pass a check — fix the general behavior.
3. No new dependencies without a card the human has seen.
4. Every behavior change ships with a test or recorded evidence.
5. Ask the human only about things only the human can decide.
6. **Never weaken a validation to make it pass.** An honest failing number is a deliverable; a
   tuned-to-green number is a hidden defect. If a gate fails, fix the pipeline or prove the gate wrong
   against the source — never relax the gate to reach green.
7. **Every check ships a negative control.** A check that has never been shown to fail is absent,
   not passing. Damage an input, prove the check rejects it, keep that control runnable.
8. **Denominators come from the source documents**, never from what processing produced.
9. **Whatever built something must not grade it.** Verification runs in a fresh context whose
   instruction is to prove the work is broken.
10. **A gap and a fabrication are not the same failure.** Missing text is a known hole; invented
    text is a false statement in a legal corpus and outranks everything else.

## The board

```board #tasks
## Todo
- [ ] C1 Make the gates able to fail @unclaimed #gates #blocking
  done-means: `fulltext_negative_controls.py` reports >=48/50 DETECTED and runs in CI; verify_pdf's
  denominator comes from the source PDF not the parse (90%-deletion control drops the score, not
  raises it); verify_volumes scores the stored children (child-side damage moves the number);
  every gate exits non-zero on empty input; the three accommodations added 2026-07-22 are gone.
  Blocks every other card: no repair is believable until the instruments can fail.
- [ ] C2 Kill two-column weaving fabrication (pre-1994 PDF path) @unclaimed #fabrication #p0
  done-means: independent re-check of 400 PDF-path docs finds 0 paragraphs absent from their own
  source (baseline 6.96%); A/RES/1514(XV) preamble matches the printed text verbatim; column
  handling proven on 2-col, 3-col and mixed-layout pages with recorded evidence.
- [ ] C3 Stop inventing paragraph markers @unclaimed #fabrication #p0
  done-means: no document stores a marker that does not continue its own sequence; S/RES/661(1990)
  has 9 operatives and no "19."; the 513-doc signature class is either fixed or each remaining
  case shown legitimate against its source.
- [ ] C4 Fix silent truncation of parsed documents @unclaimed #loss #p0
  done-means: the 145 known-truncated docs store >=95% of source words (A/RES/701(VII) is not a
  47-char title line); a TOTAL source-vs-stored ratio check covers every parsed document and
  flags rather than silently accepts; anchor-found no longer suppresses the flag.
- [ ] C5 Make stored text visible to readers @unclaimed #display
  done-means: <1% of documents render zero content (baseline 3,116 = 12%); all 3,590 volume-split
  children render; decision bodies are not typed frontmatter; the NULL-subtype predicate bug at
  paragraphs.ts:168 is fixed with a regression test.
- [ ] C6 Recover the decisions we never detected @unclaimed #recall
  done-means: recall >=97% against a denominator counted from the volume PDFs themselves; the
  "heading merged with adoption line" class (249 docs) is eliminated; A/DEC/60/404 and
  A/DEC/60/519 have their own paragraphs; lettered-part rule (405 A/B) picked once and applied
  to both source and catalog counts.
- [ ] C7 Repair the early-HRC track @unclaimed #recall
  done-means: A/HRC/10/29 yields its 51 adopted texts; every session report's child count equals
  its printed item count; no child exceeds its printed length (A/HRC/PRST/8/2 is not 108k words);
  the volume map is verified doc-by-doc (A/HRC/S-4/2 is currently a Darfur letter, not a report).
- [ ] C8 Undo lexical damage @unclaimed #fidelity
  done-means: 0 rows contain "selfdetermination"; hyphenated compounds preserved; the run-join
  allowlist excuses <1% of content tokens (baseline 11%) and cannot hide a dropped word; the
  French-line filter applies only to genuinely bilingual documents (1,003 English lines restored).
- [ ] C9 Publish honest scope boundaries @unclaimed #coverage
  done-means: a table where each of the 41,802 catalog symbols sits in exactly one bucket with
  source-derived evidence; out-of-scope classes (OCR, hopeless scans) named with counts and the
  reason; every number in docs/fulltexts.md either reproducible by a command or removed.
## Doing
## Testing
## Needs work
## Done
- [x] Spin up this HQ doc @human
- [x] Adversarial audit of the fulltext pipeline @claude-integrator #evidence
  Four fresh-context audits; findings at commit 04fe151 in documents.unfck.org.
  15/50 gate controls fire; fabrication, truncation, invisibility, recall gaps all confirmed
  against the live corpus by independent SQL. Retracted: "89/89 volumes pass", "100.0000%
  preservation" (true over 58.8%), "34/34 PDF sample", "OCR yield 80-95% (measured)".
```

## Team chat

```chat #hq
## general
- 2026-07-07T00:00Z @hq (agent): Welcome! Claim a role in the table up top, announce yourself here, then start watching the doc. Your briefing is the AGENTS: READ THIS FIRST section.
## blockers
- 2026-07-07T00:01Z @hq (agent): Post here when you're stuck, and @mention the role you need.
```

## For humans: setup

1. Tweak **Roles** and **House rules** to taste. Set a **Mission** if there's a standing goal — or skip it; this doc works fine as an open-ended workspace.
2. Press **Share** and grab an **edit** link. Optionally mint one link per agent so you can revoke them individually.
3. Paste it into each agent session with: _"Read this doc and follow the AGENTS: READ THIS FIRST section."_
4. Hand out work however you like: add a card to the board, or just say it in chat ("the mobile view feels cramped — someone fix it") — one agent will claim it and run. The board and chat update live; watching agents react in seconds.
