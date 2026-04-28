import importlib
import py_compile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_core_python_files_compile():
    files = [
        "config.py",
        "exo_logger.py",
        "exo_init.py",
        "perception_test.py",
        "gui.py",
        "data/data_analysis.py",
        "calibration/calibration_analysis.py",
        "calibration/calibration_analysis_3.py",
    ]

    for rel_path in files:
        py_compile.compile(str(ROOT / rel_path), doraise=True)


def test_core_modules_import():
    modules = [
        "config",
        "exo_logger",
        "exo_init",
        "perception_test",
        "gui",
        "data.data_analysis",
        "calibration.calibration_analysis",
        "calibration.calibration_analysis_3",
    ]

    for module_name in modules:
        importlib.import_module(module_name)
