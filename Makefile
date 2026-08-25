.PHONY: install smoke run test explain buildings clean

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
