import unittest
from pathlib import Path

from evaluate_controls import evaluate


class EvaluateControlsTests(unittest.TestCase):
    def setUp(self):
        self.rows = evaluate(Path(__file__).with_name("control_candidates.csv"))

    def test_all_candidates_are_preserved(self):
        self.assertEqual({row["initiative"] for row in self.rows}, {"rollout_guardrails", "observability", "restore_rollback_drills"})

    def test_coverage_per_point_is_computed(self):
        scores = {row["initiative"]: float(row["coverage_per_point"]) for row in self.rows}
        self.assertAlmostEqual(scores["rollout_guardrails"], 0.5)
        self.assertAlmostEqual(scores["observability"], 0.6)
        self.assertAlmostEqual(scores["restore_rollback_drills"], 0.375)

    def test_owner_evidence_and_reversibility_survive(self):
        recovery = next(row for row in self.rows if row["initiative"] == "restore_rollback_drills")
        self.assertIn("Database", recovery["owner"])
        self.assertIn("recovery", recovery["audit_evidence"])
        self.assertEqual(recovery["reversible"], "yes")


if __name__ == "__main__":
    unittest.main()
