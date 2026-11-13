"""First-party workflow DAG validation only; never execute steps or agents."""

import unittest

from operator_mcp.workflow.schema import StepDef, StepType, WorkflowDef
from operator_mcp.workflow.validator import validate_workflow


def chain(count: int) -> WorkflowDef:
    steps = [
        StepDef(
            id=f"step_{i}", type=StepType.NOTIFY,
            depends_on=[f"step_{i - 1}"] if i else [],
        )
        for i in range(count)
    ]
    # Legal YAML ordering: downstream tasks appear before their dependencies.
    # This forces cycle detection to traverse the whole dependency depth.
    return WorkflowDef(name="native_graph_contract", steps=list(reversed(steps)))


class NativeDagValidation(unittest.TestCase):
    def test_small_chain_is_valid_and_topologically_sorted(self):
        result = validate_workflow(chain(4))
        self.assertTrue(result.valid, [e.message for e in result.errors])
        self.assertEqual(result.execution_order, [f"step_{i}" for i in range(4)])

    def test_long_valid_chain_does_not_overflow_python_recursion(self):
        result = validate_workflow(chain(1200))
        self.assertTrue(result.valid, [e.message for e in result.errors])
        self.assertEqual(len(result.execution_order), 1200)
        self.assertEqual(result.execution_order[0], "step_0")
        self.assertEqual(result.execution_order[-1], "step_1199")

    def test_cycle_is_still_rejected(self):
        wf = chain(4)
        wf.steps[0].depends_on = ["step_3"]
        result = validate_workflow(wf)
        self.assertFalse(result.valid)
        self.assertTrue(any("Dependency cycle" in e.message for e in result.errors))

    def test_two_independent_roots_stay_valid(self):
        wf = WorkflowDef(name="two_roots", steps=[
            StepDef(id="left", type=StepType.NOTIFY),
            StepDef(id="right", type=StepType.NOTIFY),
            StepDef(id="join", type=StepType.NOTIFY, depends_on=["left", "right"]),
        ])
        result = validate_workflow(wf)
        self.assertTrue(result.valid)
        self.assertEqual(result.execution_order[-1], "join")


if __name__ == "__main__":
    unittest.main()
