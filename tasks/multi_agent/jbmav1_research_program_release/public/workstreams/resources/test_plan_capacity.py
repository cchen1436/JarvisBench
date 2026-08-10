import unittest
from pathlib import Path

from plan_capacity import evaluate


class CapacityTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).parent
        self.rows = evaluate(root / "lab_capacity.json", root / "direction_requirements.csv")

    def test_three_directions_are_preserved(self):
        self.assertEqual(len(self.rows), 3)

    def test_scoped_directions_are_feasible(self):
        self.assertTrue(all(row["feasible"] == "yes" for row in self.rows))

    def test_binding_constraints_are_calculated(self):
        bindings = {row["direction"]: row["binding_constraint"] for row in self.rows}
        self.assertEqual(bindings["corrective_retrieval"], "gpu_hours")
        self.assertEqual(bindings["citation_calibration"], "annotator_hours")
        self.assertEqual(bindings["context_order"], "queries")


if __name__ == "__main__":
    unittest.main()
