from minecraft_setup.engine.sides import classify


def test_classify_rules():
    assert classify(2, ("required", "required"), "client") == "client"
    assert classify(4, ("required", "unsupported"), None) == "required"
    assert classify(None, ("optional", "required"), None) == "required"
    assert classify(2, ("required", "optional"), None) == "client"
    assert classify(None, None, None) == "client"
