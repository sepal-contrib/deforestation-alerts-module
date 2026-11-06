"""The voila entry must be a valid nbformat notebook whose kernelspec names
SEPAL's module venv. Voila does NOT fail loudly on a missing kernel — it
falls back to any registered kernel with only a WARNING (sepal-deployment.md),
so the name is load-bearing."""

from pathlib import Path

import nbformat

NB = Path(__file__).resolve().parents[1] / "ui.ipynb"


def test_notebook_is_valid_and_targets_module_kernel():
    nb = nbformat.read(NB, as_version=4)
    nbformat.validate(nb)
    assert nb.metadata["kernelspec"]["name"] == "venv-deforestation-alerts-module"


def test_notebook_stays_thin():
    nb = nbformat.read(NB, as_version=4)
    code = "\n".join(c.source for c in nb.cells if c.cell_type == "code")
    assert "from gui.solara_app import Page" in code
    # thin by design: shim + import + display, no business logic
    assert len(nb.cells) <= 3
