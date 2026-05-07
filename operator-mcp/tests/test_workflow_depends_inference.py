"""Tests for load-time depends_on inference from ${step.field} interpolations.

The runtime wave scheduler (executor.py wave loop) reads only
``step.depends_on`` to decide what to launch. Architect-generated YAML
(and many hand-written workflows) express deps purely via prompt /
template references — without inference, those steps fan into Wave 1 and
downstream steps see empty interpolated inputs.

Mirror coverage: same five surfaces the frontend's tasksToFlow edge
inference handles in yamlSync.ts.
"""
from __future__ import annotations

import logging

import pytest

from operator_mcp.workflow.loader import (
    _infer_depends_on,
    load_workflow_from_dict,
)


def _wf(steps: list[dict]) -> dict:
    return {"name": "test-wf", "steps": steps}


class TestDependsOnInference:
    def test_siblings_inferred_from_prompt_interpolation(self):
        """The bug: three siblings, C's prompt references A and B but C
        has no explicit depends_on. After load, both refs become deps."""
        wf = load_workflow_from_dict(_wf([
            {"id": "a", "type": "agent", "agent": {"prompt": "do A"}},
            {"id": "b", "type": "agent", "agent": {"prompt": "do B"}},
            {
                "id": "c",
                "type": "agent",
                "agent": {
                    "prompt": "combine ${a.output} with ${b.output}",
                },
            },
        ]))
        c = wf.step_by_id("c")
        assert c is not None
        assert sorted(c.depends_on) == ["a", "b"]

    def test_explicit_depends_on_not_double_added(self):
        wf = load_workflow_from_dict(_wf([
            {"id": "a", "type": "agent", "agent": {"prompt": "do A"}},
            {
                "id": "b",
                "type": "agent",
                "depends_on": ["a"],
                "agent": {"prompt": "extend ${a.output}"},
            },
        ]))
        b = wf.step_by_id("b")
        assert b is not None
        assert b.depends_on == ["a"]

    def test_inputs_namespace_does_not_create_dep(self):
        wf = load_workflow_from_dict(_wf([
            {
                "id": "a",
                "type": "agent",
                "agent": {"prompt": "research ${inputs.topic}"},
            },
        ]))
        a = wf.step_by_id("a")
        assert a is not None
        assert a.depends_on == []

    def test_nonexistent_ref_does_not_add_false_dep(self, caplog):
        """Validator surfaces missing-step refs separately. The inferrer
        must not invent a dep on a step that doesn't exist (would mask
        the validator error and likely cause wave deadlock)."""
        with caplog.at_level(logging.WARNING):
            wf = load_workflow_from_dict(_wf([
                {
                    "id": "a",
                    "type": "agent",
                    "agent": {"prompt": "use ${nonexistent.output}"},
                },
            ]))
        a = wf.step_by_id("a")
        assert a is not None
        assert a.depends_on == []

    def test_self_reference_skipped(self):
        wf = load_workflow_from_dict(_wf([
            {
                "id": "a",
                "type": "agent",
                # Pathological but should never self-loop the graph.
                "agent": {"prompt": "improve on ${a.output}"},
            },
        ]))
        a = wf.step_by_id("a")
        assert a is not None
        assert a.depends_on == []

    def test_skip_namespaces_inputs_trigger_env_loop_for_each_previous(self):
        """All special namespaces in executor.interpolate() are skipped."""
        wf = load_workflow_from_dict(_wf([
            {"id": "src", "type": "agent", "agent": {"prompt": "p"}},
            {
                "id": "consumer",
                "type": "agent",
                "agent": {
                    "prompt": (
                        "i=${inputs.x} t=${trigger.entity_kref} "
                        "e=${env.HOME} l=${loop.iteration} "
                        "f=${for_each.index} p=${previous.src.output} "
                        "r=${rejection.feedback} run=${run_id}"
                    ),
                },
            },
        ]))
        c = wf.step_by_id("consumer")
        assert c is not None
        # No real step refs in the prompt — only special namespaces — so
        # depends_on must remain empty even though "src" exists.
        assert c.depends_on == []

    def test_idempotent(self):
        wf = load_workflow_from_dict(_wf([
            {"id": "a", "type": "agent", "agent": {"prompt": "do A"}},
            {
                "id": "b",
                "type": "agent",
                "agent": {"prompt": "extend ${a.output}"},
            },
        ]))
        b = wf.step_by_id("b")
        assert b is not None
        assert b.depends_on == ["a"]
        # Run inference again — must not duplicate.
        _infer_depends_on(wf)
        assert b.depends_on == ["a"]

    def test_shell_command_interpolation(self):
        wf = load_workflow_from_dict(_wf([
            {"id": "build", "type": "agent", "agent": {"prompt": "build"}},
            {
                "id": "deploy",
                "type": "shell",
                "shell": {"command": "echo ${build.output_data.artifact}"},
            },
        ]))
        deploy = wf.step_by_id("deploy")
        assert deploy is not None
        assert deploy.depends_on == ["build"]

    def test_output_template_interpolation(self):
        wf = load_workflow_from_dict(_wf([
            {"id": "a", "type": "agent", "agent": {"prompt": "do A"}},
            {"id": "b", "type": "agent", "agent": {"prompt": "do B"}},
            {
                "id": "report",
                "type": "output",
                "output": {
                    "format": "markdown",
                    "template": "A: ${a.output}\n\nB: ${b.output}",
                },
            },
        ]))
        report = wf.step_by_id("report")
        assert report is not None
        assert sorted(report.depends_on) == ["a", "b"]

    def test_python_args_dict_interpolation(self):
        wf = load_workflow_from_dict(_wf([
            {"id": "fetch", "type": "agent", "agent": {"prompt": "fetch"}},
            {
                "id": "transform",
                "type": "python",
                "python": {
                    "code": "import json,sys; print(json.dumps({}))",
                    "args": {"payload": "${fetch.output}"},
                },
            },
        ]))
        t = wf.step_by_id("transform")
        assert t is not None
        assert t.depends_on == ["fetch"]
