"""Currency normalization must preserve percentages and dimensionless ratios."""

import copy

import pytest

from agentuniverse.agent.action.knowledge.doc_processor.financial_indicator_extractor import (
    FinancialIndicatorExtractor,
)
from agentuniverse.agent.action.knowledge.store.document import Document


def extract(text, metric_name, normalize=True):
    """Exercise the public processor and check source-document preservation."""
    original = Document(text=text, metadata={"source": "authored-report"})
    before = copy.deepcopy(original.metadata)
    processor = FinancialIndicatorExtractor(
        name="measurement-units",
        metrics=[metric_name],
        extract_temporal=False,
        extract_comparisons=False,
        currency_normalization=normalize,
        use_llm=False,
        skip_on_error=False,
    )
    result = processor.process_docs([original])
    assert len(result) == 1
    assert result[0].id == original.id
    assert result[0].text == text
    assert original.metadata == before
    assert result[0].metadata["original_metadata"] == before
    records = result[0].metadata["financial_metrics"][metric_name]
    assert records
    return records


@pytest.mark.parametrize(
    "metric_name,report,value",
    [
        ("margin", "Margin: 20%", 20),
        ("gross_margin", "Gross margin: 20%", 20),
        ("operating_margin", "Operating margin: 20%", 20),
        ("net_margin", "Net profit margin: 20%", 20),
        ("roi", "ROI: 20%", 20),
        ("roe", "ROE: 20%", 20),
        ("roa", "ROA: 20%", 20),
        ("growth_rate", "Growth rate: 20%", 20),
        ("market_share", "Market share: 20%", 20),
        ("churn_rate", "Churn rate: 20%", 20),
    ],
)
def test_all_percentage_metrics_keep_their_measurement(metric_name, report, value):
    """Each supported percentage family survives neighboring EUR revenue."""
    for record in extract("Revenue: €100 million. " + report, metric_name):
        assert record["value"] == pytest.approx(value)
        assert record["unit"] == "%"
        assert record["currency"] == ""
        assert record["scale"] == "units"


@pytest.mark.parametrize("normalize", [True, False])
@pytest.mark.parametrize("currency", ["€", "£", "¥"])
@pytest.mark.parametrize(
    "metric_name,report,value,unit",
    [
        ("margin", "Margin: 20%", 20, "%"),
        ("debt_ratio", "Debt-to-equity ratio: 1.5", 1.5, "ratio"),
    ],
)
def test_currency_and_normalization_options_do_not_change_nonmonetary_values(
    metric_name, report, value, unit, currency, normalize
):
    """Foreign symbols must not rescale rates or assign monetary metadata."""
    text = report + "; revenue of " + currency + "100 million."
    for record in extract(text, metric_name, normalize):
        assert record["value"] == pytest.approx(value)
        assert record["unit"] == unit
        assert record["currency"] == ""
        assert record["scale"] == "units"


@pytest.mark.parametrize("value", [-12.5, 0, 100])
def test_percentage_points_are_retained_without_currency(value):
    """A percentage is expressed in points, without division or conversion."""
    for record in extract("ROI: " + str(value) + "%", "roi"):
        assert record["value"] == value
        assert record["unit"] == "%"
        assert record["currency"] == ""


@pytest.mark.parametrize(
    "currency,expected",
    [("$", 100_000_000), ("€", 109_000_000), ("£", 127_000_000), ("¥", 14_000_000)],
)
def test_monetary_normalization_keeps_existing_fixed_rates(currency, expected):
    """The correction leaves the existing monetary conversion policy intact."""
    for record in extract("Revenue: " + currency + "100 million.", "revenue"):
        assert record["value"] == pytest.approx(expected)
        assert record["unit"] == "USD"


def test_disabling_normalization_retains_monetary_amount():
    """The existing normalization switch still controls monetary amounts."""
    for record in extract("Revenue: €100 million.", "revenue", normalize=False):
        assert record["value"] == 100_000_000
        assert record["currency"] == "EUR"
