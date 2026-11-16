"""Rejecting one part of a split upload also covers its other parts (v0.260.12)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api"))
import indexer_search as ix  # noqa: E402

fails = []
def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        fails.append(name)

P1 = "Hookup.Therapy.ep.15.Well.Groomed.Part.1.and.2..Alex.Adams,.Stella.Barey.1080p._part.1"
P2 = P1.replace("_part.1", "_part.2")
split = ix._split_rejected(["exact:" + P1])
check("the same part is rejected", ix._is_rejected(P1, split))
check("the other part is rejected too", ix._is_rejected(P2, split))
check("a 'Part 1 and 2' in the middle of the name is not a part marker", ix._exact_key("A.Scene.Part.1.and.2.1080p") == "a scene part 1 and 2 1080p")
check("cd2 / 1of2 markers", ix._exact_key("Some.Release.1080p.cd2") == ix._exact_key("Some.Release.1080p.cd1") == "some release 1080p"
      and ix._exact_key("Some.Release.1of2") == "some release")
check("another encode of the same release is still not rejected", not ix._is_rejected(P1.replace("1080p", "720p"), split))
print("FAILED: " + ", ".join(fails) if fails else "ALL OK")
sys.exit(1 if fails else 0)
