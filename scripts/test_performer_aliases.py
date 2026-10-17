"""Tests (v0.218.2): a release naming the performer by the scene's "as" credit or a full-name alias counts. Run: venv/bin/python scripts/test_performer_aliases.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import indexer_search as ix
from indexer_search import AliasName

FAILS = []
def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

entry = {"as": "Kitty Wild", "performer": {"name": "Xenia Blondi", "aliases": ["Briana", "Kitty Wild", "Ksenia", "Lexi", "Xenia Virgin", "Ksenia Blonde"]}}
extra = ix.alias_variants(entry)
check("the 'as' credit and full-name aliases are kept, short single words are not, no duplicates",
      [str(a) for a in extra] == ["Kitty Wild", "Xenia Virgin", "Ksenia Blonde"] and all(a.of == "Xenia Blondi" for a in extra))
check("a performer without extras gives none", ix.alias_variants({"as": None, "performer": {"name": "Stanley Johnson", "aliases": ["Stanislav"]}}) == [])

TITLE, DATE = "A businessman and his whore", "2026-10-15"
REL = "Hunt4K.26.10.15.Kitty.Wild.XXX.1080p.MP4-P0RNL0V3R"
plain = ["Xenia Blondi", "Stanley Johnson"]
with_alias = ["Xenia Blondi", *extra, "Stanley Johnson"]
check("without the alias the release is not recognised (the case)", not ix._candidate_plausible(TITLE, DATE, plain, REL))
check("with the alias: same date + a credited name -> plausible", ix._candidate_plausible(TITLE, DATE, with_alias, REL))
check("... but only with the right date", not ix._candidate_plausible(TITLE, "2026-09-01", with_alias, REL.replace("26.10.15", "26.10.15")))
check("a release of another day naming the alias is still no match", not ix._candidate_plausible(TITLE, DATE, with_alias, "Hunt4K.26.10.01.Kitty.Wild.XXX.1080p.MP4-GRP"))
check("primary_names drops the aliases", ix.primary_names(with_alias) == plain)
q = ix._build_queries(TITLE, with_alias, "Hunt 4K", DATE)
check("queries use the performers' own names only", "Xenia Blondi Stanley Johnson" in q and not any("Kitty" in x for x in q))
check("'all performers named' (the review rule) counts the performers' own names only, never an alias",
      not ix._all_performers_plausible(["Xenia Blondi", AliasName("Kitty Wild", "Xenia Blondi")], "x.Kitty.Wild.y")
      and ix._all_performers_plausible(["Xenia Blondi", AliasName("Kitty Wild", "Xenia Blondi")], "x.Xenia.Blondi.y"))
check("the aliases' words are 'known' words: a release naming just them has no foreign words",
      ix._foreign_words(TITLE, with_alias, REL) == [] and ix._foreign_words(TITLE, plain, REL) != [])

print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
