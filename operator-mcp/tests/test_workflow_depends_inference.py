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

    def test_for_each_range_and_items_interpolation(self):
        """for_each.range and for_each.items are interpolated at runtime
        (executor.py:1241 / :1272). Without scanning them, a dynamic range
        like ``1..${count.output_data.n}`` would fan count + the for_each
        step into the same wave and crash with "for_each range resolved
        to empty"."""
        # range case
        wf = load_workflow_from_dict(_wf([
            {"id": "count", "type": "agent", "agent": {"prompt": "how many"}},
            {"id": "body", "type": "agent", "agent": {"prompt": "iter"}},
            {
                "id": "loop",
                "type": "for_each",
                "for_each": {
                    "range": "1..${count.output_data.n}",
                    "steps": ["body"],
                },
            },
        ]))
        loop = wf.step_by_id("loop")
        assert loop is not None
        assert loop.depends_on == ["count"]

        # items case — both string interpolation and list-element scanning
        wf2 = load_workflow_from_dict(_wf([
            {"id": "alpha", "type": "agent", "agent": {"prompt": "a"}},
            {"id": "beta", "type": "agent", "agent": {"prompt": "b"}},
            {"id": "body", "type": "agent", "agent": {"prompt": "iter"}},
            {
                "id": "loop",
                "type": "for_each",
                "for_each": {
                    "items": ["${alpha.output}", "${beta.output}"],
                    "steps": ["body"],
                },
            },
        ]))
        loop2 = wf2.step_by_id("loop")
        assert loop2 is not None
        assert sorted(loop2.depends_on) == ["alpha", "beta"]

    def test_notify_and_human_approval_channel_id_interpolation(self):
        """notify.channel_id and human_approval.channel_id are interpolated
        at executor.py:2547 / :2596. Workflows that pick the channel ID
        dynamically (``${pick.output_data.channel}``) need an inferred dep
        on the picker step or the pause/notify step fans into the same
        wave with an empty channel."""
        wf = load_workflow_from_dict(_wf([
            {"id": "pick", "type": "agent", "agent": {"prompt": "pick channel"}},
            {
                "id": "ask",
                "type": "human_approval",
                "human_approval": {
                    "message": "approve?",
                    "channel": "discord",
                    "channel_id": "${pick.output_data.channel}",
                },
            },
            {
                "id": "tell",
                "type": "notify",
                "notify": {
                    "channels": ["discord"],
                    "channel_id": "${pick.output_data.channel}",
                    "message": "done",
                },
            },
        ]))
        ask = wf.step_by_id("ask")
        tell = wf.step_by_id("tell")
        assert ask is not None and tell is not None
        assert ask.depends_on == ["pick"]
        assert tell.depends_on == ["pick"]

    def test_parallel_child_ref_on_parent_deps_suppressed(self):
        """A parallel child that references a step already in the parallel
        parent's depends_on must NOT get an inferred dep on it. The parent
        gates the block; adding a cross-group edge would be rejected by the
        validator."""
        wf = load_workflow_from_dict(_wf([
            {"id": "upstream", "type": "agent", "agent": {"prompt": "do U"}},
            {
                "id": "cluster",
                "type": "parallel",
                "depends_on": ["upstream"],
                "parallel": {"steps": ["child"]},
            },
            {
                "id": "child",
                "type": "agent",
                "agent": {"prompt": "use ${upstream.output}"},
            },
        ]))
        child = wf.step_by_id("child")
        assert child is not None
        assert child.depends_on == []

    def test_parallel_child_ref_not_on_parent_deps_inferred(self):
        """If the parallel parent does NOT depend on the referenced step,
        suppression does not apply and the inferrer adds the dep. The
        downstream validator will reject this cross-group edge — that loud
        failure is the desired semantics."""
        wf = load_workflow_from_dict(_wf([
            {"id": "upstream", "type": "agent", "agent": {"prompt": "do U"}},
            {
                "id": "cluster",
                "type": "parallel",
                "parallel": {"steps": ["child"]},
            },
            {
                "id": "child",
                "type": "agent",
                "agent": {"prompt": "use ${upstream.output}"},
            },
        ]))
        child = wf.step_by_id("child")
        assert child is not None
        assert child.depends_on == ["upstream"]

    def test_nested_parallel_inner_child_ref_on_outer_deps_documents_gap(self):
        """Documents the current limitation: suppression only checks the
        IMMEDIATE parallel parent's depends_on, not transitively up the
        nesting chain. If an inner-parallel child references a step that
        only the OUTER parallel depends on, the inferrer still adds the dep
        and the validator will then reject it. This test pins that behavior
        to surface any future fix; it is documentation of the gap, not
        desired behavior."""
        wf = load_workflow_from_dict(_wf([
            {"id": "upstream", "type": "agent", "agent": {"prompt": "do U"}},
            {
                "id": "outer_p",
                "type": "parallel",
                "depends_on": ["upstream"],
                "parallel": {"steps": ["inner_p"]},
            },
            {
                "id": "inner_p",
                "type": "parallel",
                "parallel": {"steps": ["child"]},
            },
            {
                "id": "child",
                "type": "agent",
                "agent": {"prompt": "use ${upstream.output}"},
            },
        ]))
        child = wf.step_by_id("child")
        assert child is not None
        assert child.depends_on == ["upstream"]
