"""Turn Django test failures in test.log into GitHub annotations, so they show on the run summary."""
import re
import sys

log = open(sys.argv[1], encoding="utf-8", errors="replace").read()
blocks = re.split(r"^=+\n", log, flags=re.M)
failures = [b for b in blocks if re.match(r"(ERROR|FAIL): ", b)]


def escape(text):
    return text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")


for block in failures[:9]:
    title, _, body = block.partition("\n")
    print(f"::error title={escape(title.strip())}::{escape(body.strip()[-3500:])}")
if len(failures) > 9:
    rest = "\n".join(b.partition("\n")[0] for b in failures[9:])
    print(f"::error title={len(failures) - 9} more failures::{escape(rest[:3500])}")
if not failures:
    print(f"::error title=Tests failed::{escape(log[-3500:])}")
