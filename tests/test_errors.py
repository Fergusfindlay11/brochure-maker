"""Tests for the error taxonomy."""

from brochure_maker.map_pipeline.errors import (
    MapPipelineError,
    MapErrorCode,
    DatasetUnavailableError,
    FontMissingError,
    StyleValidationError,
    ExportFailedError,
    VectorPurityError,
)


class TestErrorCodes:
    """Each error code has an actionable message."""

    def test_all_codes_have_remediation(self):
        from brochure_maker.map_pipeline.errors import _REMEDIATION
        for code in MapErrorCode:
            assert code in _REMEDIATION, f"Missing remediation for {code}"

    def test_error_to_dict(self):
        err = DatasetUnavailableError(detail="DB not found")
        d = err.to_dict()
        assert d["error_code"] == "DATASET_UNAVAILABLE"
        assert "DB not found" in d["detail"]
        assert d["remediation"]

    def test_all_subclasses(self):
        errors = [
            DatasetUnavailableError("test"),
            FontMissingError("test"),
            StyleValidationError("test"),
            ExportFailedError("test"),
            VectorPurityError("test"),
        ]
        for err in errors:
            assert isinstance(err, MapPipelineError)
            assert err.code in MapErrorCode.__members__.values()

    def test_error_context(self):
        err = MapPipelineError(
            MapErrorCode.POI_QUERY_TIMEOUT,
            detail="Query too slow",
            context={"elapsed_ms": 5500},
        )
        d = err.to_dict()
        assert d["context"]["elapsed_ms"] == 5500
