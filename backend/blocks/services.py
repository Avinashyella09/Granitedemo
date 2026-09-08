from .config import (
    POC_SEIGNIORAGE_RATES,
    DEFAULT_POC_RATE_PER_MT,
    DEFAULT_DENSITY_MT_PER_M3,
    GRANITE_CATEGORIES,
    GANGSAW_CLASSIFICATIONS,
)
from .calculations import calculate_volume, calculate_weight

def get_applicable_rate(category, classification):
    """
    Looks up the POC rate for the given category and classification.
    
    Args:
        category (str): Granite category.
        classification (str): Gangsaw classification.
        
    Returns:
        float: POC rate per metric tonne.
    """
    # Normalize inputs to match key matching
    cat_normalized = str(category).strip()
    class_normalized = str(classification).strip()
    
    # Try direct mapping
    rate = POC_SEIGNIORAGE_RATES.get((cat_normalized, class_normalized))
    if rate is not None:
        return float(rate)
        
    # Return default fallback rate if not matched
    return float(DEFAULT_POC_RATE_PER_MT)


def generate_assessment_report(block_id, length_m, breadth_m, height_m, category, classification, density=None):
    """
    Computes volume, weight, applicable rate, and seigniorage fee.
    Generates a dictionary report marked clearly with POC disclaimer.
    
    Args:
        block_id (str): Block identifier.
        length_m (float): Length in metres.
        breadth_m (float): Breadth in metres.
        height_m (float): Height in metres.
        category (str): Granite category.
        classification (str): Gangsaw classification.
        density (float, optional): Custom density value. Defaults to configuration DEFAULT_DENSITY_MT_PER_M3.
        
    Returns:
        dict: Calculation results and metadata.
    """
    if density is None:
        density = DEFAULT_DENSITY_MT_PER_M3
        
    # 1. Deterministic volume
    volume_m3 = calculate_volume(length_m, breadth_m, height_m)
    
    # 2. Deterministic weight
    weight_mt = calculate_weight(volume_m3, density_mt_per_m3=density)
    
    # 3. Retrieve rate
    rate_per_mt = get_applicable_rate(category, classification)
    
    # 4. Calculate seigniorage
    indicative_seigniorage = weight_mt * rate_per_mt
    
    return {
        "block_id": block_id,
        "length_m": float(round(length_m, 3)),
        "breadth_m": float(round(breadth_m, 3)),
        "height_m": float(round(height_m, 3)),
        "volume_m3": float(round(volume_m3, 4)),
        "density_mt_per_m3": float(round(density, 3)),
        "estimated_weight_mt": float(round(weight_mt, 3)),
        "granite_category": category,
        "gangsaw_classification": classification,
        "applicable_rate_per_mt": float(round(rate_per_mt, 2)),
        "indicative_seigniorage": float(round(indicative_seigniorage, 2)),
        "disclaimer": "POC ONLY: This calculation uses proof-of-concept placeholder rates and density values. Official government rates are not applied.",
        "is_official": False
    }
