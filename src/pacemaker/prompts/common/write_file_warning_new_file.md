When the tool is Write, the file at the path above is being CREATED by this
operation — it does NOT exist on disk yet, and you have NO filesystem access.
The PROPOSED CODE above is the COMPLETE, final content of that new file; you
already have everything you need to review it.

NEVER reject on the grounds that the file "does not exist", "could not be read",
"was not created", or "the path appears incorrect / wrong" — that is the
EXPECTED state for a file being created, not a problem, and NEVER a reason to
block. Do not ask for the file to be created first. Base your entire decision
SOLELY on the PROPOSED CODE shown above and the declared intent.
