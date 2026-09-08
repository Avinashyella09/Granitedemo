# CENTRALIZED CONFIGURATION FOR THE GRANITE-BLOCK MEASUREMENT POC
# WARNING: All rates, categories, and density values defined here are 
# POC PLACEHOLDERS and do NOT represent official government rules or rates.

# Default density (in metric tonnes per cubic metre)
# This is a placeholder value.
DEFAULT_DENSITY_MT_PER_M3 = 2.7

# Allowed Granite Categories
GRANITE_CATEGORIES = [
    "Premium",
    "Standard",
    "Commercial",
]

# Allowed Gangsaw Classifications
GANGSAW_CLASSIFICATIONS = [
    "Gangsaw Size",
    "Mini Gangsaw Size",
    "Scabos/Other",
]

# POC Seigniorage Rates (in currency units per metric tonne, e.g., INR/MT or USD/MT)
# Maps (Category, Classification) to a numeric rate.
# If a combination is not found, a default rate can be resolved or an error raised.
POC_SEIGNIORAGE_RATES = {
    ("Premium", "Gangsaw Size"): 3000.0,
    ("Premium", "Mini Gangsaw Size"): 2500.0,
    ("Premium", "Scabos/Other"): 2000.0,
    
    ("Standard", "Gangsaw Size"): 2200.0,
    ("Standard", "Mini Gangsaw Size"): 1800.0,
    ("Standard", "Scabos/Other"): 1400.0,
    
    ("Commercial", "Gangsaw Size"): 1500.0,
    ("Commercial", "Mini Gangsaw Size"): 1200.0,
    ("Commercial", "Scabos/Other"): 900.0,
}

DEFAULT_POC_RATE_PER_MT = 1000.0
