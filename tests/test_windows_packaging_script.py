import json
from pathlib import Path


def test_windows_packaging_script_entrypoint_is_wired():
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "package_windows_desktop.ps1"
    package_json = json.loads((root / "package.json").read_text(encoding="utf-8"))

    assert script.exists()
    assert package_json["scripts"]["package:windows"] == (
        "powershell -ExecutionPolicy Bypass -File scripts/package_windows_desktop.ps1"
    )

    content = script.read_text(encoding="utf-8")
    assert "function Invoke-Native" in content
    assert "function Invoke-TauriBuild" in content
    assert '"run", "tauri", "--", "build"' in content
    assert "src-tauri\\target\\release\\bundle" in content


def test_windows_smoke_build_uses_release_gui_exe():
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "package_windows_desktop.ps1"
    content = script.read_text(encoding="utf-8")

    assert '$arguments += "--no-bundle"' in content
    assert "--debug --no-bundle" not in content
    assert "src-tauri\\target\\release\\aitrade-cn.exe" in content


def test_windows_desktop_app_does_not_open_console_window():
    root = Path(__file__).resolve().parents[1]
    main_rs = (root / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
    lib_rs = (root / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")

    assert 'windows_subsystem = "windows"' in main_rs
    assert "CREATE_NO_WINDOW" in lib_rs
    assert ".creation_flags(CREATE_NO_WINDOW)" in lib_rs


def test_windows_bundle_metadata_uses_stable_nsis_target():
    root = Path(__file__).resolve().parents[1]
    tauri_config = json.loads((root / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    cargo_toml = (root / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")

    assert tauri_config["productName"] == "MarketPrism"
    assert tauri_config["bundle"]["targets"] == "nsis"
    assert tauri_config["bundle"]["icon"] == ["icons/icon.ico"]
    assert tauri_config["bundle"]["windows"]["nsis"]["installerIcon"] == "icons/icon.ico"
    assert tauri_config["bundle"]["windows"]["nsis"]["uninstallerIcon"] == "icons/icon.ico"
    assert 'description = "MarketPrism AI stock research workspace"' in cargo_toml


def test_windows_package_bundles_python_backend_sidecar():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "package_windows_desktop.ps1").read_text(encoding="utf-8")
    tauri_config = json.loads((root / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    lib_rs = (root / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")

    assert (root / "scripts" / "desktop_backend_sidecar.py").exists()
    assert "externalBin" not in tauri_config["bundle"]
    assert "PyInstaller" in script
    assert "--onedir" in script
    assert "--onefile" not in script
    assert "--console" in script
    assert "--noconsole" not in script
    assert "--collect-data" in script
    assert '"akshare"' in script
    assert "--collect-binaries" in script
    assert '"py_mini_racer"' in script
    assert "--hidden-import" in script
    assert '"baostock"' in script
    assert "src-tauri\\binaries" in script
    assert "--config" in script
    assert "resources" in script
    assert "binaries/aitrade-backend-" in script
    assert "aitrade-backend.exe" in script
    assert "tauri-sidecar-config.json" in script
    assert "aitrade-backend.exe" in lib_rs
    assert '"aitrade-backend"' in lib_rs
    assert 'option_env!("AITRADE_AUTH_SERVER_URL")' in lib_rs


def test_windows_installer_stops_backend_before_installing_or_uninstalling():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "package_windows_desktop.ps1").read_text(encoding="utf-8")
    tauri_config = json.loads((root / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    hooks_path = root / "src-tauri" / "windows" / "hooks.nsh"
    hooks = hooks_path.read_text(encoding="utf-8")

    assert "function Stop-AITradeBackendProcesses" in script
    assert 'Get-Process -Name "aitrade-backend"' in script
    assert "Stop-Process -Id $process.Id -Force" in script
    assert tauri_config["bundle"]["windows"]["nsis"]["installerHooks"] == "windows/hooks.nsh"
    assert "!macro NSIS_HOOK_PREINSTALL" in hooks
    assert "!macro NSIS_HOOK_PREUNINSTALL" in hooks
    assert "taskkill /F /T /IM aitrade-backend.exe" in hooks


def test_windows_package_never_bundles_local_deploy_config():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "package_windows_desktop.ps1").read_text(encoding="utf-8")
    tauri_config = json.loads((root / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    gitignore = (root / ".gitignore").read_text(encoding="utf-8")

    assert "deploy.local.json" in gitignore
    assert "deploy.local.json" not in json.dumps(tauri_config)
    assert "Copy-Item -LiteralPath $deployConfig" not in script
    tauri_config_lines = [line for line in script.splitlines() if "TauriConfigArgs" in line or "--config" in line]
    assert tauri_config_lines
    assert all("deploy.local.json" not in line and "$deployConfig" not in line for line in tauri_config_lines)
