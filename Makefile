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

screen-demo:      ## interactive demo on the REAL display (Enter to step; auto-detects device)
	$(PY) integration/screen_demo.py

mouse-demo:       ## interactive touch/mouse demo on the REAL display (buttons + live cursor)
	$(PY) integration/mouse_demo.py

clean:            ## drop the venv and generated frames
	rm -rf $(VENV) integration/*_frame.png
