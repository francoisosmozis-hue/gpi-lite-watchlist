import subprocess
import sys
from pathlib import Path


PYTHON = sys.executable


def run_step(name, script):
    print("")
    print("=" * 60)
    print(name)
    print("=" * 60)

    path = Path(script)

    if not path.exists():
        print(f"Script absent : {script}")
        return False

    result = subprocess.run([PYTHON, script], text=True)

    if result.returncode != 0:
        print("")
        print(f"ERREUR dans {script}")
        return False

    return True


def main():
    steps = [
        ("1. Watchlist matin", "main.py"),
        ("2. Controle H-5", "h5_main.py"),
        ("3. Extraction resultats", "results_main.py"),
        ("4. Audit securise", "audit_all.py"),
        ("5. Controle qualite", "quality_check.py"),
    ]

    for name, script in steps:
        run_step(name, script)

    print("")
    print("=" * 60)
    print("Pipeline termine")
    print("=" * 60)


if __name__ == "__main__":
    main()