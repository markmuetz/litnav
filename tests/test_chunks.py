from litnav import chunks, sections

PARA = ("Cold pools spread beneath convective systems and lift the surrounding air. " * 6).strip()


def paper(n_par=4):
    body = "\n\n".join([PARA] * n_par)
    return f"""Cold pools and convection
Jane Smith

Abstract
We study cold pools. They matter for the organisation of convection in models.

1. Introduction

{body}

2. Data and methods

2.1 Model

{body}

Fig. 1. Snapshot of the cold pool at 3 h, showing the gust front and lifting.

2.2 Experiments

Short subsection with only a few words here.

3. Results

{body}

4. Conclusions

{PARA}

References

Smith, J., 2020: Cold pools. J. Atmos. Sci., 77, 1-10.
Jones, B., 2019: Squall lines. Mon. Wea. Rev., 147, 2-20.
"""


def test_sections_numbered():
    p = sections.parse(paper())
    assert p.detected and p.method == "numbered"
    slugs = [s.slug for s in p.sections]
    assert slugs[:2] == ["front", "abstract"]
    assert ["1", "2", "2-1", "2-2", "3", "4", "references"] == slugs[2:]
    assert p.sections[-1].kind == "refs"
    assert p.sections[2].title == "Introduction"


def test_sequence_rejects_stray_numbers():
    t = "Intro text\n\n3 km grid spacing was used\n\n12 Hours Later we saw rain\n\n" + PARA
    p = sections.parse(t)
    assert not any(s.number for s in p.sections)


def test_all_caps_headings_titled():
    t = paper().replace("1. Introduction", "1 INTRODUCTION").replace("3. Results", "3 RESULTS")
    p = sections.parse(t)
    assert "Introduction" in [s.title for s in p.sections]


def test_chunks_never_straddle_and_skip_refs():
    p = sections.parse(paper(8))
    cs = chunks.chunk("smith2021cold", p)
    by = {s.slug: s for s in p.sections}
    for c in cs:
        if c.kind == "caption":
            continue
        top = c.section.split("-")[0]
        s = by[c.section]
        # within the chunk's own top-level section
        group_end = min((x.start for x in p.sections if x.level == 1 and x.start > s.start), default=len(p.text))
        assert s.start <= c.start and c.end <= group_end, c.id
    assert not any("Mon. Wea. Rev" in c.text for c in cs)
    assert any(c.kind == "abstract" for c in cs)
    assert any(c.kind == "caption" and c.text.startswith("Fig. 1.") for c in cs)
    assert not any("Fig. 1." in c.text for c in cs if c.kind != "caption")


def test_chunk_sizes_and_overlap():
    p = sections.parse(paper(12))
    body = [c for c in chunks.chunk("k", p) if c.kind == "body"]
    assert all(len(c.text.split()) <= chunks.MAX + 70 for c in body)
    assert any(len(c.text.split()) >= chunks.TARGET for c in body)


def test_ids_stable_and_unique():
    p = sections.parse(paper(6))
    a = chunks.chunk("k", p)
    b = chunks.chunk("k", sections.parse(paper(6)))
    assert [c.id for c in a] == [c.id for c in b]
    assert len({c.id for c in a}) == len(a)
    assert a[0].id.startswith("k#")


def test_short_subsection_merged():
    p = sections.parse(paper())
    cs = chunks.chunk("k", p)
    assert not any(c.section == "2-2" and len(c.text.split()) < 20 for c in cs)


def test_fallback_windows():
    t = " ".join(f"word{i}" for i in range(1000))
    cs = chunks.chunk("k", sections.parse(t))
    assert cs and all(c.kind == "window" for c in cs)
    assert len(cs[0].text.split()) == chunks.WINDOW


def test_manual_sections_opt_in():
    t = "# Intro\nsome text here\n# Methods\nmore text\n"
    assert sections.parse(t).method != "manual"
    assert sections.parse(t, manual=True).method == "manual"
