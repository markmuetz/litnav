---
description: Discuss a paper (or concept:<slug>) you have read, in your own words, instead of a quiz
argument-hint: <paper key or concept:slug> [focus]
---

Run a discussion about **$ARGUMENTS** with the user, following the reader
skill's "Discuss" section. In short:

1. **Prepare quietly.** Resolve the key (`get_paper`; for `concept:<slug>`
   read `concepts/<slug>.md`). Read the user's notes (`get_note`), the
   previous log (`litnav quiz show <id>`), and the section list
   (`list_sections`). Check whether a recall card for it is due
   (`reading_due`). Don't show any of this to the user.
2. **The user explains first.** Open with one broad question, e.g. "In your
   own words: what was this paper trying to find out, and what did it
   find?" Then wait. Don't summarise the paper for them.
3. **Probe, one short question at a time,** building on what they said: why,
   how, what would change it, how it compares with other papers, whether
   they trust it. Lean towards what their notes and earlier logs don't
   cover.
4. **Correct only from the paper.** If they get something wrong or reversed,
   check it first (`search_literature` with `tag`/`author` filters, or
   `get_section`), then say so briefly with a short quote and its chunk ID.
   If you can't find it, say you're not sure rather than guess.
5. **Finish** when the user says so or the main threads are covered:
   - log it: `litnav quiz start <id> --discuss`, then fill in the bullets
     (explained well, shaky, missing, corrections with chunk IDs);
   - if a recall card is due, propose a rating judged on the gist and apply
     it (`litnav recall <id> <rating>`) only once the user agrees;
   - suggest anything worth adding to their notes, but don't edit the notes.
