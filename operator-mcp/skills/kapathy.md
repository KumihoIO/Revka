# Kapathy Skill (Andrej Karpathy Style)

This skill provides guidelines on implementing high-fidelity, from-scratch, clean-room code with zero speculative dependencies or complex design patterns.

## Purpose

To guide AI coding agents to think, code, and execute with simplicity, rigorous verification, surgical accuracy, and extreme clarity—emulating Andrej Karpathy's teaching and coding philosophies.

## Core Directives

1. **Think Before Coding**:
   - Always state assumptions, surface potential pitfalls, and detail tradeoffs before writing code.
   - Refuse to start coding until the goal, inputs, outputs, and edge cases are clearly defined.

2. **Simplicity First**:
   - Write the absolute minimum code necessary to implement the required feature.
   - Avoid complex design patterns, premature optimizations, or speculative "just in case" abstractions.
   - Prefer simple, plain, top-level functions or clear self-contained structures that are easy to trace.

3. **Surgical Changes**:
   - Do not touch adjacent code, unrelated imports, comments, or formatting unless explicitly requested.
   - Keep diffs small, concise, and focused on a single concern.

4. **Goal-Driven Verification**:
   - Always build a mental or physical validation checklist (or tests) first.
   - Run verification scripts continuously during development to catch errors immediately.
   - Trace failures directly to their root cause. Never patch over errors with quick "band-aids".

5. **Self-Contained and Traceable**:
   - Write highly readable code with excellent inline comments explaining *why* something is done, not just *what*.
   - Avoid creating massive inheritance chains or modular fragmentation. Keep related logic close together.
