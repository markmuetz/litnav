# Extraction passes: commands

The commands that build a claim graph's schema and fill it, added in November 2026. The design lives with the
project that uses them; this page says what each command does and in what order to run them.

**Who reads what.** Open papers (Creative Commons; `litnav licences`) may go to any model. Licensed papers'
text goes only to the local model or a cluster model you run (`[local_model]`, `[batch_remote]`).
`claude-run` refuses any job that would send licensed text to the Anthropic API, and the judges refuse papers
that are not open.

**Models.** Bulk passes run on a small model: Claude Haiku through `claude-run` (needs `ANTHROPIC_API_KEY`), or
the local or cluster model through `queue push` / `pull`. Schema steps use strong models through `claude -p`
(your Claude Code login) and Gemini through `agy`. Opus drafts and revises; Fable and Gemini review.

## Running a job folder on the API

```bash
litnav claude-run RUN_DIR --effort low|medium -j 8
```

It runs `RUN_DIR/jobs.jsonl` and appends to `results.jsonl`, skipping jobs already done. It stops at the first
failure.

## G0: induce the domain schema

```bash
litnav g0 sample [--open-only] [--like RUN]   # survey jobs (every paper) + inventory jobs (a stratified sample)
litnav claude-run RUN --effort medium          # or queue them for the local/cluster model
litnav g0 tally --run-dir RUN
litnav g0 synthesize --run-dir RUN             # draft-1.json; rules-check-draft-1.json
litnav g0 review --run-dir RUN                 # review-draft-1.json
litnav g0 revise --run-dir RUN                 # draft-2.json
```

Each proposed rule lists the inventories it rests on, and code maps them back to papers. Numbers in a rule that
appear nowhere in the evidence are flagged as the model's own.

## Model registry and evidence type

```bash
litnav model-registry jobs [--open-only]       # one job per paper: runs, grid, process states, definitions
litnav model-registry build --run-dir RUN      # cases and definitions
litnav model-registry score --run-dir RUN      # leave one paper out
litnav model-registry classes --run-dir RUN    # class senses induced from the papers' definitions
```

## Topics

```bash
litnav topics jobs [--open-only]               # aspects each paper reports in detail (open vocabulary)
litnav topics cluster --run-dir RUN            # consensus clustering; clusters reported by 2+ papers
litnav topics name --run-dir RUN               # a strong model names and merges them
```

## Topic templates (T0) and filling them (T1)

```bash
litnav template route --topics-run TOPICS_RUN --topic "NAME"   # new run folder; papers routed to the topic
litnav template inventory --run-dir RUN [-n 40]                # what each paper reports about the topic
litnav claude-run RUN --effort medium
litnav template tally --run-dir RUN
litnav template draft|review|revise --run-dir RUN              # T0: the template
litnav template t1 --run-dir RUN                               # T1 jobs: every routed paper
litnav claude-run RUN/t1 --effort low
litnav template t1-summary --run-dir RUN                       # fields stated, passages checked
litnav template t1-judge --run-dir RUN [-n 5]                  # a strong model grades n random papers
```

T1 replies are in long format, one entry per stated value (experiment, field, value, passage). A field per
property compiles to a grammar too large for the API.

## Is the sample big enough?

```bash
litnav saturation RUN_DIR      # a G0 or template run folder
```

This needs no model calls. It counts the kinds seen against the papers inventoried, over random orders, at two
clustering granularities, with the Chao1 estimate (Chao 1984) of how many kinds the population holds.
