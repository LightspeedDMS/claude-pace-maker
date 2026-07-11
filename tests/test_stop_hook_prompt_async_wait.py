#!/usr/bin/env python3
"""
Unit tests for issue #87 — stop-hook validator prompt restructure.

Verifies the async-wait principle is stated BEFORE the "still running"
fallacy section (so weak verifier models anchor on the permissive rule
first), that the rule is expressed semantically (not as a brittle phrase
list), that awaiting-user-input is an explicit allow case, and that all
pre-existing sections/behaviors are preserved.

These are structural/content assertions on the externalized prompt file —
no LLM calls are made (this project forbids scripted E2E; prompt content
is verified statically here, agentic E2E is performed separately).
"""

import os
import unittest


def _load_prompt() -> str:
    """Load the stop-hook validator prompt file content."""
    import src.pacemaker.intent_validator as validator_module

    module_dir = os.path.dirname(validator_module.__file__)
    prompt_path = os.path.join(
        module_dir, "prompts", "stop", "stop_hook_validator_prompt.md"
    )
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()


class TestAsyncWaitPrincipleOrdering(unittest.TestCase):
    """The async-wait principle must appear BEFORE the fallacy section."""

    def test_core_principle_appears_before_fallacy_section(self):
        """Async-wait principle heading must precede the narrow fallacy heading."""
        content = _load_prompt()

        principle_idx = content.find("CORE PRINCIPLE")
        fallacy_idx = content.find("STILL RUNNING")

        self.assertNotEqual(
            principle_idx, -1, "CORE PRINCIPLE section not found in prompt"
        )
        self.assertNotEqual(
            fallacy_idx, -1, "STILL RUNNING fallacy section not found in prompt"
        )
        self.assertLess(
            principle_idx,
            fallacy_idx,
            "CORE PRINCIPLE (async-wait) must appear BEFORE the "
            "STILL RUNNING fallacy section so weak models anchor on the "
            "permissive rule first",
        )

    def test_tempo_check_appears_before_core_principle(self):
        """Tempo liveliness check must remain the first specialized section,
        appearing before the CORE PRINCIPLE (async-wait) section."""
        content = _load_prompt()
        tempo_idx = content.upper().find("TEMPO LIVELINESS CHECK")
        principle_idx = content.find("CORE PRINCIPLE")

        self.assertNotEqual(
            tempo_idx, -1, "TEMPO LIVELINESS CHECK section not found in prompt"
        )
        self.assertNotEqual(
            principle_idx, -1, "CORE PRINCIPLE section not found in prompt"
        )
        self.assertLess(
            tempo_idx,
            principle_idx,
            "TEMPO LIVELINESS CHECK should appear before CORE PRINCIPLE",
        )


class TestSemanticWaitingRule(unittest.TestCase):
    """The waiting rule must be stated as a semantic principle, not just a phrase list."""

    def test_semantic_rule_language_present(self):
        """Prompt must state the rule applies to ANY async mechanism semantically."""
        content = _load_prompt()

        self.assertIn("ANY asynchronous mechanism", content)
        self.assertIn("re-awaken", content)
        self.assertIn(
            "SEMANTIC",
            content.upper(),
            "Prompt must explicitly say this is a semantic rule, not a "
            "literal phrase match",
        )

    def test_phrase_list_framed_as_illustrative_not_matching_mechanism(self):
        """The old phrase list must be reframed as non-exhaustive examples."""
        content = _load_prompt()

        # The phrase list must still exist as examples (helps some models)
        self.assertIn("awaiting results", content)
        # But it must be explicitly framed as illustrative/non-exhaustive
        self.assertIn("non-exhaustive", content.lower())

    def test_waiting_never_by_itself_evidence_of_incomplete_work(self):
        """Prompt must state waiting is never by itself evidence of incomplete
        work - asserted as one distinctive phrase (whitespace-normalized to
        tolerate line wrapping), not loose single-word substrings that could
        match unrelated text elsewhere in the prompt."""
        content = _load_prompt()
        normalized = " ".join(content.split())
        self.assertIn("NEVER by itself evidence of incomplete work", normalized)


class TestAwaitingUserInputAllowance(unittest.TestCase):
    """WHEN TO ALLOW must include an explicit awaiting-user-input case."""

    def test_when_to_allow_section_mentions_user_question(self):
        """WHEN TO ALLOW section must allow stoppage when awaiting user input."""
        content = _load_prompt()

        allow_idx = content.find("WHEN TO ALLOW")
        self.assertNotEqual(allow_idx, -1, "WHEN TO ALLOW section not found")

        block_idx = content.find("WHEN TO BLOCK")
        self.assertNotEqual(block_idx, -1, "WHEN TO BLOCK section not found")
        self.assertGreater(
            block_idx, allow_idx, "WHEN TO BLOCK should follow WHEN TO ALLOW"
        )

        allow_section = content[allow_idx:block_idx]
        self.assertIn("asks the user a question", allow_section)
        self.assertIn("user decision", allow_section)


