.PHONY: setup start test doctor telegram-config check check-fast

setup:
	./setup.sh

start:
	./start.sh

test:
	python3 -m unittest discover -s tests

doctor:
	python3 run.py doctor

telegram-config:
	python3 run.py telegram-config

## Quality-gate checks
check:
	./scripts/check.sh

check-fast:
	./scripts/check.sh --fast
