"""Regenerate the LeadBoost documentation diagrams.

    python3 build_diagrams.py           # writes ../*.svg
    python3 build_diagrams.py --check   # also XML-validates every SVG

Hand-maintained generator: edit the dNN_*.py modules, rerun, commit the SVGs.
"""
import importlib, pathlib, sys
from xml.dom import minidom

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE.parent
sys.path.insert(0, str(HERE))

TARGETS = {
    "leadboost-system-context.svg": "d1_context",
    "leadboost-mailer-lifecycle.svg": "d2_lifecycle",
    "leadboost-deployment-topology.svg": "d3_deployment",
    "leadboost-outreach-state-machine.svg": "d4_states",
    "leadboost-end-to-end-pipeline.svg": "d5_pipeline",
}

if __name__ == "__main__":
    for name, mod in TARGETS.items():
        try:
            m = importlib.import_module(mod)
        except ModuleNotFoundError:
            continue
        (OUT / name).write_text(m.build(), encoding="utf-8")
        if "--check" in sys.argv:
            minidom.parse(str(OUT / name))
        print("wrote", name)
