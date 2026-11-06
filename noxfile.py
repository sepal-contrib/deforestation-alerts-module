"""Developer sessions: ``nox -s lint`` (the pre-commit hooks) and ``nox -s app``.

``lint`` runs in a virtualenv kept in ``.nox/`` (delete ``.nox/lint`` to rebuild
it); ``app`` runs in the active conda environment.
"""

import nox


@nox.session(reuse_venv=True)
def lint(session):
    """Apply the pre-commits."""
    session.install("pre-commit")
    session.run("pre-commit", "run", "--all-files", *session.posargs)


@nox.session(reuse_venv=True, venv_backend="none")
def app(session):
    """Run the Solara application in the active (conda) environment."""
    session.run("solara", "run", "gui/solara_app.py", "--port", "8900", "--no-open")
