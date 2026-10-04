"""Executa o pipeline REAL do core (analyze_capture) com o protótipo de provider.

Não altera casos de uso nem o factory. Não aplica FaceMatchPolicy (threshold
PENDING CALIBRATION): apenas imprime a similaridade.
Executar com o projeto montado em /srv/app (ver README do POC).
"""

import asyncio
import json
import sys
import time
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, "/srv/app")
sys.path.insert(0, str(POC))

from adapter_prototype.opencv_sface_provider import OpenCVSFaceProviderPrototype  # noqa: E402

from app.application.ports import BiometricProvider  # noqa: E402
from app.application.use_cases.capture_analysis import analyze_capture  # noqa: E402
from app.domain.value_objects import CaptureData  # noqa: E402

# Valores SOMENTE para exercitar o fluxo no POC; não são calibração.
POC_PARAMS = {"min_face_px": 40, "min_sharpness": 20.0, "liveness_min_real": 0.5}


def load(path: Path) -> CaptureData:
    return CaptureData(content=path.read_bytes(), content_type="image/jpeg")


async def main() -> None:
    provider: BiometricProvider = OpenCVSFaceProviderPrototype(**POC_PARAMS)
    ds = POC / "dataset"
    manifest = json.loads((ds / "manifest.json").read_text())
    out = []
    for case in [c for c in manifest if c["case"] < 5 and c["scenario"] in
                 ("same_person", "different_person", "blur", "small_face", "multiple_faces", "no_face")]:
        ref, probe = load(POC / case["reference"]), load(POC / case["probe"])
        t0 = time.perf_counter()
        ref_a = await analyze_capture(provider, ref)
        probe_a = await analyze_capture(provider, probe)
        score = None
        if ref_a.embedding and probe_a.embedding:
            score = round(await provider.compare(probe_a.embedding, ref_a.embedding), 4)
        out.append({
            "case": case["case"], "scenario": case["scenario"],
            "reference": (ref_a.rejection or ref_a.error or "OK"),
            "probe": (probe_a.rejection or probe_a.error or "OK"),
            "liveness_score": probe_a.liveness.score if probe_a.liveness else None,
            "embedding_bytes": len(probe_a.embedding.vector) if probe_a.embedding else None,
            "similarity": score,
            "ms_two_captures": round((time.perf_counter() - t0) * 1000, 1),
        })
    (POC / "results" / "provider_fit.json").write_text(json.dumps(out, indent=2))
    for row in out:
        print(row)


if __name__ == "__main__":
    asyncio.run(main())
