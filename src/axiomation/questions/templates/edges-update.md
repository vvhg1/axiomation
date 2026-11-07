## This pass: the paper changed

A first reader wrote the edges and standings of an earlier version of this
paper. The sheet shows the new version and carries every line over to it:
a line stands under the block that holds its words now, and a block whose
text is unchanged keeps its lines and its done mark. Every block of the new
version is shown as it reads now; a block whose text changed also shows its
old text, under its block line, as `⟦was⟧ <the old text>`, once for each
block of the old version it came from. The `⟦was⟧` lines are fixed, like
the text.

**First, the Δ lines.** Under each changed block, write how the edit
changed what it says, one Δ line for each part listed below (one for the
block where none is listed), among the block's lines:

    Δ [<part>] <class> — what changed, in a few words
    Δ <class> — what changed, for the block as a whole

The class is one of eight:

- `unchanged`: it says what it said;
- `reworded`: the words changed, the meaning did not;
- `narrowed` / `widened`: it covers fewer cases, or more;
- `strengthened` / `weakened`: it asks or claims more, or less;
- `removed`: what it said is gone;
- `moved`: it says the same, elsewhere or of something else.

`unchanged` and `reworded` close the change there: nothing that relies on
it is asked again. Every other class carries the change on. Read a block
as the graph reads it, with the blocks that qualify it, each a premise of
what it says: an edit that writes into a block what one of its
qualifiers already assumed changes nothing it says with them.
`unchanged` stops the change, so write it only beyond doubt: for a block
whole, or on a block whose text did not change, quote its evidence in
the note, three words or more of the block or of a block that qualifies
it (one the edit did not change, for a block whose text did not change)
that show the edit does not move it; a block whose text did not change
is never `reworded` (`Δ unchanged — "every measure here is
finite": the cases the edit excludes never arise here`). In doubt, write
the class that carries the change on. Where the
change lies outside the parts listed, add a Δ line for the block as a
whole as well: an edge onto the block whole, onto its group, and its
qualifies edges go by that line, an edge onto a part by the part's.

A part whose words are gone (so marked below) cannot stay in the legend:
delete its legend line, and write `Δ [<part>] removed`; an edge that named
it names another part or the block whole. If the part still stands in
other words, quote them in its legend line instead, and give its class.

**Then the blocks open on condition.** A block the change may reach has
no done mark and a condition under the edge it would come through:
`⟦asked if <block> [<part>]'s Δ is not unchanged or reworded⟧`, or
`⟦meaning moved by rule: … of <block>, if its Δ is not unchanged or
reworded⟧`. Where your Δ lines make a condition true, check that block's
lines against the paper and the rules above, nearest the change first. A
block whose meaning moved by rule writes a Δ line of its own, as a changed
block does. A condition names a block's Δ: a block with no Δ line (one
whose own conditions are all false) closes every condition that names it,
as a Δ line `unchanged` or `reworded` does. Where no condition of a block
is true, leave its lines as they are and mark it done. The condition lines
are fixed.

The other blocks without a done mark are reopened (listed below, with
why): check every line against the paper and the rules above, give a new
block its edges and its standings, and mark each done when it reads. Every
block with a done mark is fixed: leave its lines as they are. Where a line
quoted words this version no longer holds, the hints below say where
something like them stands; quote the paper as it reads now.
