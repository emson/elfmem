"""Tests for elfmem.seed — constitutional base and domain templates."""
from __future__ import annotations

import pytest

from elfmem.seed import CONSTITUTIONAL_SEED, get_template, list_templates


class TestConstitutionalSeed:
    def test_constitutional_seed_is_list(self) -> None:
        assert isinstance(CONSTITUTIONAL_SEED, list)

    def test_constitutional_seed_has_ten_blocks(self) -> None:
        assert len(CONSTITUTIONAL_SEED) == 10

    def test_each_block_has_content(self) -> None:
        for block in CONSTITUTIONAL_SEED:
            assert "content" in block
            assert len(block["content"]) > 20

    def test_each_block_has_tags(self) -> None:
        for block in CONSTITUTIONAL_SEED:
            assert "tags" in block
            assert isinstance(block["tags"], list)
            assert len(block["tags"]) >= 1

    def test_all_constitutional_blocks_carry_constitutional_tag(self) -> None:
        for block in CONSTITUTIONAL_SEED:
            assert "self/constitutional" in block["tags"]

    def test_constitutional_seed_not_mutated_by_get_template(self) -> None:
        original_len = len(CONSTITUTIONAL_SEED)
        _ = get_template("coding")
        assert len(CONSTITUTIONAL_SEED) == original_len


class TestListTemplates:
    def test_list_templates_returns_dict(self) -> None:
        result = list_templates()
        assert isinstance(result, dict)

    def test_list_templates_includes_coding(self) -> None:
        assert "coding" in list_templates()

    def test_list_templates_includes_research(self) -> None:
        assert "research" in list_templates()

    def test_list_templates_includes_assistant(self) -> None:
        assert "assistant" in list_templates()

    def test_list_templates_descriptions_are_non_empty(self) -> None:
        for name, description in list_templates().items():
            assert isinstance(description, str)
            assert len(description) > 0, f"Template '{name}' has empty description"


class TestGetTemplate:
    def test_get_template_returns_list(self) -> None:
        blocks = get_template("coding")
        assert isinstance(blocks, list)

    def test_get_template_coding_has_blocks(self) -> None:
        assert len(get_template("coding")) > 0

    def test_get_template_research_has_blocks(self) -> None:
        assert len(get_template("research")) > 0

    def test_get_template_assistant_has_blocks(self) -> None:
        assert len(get_template("assistant")) > 0

    def test_get_template_blocks_have_content(self) -> None:
        for name in list_templates():
            for block in get_template(name):
                assert "content" in block
                assert len(block["content"]) > 10

    def test_get_template_blocks_have_tags(self) -> None:
        for name in list_templates():
            for block in get_template(name):
                assert "tags" in block
                assert isinstance(block["tags"], list)

    def test_get_template_blocks_carry_template_tag(self) -> None:
        for name in list_templates():
            for block in get_template(name):
                template_tag = f"self/template/{name}"
                assert template_tag in block["tags"], (
                    f"Block in '{name}' template missing tag '{template_tag}'"
                )

    def test_get_template_unknown_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown template"):
            get_template("nonexistent")

    def test_get_template_error_lists_available(self) -> None:
        with pytest.raises(ValueError, match="coding"):
            get_template("nonexistent")

    def test_get_template_does_not_include_constitutional_blocks(self) -> None:
        """Templates are additive — they don't re-include constitutional blocks."""
        coding = get_template("coding")
        constitutional_contents = {b["content"] for b in CONSTITUTIONAL_SEED}
        for block in coding:
            assert block["content"] not in constitutional_contents

    def test_combining_constitutional_and_template(self) -> None:
        """CONSTITUTIONAL_SEED + get_template() is the intended usage."""
        full = CONSTITUTIONAL_SEED + get_template("coding")
        assert len(full) == len(CONSTITUTIONAL_SEED) + len(get_template("coding"))

    def test_templates_are_independent(self) -> None:
        """Each template returns its own blocks — no shared state."""
        coding = get_template("coding")
        research = get_template("research")
        coding_contents = {b["content"] for b in coding}
        research_contents = {b["content"] for b in research}
        assert coding_contents.isdisjoint(research_contents)
