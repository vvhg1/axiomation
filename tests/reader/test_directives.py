"""The five directives: their tokenizer."""

from __future__ import annotations

from axiomation.reader.directives import (
    DirectiveKind,
    parse_directives,
    split_list,
    strip_directives,
)


class TestParseDirectives:
    def test_meta_pairs(self):
        found = parse_directives(["<!-- @meta rigor=argued assumes=+ass:g -->"])
        assert found[0].kind is DirectiveKind.META
        assert dict(found[0].pairs) == {"rigor": "argued", "assumes": "+ass:g"}

    def test_assumptions_list(self):
        found = parse_directives(["<!-- @assumptions ass:a, ass:b -->"])
        assert split_list(found[0].raw) == ("ass:a", "ass:b")

    def test_a_value_may_be_quoted_so_a_label_may_carry_a_space(self):
        """A paper may label most of its results with a space, so a
        directive must be able to name such a label."""
        found = parse_directives(['<!-- @claim C4 from="thm:general bound" -->'])
        assert dict(found[0].pairs) == {"from": "thm:general bound"}
        assert found[0].words == ("C4",)
        assert split_list(found[0].pairs["from"]) == ("thm:general bound",)

    def test_a_quoted_positional_word_carries_a_space_too(self):
        """`@node` names its target positionally, and `split` writes it."""
        found = parse_directives(['<!-- @node "def:confidence set" -->'])
        assert found[0].first == "def:confidence set"

    def test_a_quoted_assumes_carries_a_space(self):
        found = parse_directives(['<!-- @meta rigor=argued assumes="ass:c set" -->'])
        assert dict(found[0].pairs) == {"rigor": "argued", "assumes": "ass:c set"}

    def test_a_comma_inside_a_quoted_value_still_separates_targets(self):
        """The quotes bound the value; `from=` is a list either way."""
        found = parse_directives(['<!-- @claim C5 from="thm:a b,cor:c d" -->'])
        assert split_list(found[0].pairs["from"]) == ("thm:a b", "cor:c d")

    def test_quoting_is_optional(self):
        """Unquoted values parse unchanged."""
        found = parse_directives(["<!-- @claim C6 from=thm:chain,cor:rate -->"])
        assert split_list(found[0].pairs["from"]) == ("thm:chain", "cor:rate")

    def test_an_unbalanced_quote_parses_rather_than_raises(self):
        """A typing mistake in a manuscript is not a crash. It reads as a
        target nothing defines, which a later check would report."""
        found = parse_directives(['<!-- @claim C7 from="thm:unclosed -->'])
        assert found[0].pairs["from"] == '"thm:unclosed'

    def test_a_flag_survives_a_quoted_value(self):
        found = parse_directives(['<!-- @claim C8 from="thm:a b" start -->'])
        assert found[0].flag("start")

    def test_sorry_keeps_free_text(self):
        found = parse_directives(["<!-- @sorry lower bound pending -->"])
        assert found[0].first == "lower bound pending"
        assert dict(found[0].pairs) == {}

    def test_claim_forms(self):
        found = parse_directives(
            ["<!-- @claim C1 from=thm:a -->", "<!-- @claim C2 start -->"]
        )
        assert found[0].first == "C1"
        assert found[0].pairs["from"] == "thm:a"
        assert found[1].flag("start")

    def test_node_include(self):
        found = parse_directives(["<!-- @node thm:chain -->"])
        assert found[0].kind is DirectiveKind.NODE
        assert found[0].first == "thm:chain"

    def test_two_on_one_line_keep_their_columns(self):
        line = "text <!-- @claim C1 -->  more <!-- @claim C2 start -->"
        found = parse_directives([line])
        assert [item.start for item in found] == [5, 30]

    def test_unknown_name_is_prose(self):
        assert parse_directives(["<!-- @todo tidy this up -->"]) == []

    def test_strip_directives_keeps_offsets(self):
        line = "a <!-- @claim C1 --> b"
        blanked = strip_directives(line)
        assert len(blanked) == len(line)
        assert "@claim" not in blanked
        assert blanked.split() == ["a", "b"]
