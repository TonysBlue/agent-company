.PHONY: init status cycle worker-step worker-status worker-wake inbox report dashboard demo validate test archive clean

PYTHON ?= python3.11

init:
	$(PYTHON) -m agent_company.cli init

status:
	$(PYTHON) -m agent_company.cli status

cycle:
	$(PYTHON) -m agent_company.cli run-cycle

worker-step:
	$(PYTHON) -m agent_company.cli worker-step

worker-status:
	$(PYTHON) -m agent_company.cli worker-status

worker-wake:
	$(PYTHON) -m agent_company.cli worker-wake --reason "$(or $(REASON),operator wake)"

inbox:
	$(PYTHON) -m agent_company.cli chairman-inbox

report:
	$(PYTHON) -m agent_company.cli report

dashboard:
	$(PYTHON) -m agent_company.dashboard --host 0.0.0.0 --port 18080

demo:
	$(PYTHON) -m agent_company.cli demo

validate:
	$(PYTHON) -m agent_company.cli validate

test:
	$(PYTHON) -m unittest discover -s tests -v

archive:
	$(PYTHON) scripts/archive_company.py create --label "$(or $(LABEL),manual)"

clean:
	rm -f data/company.sqlite3
	rm -f data/artifacts/*.json data/chairman/inbox/*.json data/chairman/outbox/*.json
