import importlib.util as _u
import os as _os

_spec = _u.spec_from_file_location(
    "_tools_sdb_decode", _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "tools", "sdb_decode.py"))
_mod = _u.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
globals().update({k: v for k, v in vars(_mod).items() if not k.startswith("__")})
