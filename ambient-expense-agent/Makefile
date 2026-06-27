# Makefile for Ambient Expense Agent

.PHONY: install playground serve test test-unit

## generate-traces - run trace generator script
generate-traces:
	uv run python -m pytest tests/eval/generate_traces.py -q

## grade - evaluate generated traces
grade:
	agents-cli eval grade \
		--config tests/eval/eval_config.yaml \
		--traces artifacts/traces \
		--out artifacts/grade_results

## evaluate - convenience target (generate then grade)
evaluate: generate-traces grade

