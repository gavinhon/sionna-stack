"""Backend selection for Sionna RT: CUDA/OptiX (GPU) with an automatic LLVM (CPU) fallback.

Sionna RT runs on Mitsuba 3, which runs on Dr.Jit. Dr.Jit compiles kernels for one of two backends:

* ``cuda``: NVIDIA GPU via CUDA, with ray/mesh intersection done by **OptiX**. Mitsuba variant
  ``cuda_ad_mono_polarized``.
* ``llvm``: CPU via the LLVM JIT (needs ``libLLVM.so``). Mitsuba variant ``llvm_ad_mono_polarized``.

``import sionna.rt`` picks the first variant that *imports*. Under WSL2 the CUDA variant imports fine
but OptiX initialisation fails later (``/usr/lib/wsl/lib/libnvoptix.so.1`` is a 10 KB stub). So this
module decides the variant itself, before ``sionna.rt`` is imported:

1. Under WSL2, if ``tools/optix_wsl_userlevel.sh`` has put the Linux driver's OptiX files in
   ``~/.local/lib/nvidia-optix-<ver>/``, preload them (``ctypes``, ``RTLD_GLOBAL``) and set
   ``DRJIT_LIBOPTIX_PATH``. This works inside Jupyter kernels too, since no ``LD_LIBRARY_PATH`` is needed.
2. Try the CUDA variant and *prove* OptiX works by building a one-sphere scene.
3. Otherwise fall back to LLVM (unless the CUDA backend was explicitly requested).

Override with ``SIONNA_STACK_BACKEND=auto|cuda|llvm``, or call ``init(prefer=...)``.
Always call ``init()`` **before** ``import sionna.rt``.
"""

from __future__ import annotations

import ctypes
import glob
import os
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from importlib import metadata

CUDA_VARIANT = "cuda_ad_mono_polarized"
LLVM_VARIANT = "llvm_ad_mono_polarized"
OPTIX_USER_DIR_GLOB = os.path.expanduser("~/.local/lib/nvidia-optix-*")
# Load order matters: libnvoptix dlopen()s rtcore, which needs gpucomp.
_OPTIX_COMPANIONS = ["libnvidia-gpucomp.so.*", "libnvidia-ptxjitcompiler.so.1", "libnvidia-rtcore.so.*"]


@dataclass
class BackendInfo:
    variant: str
    backend: str  # "CUDA/OptiX (GPU)" or "LLVM (CPU)"
    is_wsl: bool
    gpu: str | None = None
    driver: str | None = None
    optix_source: str | None = None
    fallback_reason: str | None = None
    versions: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        lines = [
            f"Active backend : {self.backend}",
            f"Mitsuba variant: {self.variant}",
            f"WSL2           : {self.is_wsl}",
        ]
        if self.gpu:
            lines.append(f"GPU / driver   : {self.gpu} / {self.driver}")
        if self.optix_source:
            lines.append(f"OptiX source   : {self.optix_source}")
        if self.fallback_reason:
            lines.append(f"CPU fallback   : {self.fallback_reason}")
        lines.append("Versions       : " + ", ".join(f"{k} {v}" for k, v in self.versions.items()))
        return "\n".join(lines)


_INFO: BackendInfo | None = None


def is_wsl() -> bool:
    return "microsoft" in platform.uname().release.lower()


def _gpu_query() -> tuple[str | None, str | None]:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=20, check=True,
        ).stdout.strip().splitlines()[0]
        name, drv = (s.strip() for s in out.split(","))
        return name, drv
    except Exception:
        return None, None


def _versions() -> dict:
    v = {}
    for pkg in ["sionna", "sionna-rt", "mitsuba", "drjit", "torch"]:
        try:
            v[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            pass
    v["python"] = platform.python_version()
    return v


def find_user_optix_dir() -> str | None:
    """Newest ~/.local/lib/nvidia-optix-<ver> that contains libnvoptix.so.1."""
    dirs = sorted(d for d in glob.glob(OPTIX_USER_DIR_GLOB) if os.path.isfile(os.path.join(d, "libnvoptix.so.1")))
    return dirs[-1] if dirs else None


def _preload_user_optix(d: str) -> None:
    for pat in _OPTIX_COMPANIONS:
        for f in sorted(glob.glob(os.path.join(d, pat))):
            ctypes.CDLL(f, mode=ctypes.RTLD_GLOBAL)
    os.environ["DRJIT_LIBOPTIX_PATH"] = os.path.join(d, "libnvoptix.so.1")


def _optix_smoke_test(mi) -> None:
    """Raises if OptiX cannot build an acceleration structure."""
    mi.load_dict({"type": "scene", "s": {"type": "sphere"}})


def init(prefer: str | None = None, quiet: bool = False) -> BackendInfo:
    """Select the Mitsuba variant. Idempotent: later calls return the first result."""
    global _INFO
    if _INFO is not None:
        return _INFO
    if "sionna.rt" in sys.modules:
        raise RuntimeError("sionna_stack.backend.init() must be called before `import sionna.rt`.")

    prefer = (prefer or os.environ.get("SIONNA_STACK_BACKEND", "auto")).lower()
    if prefer not in {"auto", "cuda", "llvm"}:
        raise ValueError(f"SIONNA_STACK_BACKEND must be auto|cuda|llvm, got {prefer!r}")

    wsl = is_wsl()
    gpu, drv = _gpu_query()
    optix_source = None
    fallback = None

    if prefer in {"auto", "cuda"} and wsl and "DRJIT_LIBOPTIX_PATH" not in os.environ:
        d = find_user_optix_dir()
        if d:
            _preload_user_optix(d)
            optix_source = f"user-level Linux driver files in {d}"
    if optix_source is None and "DRJIT_LIBOPTIX_PATH" in os.environ:
        optix_source = f"DRJIT_LIBOPTIX_PATH={os.environ['DRJIT_LIBOPTIX_PATH']}"
    if optix_source is None and prefer != "llvm":
        optix_source = "system libnvoptix.so.1 (driver default)"

    import mitsuba as mi  # imported only after DRJIT_LIBOPTIX_PATH is set

    variant = None
    if prefer in {"auto", "cuda"}:
        try:
            mi.set_variant(CUDA_VARIANT)
            _optix_smoke_test(mi)
            variant = CUDA_VARIANT
        except Exception as e:  # noqa: BLE001 - we report the reason
            reason = str(e).strip().splitlines()[-1] if str(e).strip() else repr(e)
            if prefer == "cuda":
                raise RuntimeError(
                    f"CUDA/OptiX backend requested but unavailable: {reason}\n"
                    "Under WSL2 run tools/optix_wsl_userlevel.sh (see README, 'GPU / OptiX under WSL2')."
                ) from e
            fallback = f"CUDA/OptiX unavailable ({reason})"
    if variant is None:
        mi.set_variant(LLVM_VARIANT)
        variant = LLVM_VARIANT
        optix_source = None
        if fallback is None:
            fallback = "requested via SIONNA_STACK_BACKEND=llvm" if prefer == "llvm" else None

    _INFO = BackendInfo(
        variant=variant,
        backend="CUDA/OptiX (GPU)" if variant.startswith("cuda") else "LLVM (CPU)",
        is_wsl=wsl,
        gpu=gpu,
        driver=drv,
        optix_source=optix_source,
        fallback_reason=fallback,
        versions=_versions(),
    )
    if not quiet:
        print(_INFO.summary())
    return _INFO


def info() -> BackendInfo:
    if _INFO is None:
        raise RuntimeError("Call sionna_stack.backend.init() first.")
    return _INFO
