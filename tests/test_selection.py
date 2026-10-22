from manifests import entry, manifest
from minecraft_setup.engine.selection import default_choice, needed_by, resolve

REQ = entry("40 gameplay - waystones.jar", side="required", ids=["waystones"], depends=["balm"])
BALM = entry("40 lib [for waystones] - balm.jar", side="required", ids=["balm"])
A = entry("20 visual - a.jar", ids=["a"], depends=["l1"])
B = entry("20 visual - b.jar", ids=["b"], depends=["l1"])
L1 = entry("01 lib [for a+b] - l1.jar", ids=["l1"], depends=["l2"])
L2 = entry("01 lib [for l1] - l2.jar", ids=["l2"])
OFF = entry("30 ui - off.jar", ids=["off"], default=False)
M = manifest(REQ, BALM, A, B, L1, L2, OFF)


def test_required_always_enabled():
    assert {REQ.name, BALM.name} <= resolve(M, frozenset())


def test_unchecked_mod_drops_its_lib():
    enabled = resolve(M, frozenset())
    assert L1.name not in enabled and L2.name not in enabled and A.name not in enabled


def test_shared_lib_stays():
    enabled = resolve(M, frozenset({B.name}))
    assert L1.name in enabled and A.name not in enabled
    assert needed_by(M, enabled)[L1.name] == [B.name]


def test_chosen_mod_pulls_lib_transitively():
    assert resolve(M, frozenset({A.name})) == {REQ.name, BALM.name, A.name, L1.name, L2.name}


def test_dependency_pulls_a_plain_mod_back_on():
    iris = entry("11 perf - iris.jar", ids=["iris"], depends=["sodium"])
    sodium = entry("10 perf - sodium.jar", ids=["sodium"])
    m = manifest(iris, sodium)
    assert resolve(m, frozenset({iris.name})) == {iris.name, sodium.name}


def test_unknown_dependency_is_ignored():
    odd = entry("20 visual - odd.jar", ids=["odd"], depends=["sodium-api"])
    assert resolve(manifest(odd), frozenset({odd.name})) == {odd.name}


def test_unknown_chosen_name_ignored():
    assert resolve(M, frozenset({"nope.jar"})) == {REQ.name, BALM.name}


def test_libraries_cannot_be_chosen_directly():
    assert L1.name not in resolve(M, frozenset({L1.name}))


def test_orphan_library_is_choosable():
    orphan = entry("01 lib [for gone] - orphan.jar", ids=["orphan"])
    m = manifest(orphan)
    assert default_choice(m) == {orphan.name}
    assert resolve(m, frozenset({orphan.name})) == {orphan.name}


def test_pack_archives_are_not_choices():
    shader = entry("Comp.zip", dir="shaderpacks")
    m = manifest(A, L1, L2, shader)
    assert shader.name not in default_choice(m)
    assert shader.name not in resolve(m, frozenset({shader.name, A.name}))


def test_default_choice_is_client_defaults_only():
    assert default_choice(M) == {A.name, B.name}
