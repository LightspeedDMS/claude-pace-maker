When the tool is Write and the file at the path above ALREADY EXISTS on disk,
PROPOSED CODE above is a UNIFIED DIFF of the CURRENT on-disk content vs. the
new content about to be written (`-` = removed, `+` = added, unmarked =
unchanged context) — NOT the complete file, and this is NOT a new file being
created.

CHECK 2 and CHECK 3 apply only to added (`+`) lines; unmarked context and
removed (`-`) lines are shown only to verify the direction and scope of the
change.

NEVER reject on the grounds that the file "already exists" — that is the
EXPECTED state here. Base your entire decision on the diff shown above and
the declared intent.
