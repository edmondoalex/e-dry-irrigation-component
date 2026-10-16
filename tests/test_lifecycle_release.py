import ast
import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "e_dry"


class ReleaseLifecycleTests(unittest.TestCase):
    def test_release_identity_and_official_assets(self):
        manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((manifest["domain"], manifest["version"]), ("e_dry", "10.1.5"))
        expected = "f6e4e963300f68d1f5aca5343f66b979fd15bc26944f2195497728a317b79e5e"
        for relative in ("icon.png", "logo.png", "brand/icon.png", "brand/logo.png"):
            self.assertEqual(hashlib.sha256((COMPONENT / relative).read_bytes()).hexdigest(), expected)

    def test_unload_removes_services_only_after_last_entry(self):
        source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        unload = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                      and node.name == "async_unload_entry")
        rendered = ast.unparse(unload)
        self.assertIn("if not hass.data[DOMAIN]", rendered)
        self.assertIn("hass.services.async_remove(DOMAIN, service)", rendered)
        self.assertIn("controller.shutdown()", rendered)

    def test_setup_never_removes_registry_entities_implicitly(self):
        source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
        automatic_setup = source.split("async def handle_remove_stale_entities", 1)[0]
        self.assertNotIn("registry.async_remove", automatic_setup)

    def test_tests_never_call_valve_or_irrigation_services(self):
        test_source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "tests").glob("test_*.py")
            if path.name != Path(__file__).name
        )
        self.assertNotIn("async_turn_on(", test_source)
        self.assertNotIn("start_zone(", test_source)
        self.assertNotIn("start_zone_for(", test_source)


if __name__ == "__main__":
    unittest.main()
