# Full rebuild: make data && make model && make site
PY ?= python

data:            ## download 2026 raw data (about 40 minutes, polite request pacing)
	$(PY) pipeline/fetch.py schedule
	$(PY) pipeline/fetch.py players
	$(PY) pipeline/fetch.py statcast
	$(PY) pipeline/fetch.py challenges

model:           ## build the database and every model
	$(PY) pipeline/parse_raw.py
	$(PY) pipeline/build_db.py
	$(PY) pipeline/values.py
	$(PY) pipeline/zone.py
	$(PY) pipeline/perception.py
	$(PY) pipeline/policy.py
	$(PY) pipeline/policy.py probit
	$(PY) pipeline/analysis.py

site:            ## write the JSON the site reads, and the printable dugout card
	$(PY) pipeline/export.py
	$(PY) pipeline/story.py
	$(PY) pipeline/card_pdf.py
	$(PY) pipeline/og_image.py
	$(PY) scripts/check_site.py

test:
	$(PY) -m pytest -q tests

serve:
	$(PY) -m http.server 8642 --bind 127.0.0.1 --directory site

.PHONY: data model site test serve
