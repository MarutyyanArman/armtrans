# AI conversations used to build this project

The project was planned and built with Claude in three sessions. Each file holds one session's
user-visible conversation, verbatim and in order: Arman's messages and Claude's visible replies (including the
questions Claude asked with the answers given, file cards, and on-screen notices or errors).

| File | Session | When (Asia/Yerevan) | Where |
|---|---|---|---|
| `01-armenian-live-transcription-plan.md` | PLAN.md, first prototype, model choice, speaker rework | 2026-10-31 19:59 → 2026-11-01 04:41 | claude.ai chat |
| `02-speaker-count-discrepancy-in-handoff.md` | speaker labels on real calls, ASR switch to RNNT, recordings | 2026-11-01 04:43 → 16:02 | claude.ai chat |
| `03-armtrans-project-context-handoff.md` | phrase cuts, ASR context, translation guard, eval sets, cleanup, GitHub | 2026-11-01 16:15 → export time | Claude Code session |

What is not included, on purpose: tool calls and their output (commands, file contents, logs), the model's
hidden reasoning, and system instructions. App-injected reminders inside user messages were removed; the user's own
text is unchanged.

Notes:
- Session 2 had two edited prompts. The app shows only the latest branch; the replaced messages are kept in an
  appendix at the end of that file.
- Session 2 contains one screenshot the user attached; the image itself is not in the export (only its file name).
- Session 3 was exported while it was still running, so it ends at the export request.
- Session 3 shows intermediate text between tool calls in full; the app may display some of it in condensed form.
- Session 3 includes a point where the conversation was compacted; the summary given to the model is not shown.
