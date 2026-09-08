import sys
import os

# Append the backend directory so we can import the service
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "backend")))

from blocks.services import generate_assessment_report

def main():
    # Input example parameters
    block_id = "GR-001"
    length_m = 1.54
    breadth_m = 1.60
    height_m = 2.90
    
    # We will use "Premium" and "Gangsaw Size" to lookup a rate for testing
    category = "Premium"
    classification = "Gangsaw Size"
    
    print("=" * 60)
    print("   GRANITE BLOCK MEASUREMENT & SEIGNIORAGE ASSESSMENT DEMO  ")
    print("=" * 60)
    
    # Generate assessment
    result = generate_assessment_report(
        block_id=block_id,
        length_m=length_m,
        breadth_m=breadth_m,
        height_m=height_m,
        category=category,
        classification=classification
    )
    
    # Format and print output
    print(f"Block ID:               {result['block_id']}")
    print(f"Length:                 {result['length_m']} m")
    print(f"Breadth:                {result['breadth_m']} m")
    print(f"Height:                 {result['height_m']} m")
    print(f"Volume (m³):            {result['volume_m3']:.4f} m³")
    print(f"Density:                {result['density_mt_per_m3']} MT/m³ (POC Placeholder)")
    print(f"Estimated Weight (MT):  {result['estimated_weight_mt']:.3f} MT")
    print(f"Category:               {result['granite_category']}")
    print(f"Classification:         {result['gangsaw_classification']}")
    print(f"Applicable POC Rate:    {result['applicable_rate_per_mt']:.2f} per MT (POC Placeholder)")
    print(f"Indicative Seigniorage: {result['indicative_seigniorage']:.2f}")
    print("-" * 60)
    print(f"Disclaimer: {result['disclaimer']}")
    print("=" * 60)

if __name__ == "__main__":
    main()
