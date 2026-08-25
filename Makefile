.PHONY: install smoke run test explain buildings clean scan docker-tournament

install:
	python -m pip install -e ".[dev]"

smoke:
	python -m annex96p2p run --config config/smoke.yaml

run:
	python -m annex96p2p run --config config/default.yaml

explain:
	python examples/explain_market.py

buildings:
	python -m annex96p2p buildings

test:
	python -m pytest -q

clean:
	rm -rf results .pytest_cache **/__pycache__

scan:
	python scripts/scan_submissions.py submissions/

docker-tournament:
	docker build -t annex96 .
	docker run --rm --network none --memory 2g --cpus 2 \
	  -v "$$PWD/submissions:/app/submissions:ro" annex96 \
	  python -m annex96p2p tournament --config config/default.yaml
