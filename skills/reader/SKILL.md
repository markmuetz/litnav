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
| `quizzes/<key>.md` | quiz log: question, reference answer, result |

Commands: `litnav read add <key> --depth <d>`, `litnav read done <key> --depth <d>`,
`litnav read list [--all]`, `litnav due`, `litnav recall <id> <rating>`.
The web app (`litnav serve`) has the same in its Reading view.

## Depths

A depth is a time budget, set when a paper is added (ask if the user gives
none): `deep` (half a day or more; notes, quiz, recall card), `read` (2–4 h;
notes, recall card), `skim` (30–60 min; a short gist; a card only if asked),
`scan` (~20 min; no notes, no card), `comment` (someone's draft; no card).

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
