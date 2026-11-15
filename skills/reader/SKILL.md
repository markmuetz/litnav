---
name: reader
description: Help the user read and remember papers in their litnav library — the reading list and reading depths, quizzes after reading, spaced-recall reviews, and concept cards. Use when the user asks what is due, wants to add a paper to read, has finished a paper, wants a quiz, or wants to review recall cards.
---

# Reader: reading list, quizzes, spaced recall

## Files (in the library)

| file | what |
|---|---|
| `reading/list.toml` | papers to read: `key`, `depth`, `status` (`to read`, `reading`, `done`), `done = {depth, date}` |
| `reading/recall.toml` | recall cards: `id` (a key or `concept:<slug>`), `box`, `last_reviewed`, `next_due`, `prompt` |
| `notes/<key>.md` | the user's notes; cite papers as `[@key]` |
| `concepts/<slug>.md` | concept notes |
| `quizzes/<key>.md` | quiz and discussion logs, one dated section per session (`litnav quiz list/show/start`) |

Commands: `litnav read add <key> --depth <d>`, `litnav read done <key> --depth <d>`,
`litnav read list [--all]`, `litnav due`, `litnav recall <id> <rating>`.
The web app (`litnav serve`) has the same in its Reading view.

## Depths

A depth is a time budget, set when a paper is added (ask if the user gives
none): `deep` (half a day or more; notes, quiz, recall card), `read` (2–4 h;
notes, recall card), `skim` (30–60 min; a short gist; a card only if asked),
`scan` (~20 min; no notes, no card), `comment` (someone's draft; no card).

## Discuss (instead of, or as well as, a quiz)

Started by `/litnav:discuss <key>` or "let's discuss <paper>". The user
explains the paper in their own words and you probe; retrieval comes first,
so the user always speaks before you explain anything.

- **Prepare quietly**: the paper's metadata, the user's notes, the earlier
  quiz/discussion log (`litnav quiz show <id>`), the section list, and
  whether a recall card is due. Show none of it.
- **Open broad, then wait**: "What was it trying to find out, and what did
  it find?" Never open with a summary.
- **One short question at a time**, built on their last answer: mechanisms
  (why, how), sensitivity (what would change the result), placement (how it
  relates to other library papers, cited as `[@key]`), verdict (would they
  trust it, cite it, for what). Prefer what the notes don't cover. Keep your
  turns short; this is their thinking, not your lecture.
- **Correct only from the paper**: check with `search_literature` or
  `get_section` before saying they are wrong, then give a short verbatim
  quote and its chunk ID. If you can't find support either way, say so.
- **Depth follows timing**: within a few days of reading, details and
  mechanisms are fair game; after that, the gist only, and forgotten details
  are not misses.
- **If they ask you to explain**, do: briefly, then hand back with a
  question.
- **Close** with a log in `quizzes/<id>.md` (`litnav quiz start <id>
  --discuss`, then fill in: explained well, shaky, missing, corrections with
  chunk IDs). A due recall card can be rated from the discussion, on the
  gist, once the user agrees. Suggest note additions; don't make them.

## Rules

- **Only the user marks a paper read.** Never infer it from file access or
  tags. `read done` with `deep` or `read` creates a recall card.
- **Quizzes check comprehension** soon after reading; they ask about the main
  arguments, especially what the notes do not cover. Read the quiz log first
  so a second quiz goes deeper. Reference answers are pointers (a sentence
  and a section or figure number) checked against the paper, not notes.
- **Recall cards keep the gist** over months: the paper's question, its
  headline answer, where it sits, and the user's verdict. Draw prompts from
  the user's notes (the `Gist` section if there is one) before the abstract.
  Grade on the gist: forgotten details are not misses. Don't fold quiz misses
  into a card unless they are gist-level.
- **Ratings**: easy promotes a box (max 4); medium stays, due at the medium
  interval; hard stays; forgot returns to box 1. Boxes are 1 working day,
  1 week, 1 month, 3 months. Nothing is ever due at a weekend.
- **Batch** everything due today into one summary.
- **Notes are the user's.** When asked to make a notes file, copy
  `notes/_template.md` and fill only the facts (key, title); never pre-fill
  content. Label any explanation you write in a concept note as a
  "Reference explanation", separate from the user's own sections.
