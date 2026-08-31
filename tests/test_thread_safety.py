"""Thread-safety regression tests for e_dry callbacks."""

from __future__ import annotations

import ast
import asyncio
import threading
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).parents[1]
SENSOR_SOURCE = REPO_ROOT / "custom_components" / "e_dry" / "sensor.py"


def _decorator_name(decorator: ast.expr) -> str | None:
    if isinstance(decorator, ast.Name):
        return decorator.id
    if isinstance(decorator, ast.Attribute):
        return decorator.attr
    return None


class TestStateUpdateCallbacks(unittest.IsolatedAsyncioTestCase):
    async def test_program_info_interval_update_runs_on_event_loop(self) -> None:
        """The five-second update must be an event-loop callback, not executor work."""
        tree = ast.parse(SENSOR_SOURCE.read_text(encoding="utf-8-sig"))
        programs_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "EDry2ProgramsInfoSensor"
        )
        method = next(
            node
            for node in programs_class.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_handle_interval_update"
        )

        self.assertIn("callback", map(_decorator_name, method.decorator_list))

        loop_thread_id = threading.get_ident()
        write_thread_ids: list[int] = []

        class Entity:
            def async_write_ha_state(self) -> None:
                asyncio.get_running_loop()
                write_thread_ids.append(threading.get_ident())

        callback_method = compile(
            ast.Module(body=[method], type_ignores=[]),
            str(SENSOR_SOURCE),
            "exec",
        )
        namespace = {
            "callback": lambda func: setattr(func, "_hass_callback", True) or func,
            "datetime": object,
        }
        exec(callback_method, namespace)  # noqa: S102 - execute the inspected callback only
        Entity._handle_interval_update = namespace["_handle_interval_update"]

        registered_callback = Entity()._handle_interval_update
        if getattr(registered_callback, "_hass_callback", False):
            registered_callback(None)
        else:
            await asyncio.to_thread(registered_callback, None)

        self.assertEqual(write_thread_ids, [loop_thread_id])

    def test_sync_state_writers_are_home_assistant_callbacks(self) -> None:
        """Every synchronous state-writing callback must be marked for the loop."""
        violations: list[str] = []
        for path in (REPO_ROOT / "custom_components" / "e_dry").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue
                writes_state = any(
                    isinstance(child, ast.Attribute)
                    and child.attr == "async_write_ha_state"
                    for child in ast.walk(node)
                )
                decorators = {_decorator_name(item) for item in node.decorator_list}
                if writes_state and "callback" not in decorators:
                    violations.append(f"{path.name}:{node.lineno}:{node.name}")

        self.assertEqual(violations, [])

    def test_program_info_listener_is_removed_with_entity(self) -> None:
        """The interval unsubscribe callback must be registered on entity removal."""
        tree = ast.parse(SENSOR_SOURCE.read_text(encoding="utf-8-sig"))
        programs_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "EDry2ProgramsInfoSensor"
        )
        added = next(
            node
            for node in programs_class.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "async_added_to_hass"
        )
        calls = [
            ast.unparse(node)
            for node in ast.walk(added)
            if isinstance(node, ast.Call)
        ]
        self.assertIn("self.async_on_remove(self._unsub)", calls)


if __name__ == "__main__":
    unittest.main()
