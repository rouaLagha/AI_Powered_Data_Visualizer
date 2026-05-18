#!/usr/bin/env python
"""Test KPI structure generation and propagation through the pipeline."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.qlik_to_powerbi.metadata_pipeline import normalize_qlik_to_powerbi_model
from src.qlik_to_powerbi.pbip_generator import _visual_container


def test_kpi_detection_in_normalize():
    """Test that _normalize_visual correctly identifies and structures KPI visuals."""
    
    # Simulate raw Qlik metadata with KPI visual
    metadata = {
        "sheets": [{"id": "sheet_1", "title": "Sheet 1"}],
        "visuals": [
            {
                "id": "kpi_visual_1",
                "title": "Total Sales",
                "type": "kpi",
                "sheet_id": "sheet_1",
                "dimensions": [],
                "measures": [
                    {
                        "label": "Sum of Amount",
                        "expression": "Sum(Amount)",
                    }
                ],
            },
            {
                "id": "bar_visual_1",
                "title": "Sales by Region",
                "type": "barchart",
                "sheet_id": "sheet_1",
                "dimensions": [
                    {"field": "Region", "label": "Region"}
                ],
                "measures": [
                    {
                        "label": "Total Sales",
                        "expression": "Sum(Amount)",
                    }
                ],
            },
        ],
        "dimensions": {},
        "measures": {},
        "filters": [],
        "relationships": [],
    }
    
    # Normalize
    result = normalize_qlik_to_powerbi_model(metadata)
    
    # Verify KPI structure
    visuals = result.get("report", {}).get("visuals", [])
    
    print("\n=== KPI Structure Test Results ===\n")
    
    kpi_visual = None
    bar_visual = None
    
    for visual in visuals:
        if visual.get("id") == "kpi_visual_1":
            kpi_visual = visual
        elif visual.get("id") == "bar_visual_1":
            bar_visual = visual
    
    # Test KPI visual
    if kpi_visual:
        print("✓ KPI Visual Found")
        print(f"  - Title: {kpi_visual.get('title')}")
        print(f"  - Has visual_layout_type: {'visual_layout_type' in kpi_visual}")
        print(f"  - visual_layout_type value: {kpi_visual.get('visual_layout_type')}")
        print(f"  - Has kpi_title: {'kpi_title' in kpi_visual}")
        print(f"  - kpi_title value: {kpi_visual.get('kpi_title')}")
        print(f"  - Dimensions (should be empty): {kpi_visual.get('dimensions')}")
        print(f"  - Measures count: {len(kpi_visual.get('measures', []))}")
        print(f"  - Filters (should be empty): {kpi_visual.get('filters')}")
        
        is_kpi_correct = (
            kpi_visual.get("visual_layout_type") == "kpi_card" and
            kpi_visual.get("kpi_title") == "Total Sales" and
            len(kpi_visual.get("dimensions", [])) == 0 and
            len(kpi_visual.get("filters", [])) == 0 and
            len(kpi_visual.get("measures", [])) > 0
        )
        print(f"\n  Status: {'✓ PASS' if is_kpi_correct else '✗ FAIL'}")
    else:
        print("✗ KPI Visual Not Found")
    
    # Test bar visual (should NOT be KPI)
    if bar_visual:
        print("\n✓ Bar Visual Found")
        print(f"  - Title: {bar_visual.get('title')}")
        print(f"  - Has visual_layout_type: {'visual_layout_type' in bar_visual}")
        print(f"  - visual_layout_type: {bar_visual.get('visual_layout_type', 'None')}")
        print(f"  - Dimensions count: {len(bar_visual.get('dimensions', []))}")
        print(f"  - Measures count: {len(bar_visual.get('measures', []))}")
        
        is_bar_correct = (
            bar_visual.get("visual_layout_type") != "kpi_card" and
            len(bar_visual.get("dimensions", [])) > 0 and
            len(bar_visual.get("measures", [])) > 0
        )
        print(f"\n  Status: {'✓ PASS' if is_bar_correct else '✗ FAIL'}")
    else:
        print("✗ Bar Visual Not Found")


def test_kpi_in_pbip_container():
    """Test that _visual_container correctly handles KPI layout."""
    
    print("\n\n=== PBIP Container Test Results ===\n")
    
    # Test KPI visual container
    kpi_visual = {
        "id": "kpi_1",
        "title": "Revenue",
        "qlik_type": "kpi",
        "visual_layout_type": "kpi_card",
        "kpi_title": "Revenue",
        "dimensions": [],
        "measures": [
            {"name": "total_revenue", "label": "Total Revenue"}
        ],
        "filters": [],
        "powerbi_visual_type_hint": "card",
    }
    
    container = _visual_container(kpi_visual, 0)
    
    print("KPI Container:")
    print(f"  - Has visual_layout_type: {'visual_layout_type' in container}")
    print(f"  - visual_layout_type: {container.get('visual_layout_type')}")
    print(f"  - Has kpi_title: {'kpi_title' in container}")
    print(f"  - kpi_title: {container.get('kpi_title')}")
    print(f"  - Fields dimensions (should be empty): {container.get('fields', {}).get('dimensions')}")
    print(f"  - Fields filters (should be empty): {container.get('fields', {}).get('filters')}")
    print(f"  - Fields measures count: {len(container.get('fields', {}).get('measures', []))}")
    
    is_kpi_container_correct = (
        container.get("visual_layout_type") == "kpi_card" and
        container.get("kpi_title") == "Revenue" and
        container.get("fields", {}).get("dimensions") == [] and
        container.get("fields", {}).get("filters") == [] and
        len(container.get("fields", {}).get("measures", [])) > 0
    )
    print(f"\n  Status: {'✓ PASS' if is_kpi_container_correct else '✗ FAIL'}")


if __name__ == "__main__":
    test_kpi_detection_in_normalize()
    test_kpi_in_pbip_container()
    print("\n" + "="*50)
    print("KPI Structure Tests Complete")
    print("="*50 + "\n")
