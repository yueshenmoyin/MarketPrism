from pathlib import Path


def test_tauri_backend_child_is_stopped_on_app_exit():
    source = Path("src-tauri/src/lib.rs").read_text(encoding="utf-8")

    assert "impl Drop for BackendState" in source
    assert "child.kill()" in source
    assert "child.wait()" in source
