.PHONY: install test explain buildings run tournament booklet scan docker-tournament clean

install:
	python -m pip install -e ".[dev]"

# Fast suite: contracts only, no CityLearn needed.
test:
	python -m pytest -q

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
	  -v "$$PWD/submissions:/app/submissions:ro" annex96 \
	  python -m annex96p2p tournament --config config/default.yaml

clean:
	rm -rf results .pytest_cache **/__pycache__