class TestNarrowFallacyExceptionPreserved(unittest.TestCase):
    """The genuine fallacy case (synchronous claim, no async signal) must remain."""

    def test_narrow_exception_requires_no_async_signal(self):
        """Blocking on 'still running' claims must be scoped to no-async-signal cases."""
        content = _load_prompt()
        fallacy_idx = content.find("STILL RUNNING")
        self.assertNotEqual(fallacy_idx, -1)
        fallacy_section = content[fallacy_idx : fallacy_idx + 3000]
        self.assertIn("no async signal", fallacy_section.lower())

    def test_launch_failure_case_preserved(self):
        """Blocking on a visibly-failed async launch must remain documented,
        asserted via a distinctive phrase (whitespace-normalized) rather than
        the bare word 'FAILED' which could match unrelated text such as
        'PASS / FAIL / INCONCLUSIVE' in the E2E section."""
        content = _load_prompt()
        normalized = " ".join(content.split())
        self.assertIn("background launch itself FAILED", normalized)


class TestAwaitingUserInputGuardrail(unittest.TestCase):
    """The user-input allowance must be scoped to user-owned choices and
    must not conflict with the analysis-paralysis rule (issue #87 remediation)."""

    def test_guardrail_scoped_to_user_owned_choices_and_defers_to_analysis_paralysis(
        self,
    ):
        """The allowance must explicitly exclude deferring already-requested
        actionable work, and state that the ANALYSIS PARALYSIS rule wins
        (BLOCK) in that case. Uses invariant substrings, not brittle exact
        wording, so minor phrasing tweaks don't break this test."""
        content = _load_prompt()

        allow_idx = content.find("WHEN TO ALLOW")
        block_idx = content.find("WHEN TO BLOCK")
        self.assertNotEqual(allow_idx, -1, "WHEN TO ALLOW section not found")
        self.assertNotEqual(block_idx, -1, "WHEN TO BLOCK section not found")

        allow_section = content[allow_idx:block_idx]

        # Scoped to genuinely user-owned choices (not a blanket allowance)
        self.assertIn("user-owned", allow_section.lower())

        # Explicitly excludes deferring work the user already asked for
        self.assertIn("already asked", allow_section.lower())

        # Precedence is resolved: analysis paralysis wins in that case
        self.assertIn("ANALYSIS PARALYSIS", allow_section.upper())
        self.assertIn("BLOCK", allow_section)

    def test_user_decision_justification_does_not_borrow_async_rewake_language(
        self,
    ):
        """The user-decision bullet's own justification must stand on its
        own (the user must reply before progress is possible) rather than
        citing the async runtime re-awaken mechanism, which belongs to the
        separate async-mechanism bullet."""
        content = _load_prompt()

        allow_idx = content.find("WHEN TO ALLOW")
        block_idx = content.find("WHEN TO BLOCK")
        allow_section = content[allow_idx:block_idx]

        user_decision_start = allow_section.find("user decision")
        self.assertNotEqual(user_decision_start, -1, "user decision bullet not found")
        user_decision_bullet = allow_section[
            user_decision_start : user_decision_start + 700
        ]

        self.assertIn("must reply", user_decision_bullet)
        self.assertNotIn(
            "re-awaken",
            user_decision_bullet,
            "user-decision bullet must not borrow the async-mechanism's "
            "re-awaken justification",
        )


class TestPreservedExistingSections(unittest.TestCase):
    """All pre-existing behaviors/sections must remain intact."""

    def test_conversation_context_placeholder_present(self):
        content = _load_prompt()
        self.assertIn("{conversation_context}", content)

    def test_tempo_liveliness_check_preserved(self):
        content = _load_prompt()
        self.assertIn("TEMPO LIVELINESS CHECK", content.upper())

    def test_analysis_paralysis_detection_preserved(self):
        content = _load_prompt()
        self.assertIn("ANALYSIS PARALYSIS", content.upper())

    def test_unrecoverable_loop_detection_preserved(self):
        content = _load_prompt()
        self.assertIn("UNRECOVERABLE LOOP", content.upper())

    def test_e2e_evidence_requirement_preserved(self):
        content = _load_prompt()
        self.assertIn("E2E EVIDENCE", content.upper())
        self.assertIn("E2E TEST COMPLETION REPORT", content)

    def test_response_formats_preserved(self):
        content = _load_prompt()
        self.assertIn("APPROVED", content)
        self.assertIn("COMPLETE:", content)
        self.assertIn("BLOCKED:", content)


if __name__ == "__main__":
    unittest.main()
