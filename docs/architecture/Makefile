# Build the PDFs: make (needs graphviz, pandoc, xelatex)
DOCS := $(wildcard 0*.md)
PDFS := $(patsubst %.md,pdf/%.pdf,$(DOCS))
DOTS := $(wildcard diagrams/*.dot)

all: diagrams $(PDFS)

diagrams: $(DOTS:.dot=.pdf) $(DOTS:.dot=.png)
diagrams/modules.dot: diagrams/gen_modules.py $(wildcard ../../src/litnav/*.py)
	cd ../.. && python3 docs/architecture/diagrams/gen_modules.py
06-modules.md: gen_modules_md.py $(wildcard ../../src/litnav/*.py)
	cd ../.. && python3 docs/architecture/gen_modules_md.py
diagrams/%.pdf: diagrams/%.dot
	dot -Tpdf $< -o $@
diagrams/%.png: diagrams/%.dot
	dot -Tpng -Gdpi=110 $< -o $@

pdf/%.pdf: %.md header.tex $(DOTS:.dot=.pdf)
	@mkdir -p pdf
	pandoc $< -o $@ --pdf-engine=xelatex -H header.tex \
	  -V geometry:margin=2cm -V mainfont="Helvetica Neue" -V monofont="Menlo" \
	  -V fontsize=10pt -V colorlinks=true --toc --toc-depth=2 \
	  -V papersize=a4 --resource-path=.

.PHONY: all diagrams
