"""Running a test against a component ``reacton.render`` mounted.

A plain module rather than a fixture: the tests call it with their own render
call and body, and it owns only the event loop and the cleanup."""

import asyncio


def run_rendered(render, exercise) -> None:
    """``await exercise(rc)`` on the render context ``render()`` returns
    (``(box, rc)``, as ``reacton.render``), inside one event loop, and close
    ``rc`` afterwards whatever happens. ``render`` runs inside the loop: a
    component's tasks need it."""

    async def main():
        _box, rc = render()
        try:
            await exercise(rc)
        finally:
            rc.close()

    asyncio.run(main())
