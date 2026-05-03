.PHONY: setup start test doctor telegram-config

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
