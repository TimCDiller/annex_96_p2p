.PHONY: install test course-build course-run class-run explain buildings run tournament booklet scan docker-tournament clean

install:
	python -m pip install -r scenarios/requirements.txt
	python -m pip install -e .

# Contracts plus the course market; skips what is not installed.
test:
	python -m pytest -q

# --- the course market: the altered Vermont winter stock -------------------
course-build:
	python -m annex96p2p.altered_stock build

# Your file(s) in submissions/, one week, the graded configuration.
course-run:
	python -m annex96p2p.altered_stock run --configurations balanced --modes competitive --days 7 --submissions submissions --out results/course

# Everyone's Canvas files in submissions/, full month. Scan first.
class-run: scan
	python -m annex96p2p.altered_stock run --configurations balanced --modes competitive --submissions submissions --out results/class

# --- the original 25-home exercise (docs/original_exercise.md) --------------

# The market mechanism walked through in real numbers. No dataset needed.
explain:
	python examples/explain_market.py

buildings:
	python -m annex96p2p buildings --config config/default.yaml

run:
	python -m annex96p2p run --config config/default.yaml

tournament:
	python -m annex96p2p tournament --config config/default.yaml

# The sample run: every example agent, plus an HTML booklet of what happened.
booklet:
	python examples/sample_run.py --days 7

scan:
	python scripts/scan_submissions.py submissions/

docker-tournament:
	docker build -t annex96 .
	docker run --rm --network none --memory 4g --cpus 2 \
	  -v "$$PWD/examples/original_submissions:/app/examples/original_submissions:ro" annex96 \
	  python -m annex96p2p tournament --config config/default.yaml

clean:
	rm -rf results .pytest_cache **/__pycache__
