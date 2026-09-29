When the tool is Write and the file at the path above ALREADY EXISTS on disk,
PROPOSED CODE above is normally a diff — but this change is large enough that
a diff risked hiding early parts of it, so PROPOSED CODE above is instead the
COMPLETE, final new content that will REPLACE the existing file's current
content in full. It is NOT the content of a new file being created.

NEVER reject on the grounds that the file "already exists" — that is the
EXPECTED state here. Base your entire decision SOLELY on the PROPOSED CODE
shown above and the declared intent.
