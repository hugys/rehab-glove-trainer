from .interface import EngineError, FrameResult, ReferenceTrajectory, RehabEngine, SessionParams

__all__ = ["EngineError", "FrameResult", "ReferenceTrajectory", "RehabEngine", "SessionParams",
           "create_engine"]


def create_engine(kind: str, landmark_set, model_path: str | None = None):
    """Factory used by main.py. kind = "mock" | "mediapipe"."""
    if kind == "mock":
        from .mock_engine import MockEngine
        return MockEngine(landmark_set)
    if kind == "mediapipe":
        from .mediapipe_engine import MediaPipeEngine
        return MediaPipeEngine(landmark_set, model_path)
    raise ValueError(f"Unknown engine kind {kind!r}")
