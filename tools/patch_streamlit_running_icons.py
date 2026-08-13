"""
Patches Streamlit's installed frontend bundle so the "app is running"
status-widget icon (top-right corner spinner) cycles through
construction-themed icons instead of sport pictograms (accessible-forward
/ accessibility-new / directions-bike / directions-run / pool / rowing).

Unsupported / not officially exposed via any Python or config.toml
option — the icon cycle is hardcoded in Streamlit's compiled React
bundle. This script directly edits the installed package's JS file by
swapping each icon component's SVG path ('d' attribute) for a
hand-authored construction icon, keeping every other detail (component
wrapper, displayName, viewBox, array wiring) untouched — the least
invasive patch possible.

Must be re-run after every `pip install`/upgrade of streamlit in this
venv (a normal package reinstall overwrites the bundle file and silently
reverts this patch). Safe to re-run: no-ops if already patched.
"""
import re
import shutil
import sys

from pathlib import Path

BUNDLE = str(Path(sys.executable).resolve().parent.parent / "Lib" / "site-packages" / "streamlit" / "static" / "static" / "js" / "index.BvGIeCyC.js")

# variable name -> new construction-themed SVG path ('d' attribute, 24x24 viewBox)
NEW_ICONS = {
    "Ku": "M12 4L19 20H5L12 4ZM3 20H21V22H3V20Z",  # traffic cone
    "Gu": "M4 20C4 12.27 7.58 6 12 6C16.42 6 20 12.27 20 20H4ZM2 19H22V21H2V19Z",  # hard hat
    "Yu": "M22.7 19L13.6 9.9C14.5 7.6 14 4.9 12.1 3C10.1 1 7.1 0.6 4.7 1.7L9 6L6 9L1.7 4.7C0.6 7.1 1 10.1 3 12.1C4.9 14 7.6 14.5 9.9 13.6L19 22.7C19.4 23.1 20 23.1 20.4 22.7L22.7 20.4C23.1 20 23.1 19.4 22.7 19Z",  # wrench
    "Xu": "M3 5H21V9H3ZM3 15H21V19H3Z",  # barricade stripes
    "Qu": "M12 4L19 20H5L12 4ZM3 20H21V22H3V20Z",  # traffic cone (repeat)
    "$u": "M4 20C4 12.27 7.58 6 12 6C16.42 6 20 12.27 20 20H4ZM2 19H22V21H2V19Z",  # hard hat (repeat)
}

MARKER = "/* construction-icons-patch-applied */"


def find_component_source(content: str, var_name: str) -> str:
    """Returns the full `var NAME=K.forwardRef(...)...NAME.displayName=`X`;`
    statement for the given minified variable name, located by simple
    index search (robust to exact brace-nesting depth, unlike a regex)."""
    esc = re.escape(var_name)
    start_pattern = re.compile(r"(?<![A-Za-z0-9_$])" + esc + r"=K\.forwardRef\(function\(e,t\)\{")
    m = start_pattern.search(content)
    if not m:
        raise RuntimeError(f"Could not locate start of component {var_name!r} (bundle may have changed).")
    start = m.start()
    disp_marker = f"{var_name}.displayName=`"
    disp_idx = content.find(disp_marker, m.end())
    if disp_idx == -1:
        raise RuntimeError(f"Could not locate displayName assignment for {var_name!r}.")
    end_idx = content.find(";", disp_idx)
    if end_idx == -1:
        raise RuntimeError(f"Could not find statement terminator for {var_name!r}.")
    return content[start:end_idx + 1]


def replace_last_path_d(component_src: str, new_d: str) -> str:
    """Within one component's source, find every `d:\\`...\\`` occurrence and
    replace the LAST one (the actual icon shape; the first is always the
    `M0 0h24v24H0V0z` invisible bounding-box path) with `new_d`."""
    matches = list(re.finditer(r"d:`([^`]*)`", component_src))
    if len(matches) < 2:
        raise RuntimeError("Expected at least 2 `d:` path entries (bounding box + icon shape).")
    last = matches[-1]
    return component_src[: last.start(1)] + new_d + component_src[last.end(1):]


def main() -> int:
    with open(BUNDLE, "r", encoding="utf-8") as f:
        content = f.read()

    if MARKER in content:
        print("Already patched - nothing to do.")
        return 0

    backup_path = BUNDLE + ".pre_construction_icons.bak"
    shutil.copyfile(BUNDLE, backup_path)
    print(f"Backup written to: {backup_path}")

    for var_name, new_d in NEW_ICONS.items():
        old_src = find_component_source(content, var_name)
        new_src = replace_last_path_d(old_src, new_d)
        if content.count(old_src) != 1:
            raise RuntimeError(f"Component source for {var_name!r} is not unique in the bundle - aborting for safety.")
        content = content.replace(old_src, new_src, 1)
        print(f"Patched {var_name} icon.")

    content = content.rstrip() + "\n" + MARKER + "\n"

    with open(BUNDLE, "w", encoding="utf-8") as f:
        f.write(content)

    print("Done. Restart the Streamlit processes to pick up the new bundle (hard-refresh browser too, Ctrl+F5).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
