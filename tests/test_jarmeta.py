import io
import json
import zipfile

from minecraft_setup.engine.jarmeta import JarMeta, read_jar


def zip_bytes(entries: dict[str, bytes | str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def jar(tmp_path, entries, name="m.jar"):
    path = tmp_path / name
    path.write_bytes(zip_bytes(entries))
    return path


def test_fabric_ids_provides_and_nested(tmp_path):
    nested = zip_bytes({"fabric.mod.json": json.dumps({"id": "n"})})
    meta = {"id": "a", "provides": ["a2"], "depends": {"minecraft": "*", "b": "*"}, "jars": [{"file": "META-INF/jars/n.jar"}]}
    path = jar(tmp_path, {"fabric.mod.json": json.dumps(meta), "META-INF/jars/n.jar": nested})
    assert read_jar(path, "fabric") == JarMeta(frozenset({"a", "a2", "n"}), frozenset({"b"}))


def test_neoforge_required_only(tmp_path):
    toml = """
[[mods]]
modId="c"

[[dependencies.c]]
modId="d"
type="required"

[[dependencies.c]]
modId="e"
type="optional"

[[dependencies.c]]
modId="neoforge"
type="required"

[[dependencies.c]]
modId="f"
mandatory=true
"""
    path = jar(tmp_path, {"META-INF/neoforge.mods.toml": toml})
    assert read_jar(path, "neoforge") == JarMeta(frozenset({"c"}), frozenset({"d", "f"}))


def test_neoforge_jarjar_nested(tmp_path):
    nested = zip_bytes({"META-INF/neoforge.mods.toml": '[[mods]]\nmodId="x"\n'})
    metadata = json.dumps({"jars": [{"path": "META-INF/jarjar/x.jar"}]})
    path = jar(
        tmp_path,
        {
            "META-INF/neoforge.mods.toml": '[[mods]]\nmodId="top"\n',
            "META-INF/jarjar/metadata.json": metadata,
            "META-INF/jarjar/x.jar": nested,
        },
    )
    assert read_jar(path, "neoforge").ids == frozenset({"top", "x"})


def test_neoforge_language_loader_is_a_dependency(tmp_path):
    kotlin = jar(tmp_path, {"META-INF/neoforge.mods.toml": 'modLoader="kotlinforforge"\n[[mods]]\nmodId="ipn"\n'})
    java = jar(tmp_path, {"META-INF/neoforge.mods.toml": 'modLoader="javafml"\n[[mods]]\nmodId="j"\n'}, "j.jar")
    lowcode = jar(tmp_path, {"META-INF/neoforge.mods.toml": 'modLoader="lowcodefml"\n[[mods]]\nmodId="l"\n'}, "l.jar")
    assert read_jar(kotlin, "neoforge") == JarMeta(frozenset({"ipn"}), frozenset({"kotlinforforge"}))
    assert read_jar(java, "neoforge").depends == frozenset()
    assert read_jar(lowcode, "neoforge").depends == frozenset()


def test_non_mod_jar_is_empty(tmp_path):
    assert read_jar(jar(tmp_path, {"a.txt": "x"}), "fabric") == JarMeta(frozenset(), frozenset())


def test_fabric_json_with_control_chars(tmp_path):
    raw = '{"id": "d", "description": "line one\nline two"}'
    assert read_jar(jar(tmp_path, {"fabric.mod.json": raw}), "fabric").ids == frozenset({"d"})


def test_multiloader_jar_reads_only_its_loader(tmp_path):
    fabric = json.dumps({"id": "m", "depends": {"fabric-api": "*"}})
    toml = '[[mods]]\nmodId="m"\n[[dependencies.m]]\nmodId="lib"\ntype="required"\n'
    path = jar(tmp_path, {"fabric.mod.json": fabric, "META-INF/neoforge.mods.toml": toml})
    assert read_jar(path, "neoforge") == JarMeta(frozenset({"m"}), frozenset({"lib"}))
    assert read_jar(path, "fabric") == JarMeta(frozenset({"m"}), frozenset({"fabric-api"}))


def test_loader_bundled_ids_are_builtin(tmp_path):
    path = jar(tmp_path, {"fabric.mod.json": json.dumps({"id": "l", "depends": {"mixinextras": "*"}})})
    assert read_jar(path, "fabric").depends == frozenset()
