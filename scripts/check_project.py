from pathlib import Path
import ast
import py_compile

root = Path(__file__).resolve().parents[1]
app = root / "app.py"

py_compile.compile(str(app), doraise=True)
source = app.read_text(encoding="utf-8")
tree = ast.parse(source)

version = None
classes = set()
has_main = False

for node in tree.body:
    if isinstance(node, ast.ClassDef):
        classes.add(node.name)
    elif isinstance(node, ast.FunctionDef) and node.name == "main":
        has_main = True
    elif isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "APP_VERSION":
                if isinstance(node.value, ast.Constant):
                    version = node.value.value

required = {"MangaEditor", "CanvasView", "ImageItem", "PanelItem"}
missing = sorted(required - classes)
if missing:
    raise SystemExit(f"Missing required classes: {missing}")
if not has_main:
    raise SystemExit("main() not found")
if not version:
    raise SystemExit("APP_VERSION not found")

print(f"APP_VERSION={version}")
print(f"lines={len(source.splitlines())}")
print("compile=OK")
print("ast=OK")
print("required_classes=OK")
