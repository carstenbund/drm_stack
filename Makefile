VENV   := .venv
PY     := $(VENV)/bin/python
PYTEST := $(VENV)/bin/pytest

.PHONY: setup test demo clean

setup:            ## clone packages + editable-install into .venv
	./setup.sh

test:             ## run the stack integration suite
	$(PYTEST) -q

demo:             ## render the headless end-to-end demo -> integration/stack_frame.png
	$(PY) integration/stack_demo.py

clean:            ## drop the venv and generated frames
	rm -rf $(VENV) integration/*_frame.png
